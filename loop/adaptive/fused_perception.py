"""Experimental GPA + optional DINO + card-split fusion. Current-frame only."""

from __future__ import annotations

from PIL import Image

from loop.adaptive.card_proposals import propose_hand_cards
from loop.adaptive.gui_yolo import GpaGuiDetector
from loop.adaptive.proposal_fusion import FrameProposal, ProposalFusion
from loop.adaptive.tracker import BBox, TrackHit

_WORLD = {"fused": "tiny", "fused-gpa": "none", "fused-base": "base"}
_HAND_MIN_W = 0.18
_HAND_MIN_H = 0.12
_HAND_CY = 0.55


def _as_proposal(row: dict) -> FrameProposal:
    fid = row.get("frame_id") or 0
    return FrameProposal(
        tuple(float(v) for v in row["bbox"]),  # type: ignore[arg-type]
        str(row.get("role") or "unknown"),
        str(row.get("source") or ""),
        str(row.get("class_hint") or row.get("label") or ""),
        float(row.get("confidence") or row.get("conf") or 0.0),
        fid,
    )


def _split_hands(
    image: Image.Image,
    ui: list[dict],
    ocr: list[tuple[str, BBox]] | None,
    frame_id: str,
    counters: dict | None = None,
) -> list[dict]:
    extra: list[dict] = []
    drop: set[int] = set()
    for i, row in enumerate(ui):
        bbox = tuple(row["bbox"])
        x0, y0, x1, y1 = bbox
        if x1 - x0 < _HAND_MIN_W or y1 - y0 < _HAND_MIN_H or (y0 + y1) * 0.5 < _HAND_CY:
            continue
        cards = propose_hand_cards(image, bbox, ocr, frame_id=frame_id)
        if len(cards) >= 2:
            extra.extend(cards)
        drop.add(i)
        # A hand-wide region is context even when the cards cannot be split.
    if extra:
        for i, row in enumerate(ui):
            if i in drop:
                continue
            bbox = tuple(row["bbox"])
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if area <= 0:
                continue
            for card in extra:
                cb = card["bbox"]
                overlap = max(0.0, min(bbox[2], cb[2]) - max(bbox[0], cb[0])) * max(
                    0.0, min(bbox[3], cb[3]) - max(bbox[1], cb[1])
                )
                if overlap / area >= 0.6:
                    drop.add(i)
                    break
    kept = [row for i, row in enumerate(ui) if i not in drop]
    if counters is not None:
        counters["added"] = len(extra)
        counters["dropped"] = len(ui) - len(kept)
    return kept + extra


