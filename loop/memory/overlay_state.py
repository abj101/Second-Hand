from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OverlayDismissal:
    title: str
    dismissed_at_step: int
    suppress_ocr_steps: int = 3


@dataclass
class OverlayState:
    """Per-episode overlay dismissal memory for OCR/perceive reconciliation."""

    dismissals: dict[str, OverlayDismissal] = field(default_factory=dict)
    screen_disagreements: list[dict[str, Any]] = field(default_factory=list)

    def record_dismissal(
        self,
        *,
        title: str,
        step_id: int,
        suppress_ocr_steps: int = 3,
    ) -> None:
        key = (title or "overlay").strip().lower()
        if not key:
            return
        self.dismissals[key] = OverlayDismissal(
            title=title,
            dismissed_at_step=int(step_id),
            suppress_ocr_steps=int(suppress_ocr_steps),
        )

    def should_suppress_ocr(self, *, text: str, current_step: int) -> bool:
        blob = (text or "").strip().lower()
        if not blob:
            return False
        for dismissal in self.dismissals.values():
            if current_step - dismissal.dismissed_at_step > dismissal.suppress_ocr_steps:
                continue
            title = dismissal.title.strip().lower()
            if title and title in blob:
                return True
            if "energy" in blob and "tutorial" in title:
                return True
        return False

    def log_screen_disagreement(
        self,
        *,
        host_screen: str,
        perceive_screen: str,
        step_id: int,
    ) -> None:
        if not host_screen or not perceive_screen:
            return
        if host_screen.strip().lower() == perceive_screen.strip().lower():
            return
        self.screen_disagreements.append(
            {
                "step": int(step_id),
                "host": host_screen.strip().lower(),
                "perceive": perceive_screen.strip().lower(),
            }
        )

    def resolved_screen(self, *, host_screen: str, perceive_screen: str) -> str:
        host = (host_screen or "").strip().lower()
        perceive = (perceive_screen or "").strip().lower()
        if host and host not in {"", "other"}:
            return host
        return perceive or host or "other"


@dataclass
class ControlSuppressionState:
    """Track twice-blocked controls per unchanged typed action surface."""

    fingerprint: str = ""
    attempts: dict[str, int] = field(default_factory=dict)
    suppressed: set[str] = field(default_factory=set)
    last_log_reason: str = ""

    def reset(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint
        self.attempts.clear()
        self.suppressed.clear()
        self.last_log_reason = ""

    def maybe_reset(
        self,
        fingerprint: str,
        *,
        membership_changed: bool = False,
        object_moved: bool = False,
    ) -> None:
        if (
            fingerprint != self.fingerprint
            or membership_changed
            or object_moved
        ):
            self.reset(fingerprint)

    def record_no_change(self, fingerprint: str, control_key: str) -> int:
        if fingerprint != self.fingerprint:
            self.reset(fingerprint)
        count = int(self.attempts.get(control_key, 0)) + 1
        self.attempts[control_key] = count
        if count >= 2:
            self.suppressed.add(control_key)
            self.last_log_reason = "suppressed:blocked-twice"
        return count

    def is_suppressed(self, control_key: str) -> bool:
        return control_key in self.suppressed

    def clear_key(self, control_key: str) -> None:
        self.attempts.pop(control_key, None)
        self.suppressed.discard(control_key)
