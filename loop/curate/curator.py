from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loop.config import project_root
from loop.curate.critic import commit_allowed, cluster_failures, failure_signature
from loop.curate.synth import deterministic_skill, last_after_metrics, vlm_synth
from loop.fixtures.elden import load_hud_spec
from loop.log.schema import EpisodeLog, StepLog
from loop.log.store import EpisodeStore, latest_run_dir
from loop.memory.skills import EvidenceRow, SkillBank
from loop.model.gateway import VlmGateway
from loop.os.capture import encode_image
from PIL import Image


@dataclass
class CurateReport:
    preview: bool
    recorded: int = 0
    retired: list[str] | None = None
    born: list[str] | None = None
    refused: list[str] | None = None
    rows: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.retired = self.retired or []
        self.born = self.born or []
        self.refused = self.refused or []
        self.rows = self.rows or []


def librarian_spec(fixture: str) -> dict[str, Any]:
    if fixture == "elden":
        hud = load_hud_spec()
        spec = hud.get("librarian") if isinstance(hud.get("librarian"), dict) else {}
        return {
            "n_min": int(spec.get("n_min") or 20),
            "tau": float(spec.get("tau") or 0.10),
            "cap": int(spec.get("cap") or 8),
        }
    return {"n_min": 20, "tau": 0.10, "cap": 8}


def skill_bank_path(fixture: str, root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "loop" / fixture / "skills.json"


def perceive_report_path(fixture: str, root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "loop" / fixture / "perceive" / "report.json"


def load_run(run_dir: Path) -> tuple[list[EpisodeLog], dict[str, list[StepLog]]]:
    store = EpisodeStore(run_dir)
    episodes = [EpisodeLog.model_validate(row) for row in store.read_episodes()]
    steps_by: dict[str, list[StepLog]] = {}
    for row in store.read_steps():
        step = StepLog.model_validate(row)
        steps_by.setdefault(step.episode_id, []).append(step)
    for steps in steps_by.values():
        steps.sort(key=lambda s: s.step_id)
    return episodes, steps_by


class Curator:
    def __init__(
        self,
        fixture: str,
        *,
        run_dir: Path | None = None,
        bank: SkillBank | None = None,
        gateway: VlmGateway | None = None,
        skip_models: bool = True,
        root: Path | None = None,
    ) -> None:
        self.fixture = fixture
        self.root = root or project_root()
        self.run_dir = run_dir or latest_run_dir(fixture, self.root)
        spec = librarian_spec(fixture)
        self.bank = bank or SkillBank(
            skill_bank_path(fixture, self.root),
            n_min=spec["n_min"],
            tau=spec["tau"],
            cap=spec["cap"],
        )
        self.gateway = gateway
        self.skip_models = skip_models

    def preview_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for skill in self.bank.skills.values():
            rows.append(
                {
                    "skill_id": skill.id,
                    "n_trials": skill.n_trials,
                    "contribution": round(skill.contribution, 4),
                    "status": skill.status,
                    "would_retire": self.bank.would_retire(skill),
                }
            )
        return rows

    def run(self, *, preview: bool = False) -> CurateReport:
        report = CurateReport(preview=preview)
        if self.run_dir is None:
            report.rows = self.preview_rows()
            return report
        snapshot = {k: v.model_copy(deep=True) for k, v in self.bank.skills.items()}
        episodes, steps_by = load_run(self.run_dir)
        seen = self.bank.seen_episode_ids()
        new_eps = [ep for ep in episodes if ep.episode_id not in seen]
        for episode in new_eps:
            # Interrupted and timeout episodes carry no win/loss evidence and
            # must not penalize a skill or seed a failure cluster.
            if episode.outcome == "inconclusive":
                continue
            steps = steps_by.get(episode.episode_id) or []
            sig = failure_signature(episode, steps)
            if episode.skill_on and episode.active_skill_id:
                self.bank.record_trial(episode.active_skill_id, success=episode.success)
            evidence = EvidenceRow(
                episode_id=episode.episode_id,
                skill_id=episode.active_skill_id,
                skill_on=episode.skill_on,
                success=episode.success,
                signature=sig,
                run_dir=str(self.run_dir),
                arm=episode.arm,
            )
            if not preview:
                self.bank.append_evidence(evidence)
            report.recorded += 1
        retired = self.bank.retire_due()
        report.retired = [s.id for s in retired]
        clusters = cluster_failures(episodes, steps_by)
        owned = self.bank.signatures_owned()
        for signature, group in clusters.items():
            if len(group) < 3:
                continue
            if signature in owned:
                continue
            skill = self._synth(signature, group[0], steps_by.get(group[0].episode_id) or [])
            chord = (skill.avoid_or_do or {}).get("chord") if isinstance(skill.avoid_or_do, dict) else {}
            sample_steps: list[StepLog] = []
            for ep in group:
                sample_steps.extend(steps_by.get(ep.episode_id) or [])
            if not commit_allowed(chord if isinstance(chord, dict) else {}, sample_steps):
                report.refused.append(signature)
                continue
            added = self.bank.add_skill(skill)
            if added is None:
                report.refused.append(signature)
            else:
                report.born.append(added.id)
                owned.add(signature)
        report.rows = self.preview_rows()
        if preview:
            self.bank.skills = snapshot
        else:
            self.bank.save()
        return report

    def _synth(self, signature: str, episode: EpisodeLog, steps: list[StepLog]) -> Any:
        if self.skip_models or self.gateway is None:
            return deterministic_skill(signature)
        frame = None
        if steps and steps[-1].observation.after_screenshot_ref and self.run_dir is not None:
            path = self.run_dir / steps[-1].observation.after_screenshot_ref
            if path.exists():
                image = Image.open(path).convert("RGB")
                frame = encode_image(image, longest_side=768, jpeg_quality=85)
        return vlm_synth(
            self.gateway,
            signature=signature,
            metrics=last_after_metrics(steps),
            flags=episode.audit_flags,
            frame=frame,
        )