class FusedPerceptionTracker:
    require_visual_ownership = True

    def __init__(
        self,
        name: str = "fused",
        *,
        prompts: list[str] | None = None,
        gui=None,
        dino=None,
    ) -> None:
        if name not in _WORLD:
            raise ValueError(f"unknown fused tracker: {name}")
        self.name = name
        self.prompts = list(prompts or ["person", "character", "animal"])
        self.associator = "proposal-fusion"
        self.gui = gui if gui is not None else GpaGuiDetector()
        self.world = _WORLD[name]
        self._dino = dino
        if dino is None and self.world != "none":
            from loop.adaptive.grounding_dino import GroundingDinoTracker

            self._dino = GroundingDinoTracker(self.prompts, variant=self.world)
        self.fusion = ProposalFusion()
        self.last_infer_ms = 0.0
        self.diagnostics: dict = {}
        self._refresh_status()
        if self.status != "ok":
            self._clear_diagnostics("", f"provider_unavailable:{self.status}")

    @staticmethod
    def _device_label(provider) -> str | None:
        if provider is None:
            return None
        raw = getattr(provider, "device", None)
        if raw is None:
            raw = getattr(provider, "_device", None)
        return str(raw) if raw is not None else None

    def _provider_status(self) -> dict:
        gui = self.gui
        world = self._dino
        return {
            "gui": {
                "status": str(getattr(gui, "status", "unavailable")),
                "backend": getattr(gui, "backend", None),
                "device": self._device_label(gui),
                "fallback": getattr(gui, "fallback", None),
                "ms": float(getattr(gui, "last_infer_ms", 0.0) or 0.0),
            },
            "world": {
                "status": (
                    str(getattr(world, "status", "unavailable"))
                    if world is not None
                    else "none"
                ),
                "backend": (
                    str(getattr(world, "source", "")) or None
                    if world is not None
                    else None
                ),
                "device": self._device_label(world),
                # The world provider does not report a fallback path.
                "fallback": None,
                "ms": (
                    float(getattr(world, "last_infer_ms", 0.0) or 0.0)
                    if world is not None
                    else 0.0
                ),
            },
        }

    def _clear_diagnostics(self, frame_id: str, reason: str) -> None:
        """No measurement for this frame: counts stay unavailable, never zero."""
        self.diagnostics = {
            "frame_id": frame_id,
            "available": False,
            "reason": reason,
            "raw": {"gui": None, "world": None},
            "accepted": {"gui": None, "world": None},
            "rejected": {"gui": None, "world": None, "fusion": None},
            "merged": None,
            "tracks": None,
            "sources": None,
            "split": {"added": None, "dropped": None},
            "providers": self._provider_status(),
        }

    @property
    def load_ms(self) -> float:
        return float(getattr(self.gui, "load_ms", 0.0)) + float(
            getattr(self._dino, "load_ms", 0.0) if self._dino is not None else 0.0
        )

    def _refresh_status(self) -> None:
        gui_ok = str(getattr(self.gui, "status", "unavailable")) == "ok"
        if not gui_ok:
            self.status = str(getattr(self.gui, "status", "unavailable"))
            return
        if self.world != "none":
            dino_status = str(getattr(self._dino, "status", "unavailable")) if self._dino else "unavailable"
            if dino_status != "ok":
                self.status = dino_status
                return
        self.status = "ok"

    def clear_live(self) -> None:
        self.fusion.reset()

    def reset(self) -> None:
        self.fusion.reset()

    def update(
        self,
        image: Image.Image,
        ocr: list[tuple[str, BBox]] | None = None,
        *,
        frame_id: str = "",
    ) -> tuple[TrackHit, ...]:
        self._refresh_status()
        if self.status != "ok":
            self._clear_diagnostics(frame_id, f"provider_unavailable:{self.status}")
            return ()
        ui = self.gui.propose(image, frame_id=frame_id)
        split = {"added": 0, "dropped": 0}
        ui = _split_hands(image, ui, ocr, frame_id, counters=split)
        world: list[dict] = []
        if self._dino is not None:
            world = self._dino.propose(image, frame_id=frame_id)
            self._refresh_status()
            if self.status != "ok":
                self._clear_diagnostics(frame_id, f"provider_unavailable:{self.status}")
                return ()
        hits = self.fusion.fuse(image, [_as_proposal(row) for row in ui + world], ocr=ocr)
        diag = self.fusion.diagnostics
        gui_diag = getattr(self.gui, "diagnostics", None) or {}
        world_diag = (
            getattr(self._dino, "diagnostics", None) if self._dino is not None else None
        )
        sources: dict[str, int] = {}
        for row in ui + world:
            key = str(row.get("source") or "unknown")
            sources[key] = sources.get(key, 0) + 1
        providers = self._provider_status()
        self.diagnostics = {
            "frame_id": frame_id,
            "available": True,
            "reason": None,
            "raw": {"gui": gui_diag.get("raw"), "world": (world_diag or {}).get("raw")},
            "accepted": {
                "gui": gui_diag.get("accepted"),
                "world": (world_diag or {}).get("accepted"),
            },
            "rejected": {
                "gui": gui_diag.get("rejected"),
                "world": dict(world_diag) if world_diag is not None else None,
                "fusion": dict(diag.rejections),
            },
            "merged": int(diag.merges),
            "tracks": len(hits),
            "sources": sources,
            "split": dict(split),
            "providers": providers,
        }
        self.last_infer_ms = float(providers["gui"]["ms"]) + float(
            providers["world"]["ms"]
        )
        return hits
