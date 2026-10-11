"""One game-neutral perception, choice, action, and learning cycle."""
import hashlib, json, time
from agent.brief import GENERAL, defaults, load_brief
from agent.decide import choose, escalate
from agent.observer import cut, for_model, wait_settled
from agent.perceive import perceive
from agent.predict import PRED, effects, predict, verdict
from agent.skills import Ctx, run_affordance, wait_until
from agent.tasks import Stuck, Tasks
from agent.trace import Trace
class Loop:
    def __init__(self, env, model, mem, game):
        self.env, self.model, self.mem, self.game = env, model, mem, game
        self.trace = Trace(mem.root.parent / "trace.jsonl"); self.step_no = 0
        self.place = None; self.prev = None; self.tile_px = getattr(env, "tile_px", None)
        self.move_frames = 16; self.stuck = Stuck(); self.tasks = Tasks(mem)
        self.recent = self.trace.tail(5); self.cached = None; self.cached_hash = None; self.prev_frame = None; self.prompt_version = "e3-perceiver-5"; self.cached_version = self.prompt_version
        self.pending_cut, self.pending_via, self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = False, None, False, 0, False, False
    def _see(self, frame, previous):
        key = hashlib.sha256(frame.tobytes()).hexdigest()
        if key == self.cached_hash and self.cached is not None and self.cached_version == self.prompt_version and not self.repair_pending: return self.cached, key, True, []
        ctx = GENERAL + "\n" + load_brief(self.game) + "\nOn a visible editable text field with its on-screen keyboard, expose one type affordance for the whole field; dialogue questions alone are advance, preset name menus are select. Never list letter buttons separately. A visible modal dialogue or selection menu blocks world movement; offer only its applicable advance, select, or cancel actions until it closes. Previous-place memory is reference only; include details only if visible now." + ("\nThe previous JSON was empty or malformed. Retry with compact JSON containing string screen and list text, entities, affordances; no prose. Use empty lists only for genuinely absent content and report visible details only." if self.repair_pending else "")
        view, ids = self.mem.view("perceiver", {"place": previous, "task": None, "on_screen": [], "recent": [], "step": self.step_no})
        if view: ctx += "\n" + view
        per, reply = perceive(self.model, for_model(frame), ctx); data = reply.data
        valid = isinstance(data, dict) and isinstance(data.get("screen"), str) and isinstance(data.get("text"), (list, str)) and all(isinstance(data.get(k), list) for k in ("entities", "affordances"))
        self.cached, self.cached_hash, self.cached_version = per, key, self.prompt_version; self.repair_pending = False; self.parse_failed = not valid or not any((per["screen"], per["text"], per["entities"], per["affordances"]))
        return per, key, False, ids
    def step(self):
        start, calls = time.perf_counter(), self.model.calls
        before_set = wait_settled(self.env); before = before_set.frame; prior = self.place
        per, frame_hash, reused, _ = self._see(before, prior); self.empty_streak = self.empty_streak + 1 if self.parse_failed else 0; self.repair_used = self.repair_used if self.parse_failed else False
        via = next((a.get("target") for a in per.get("affordances", []) if a.get("verb") == "approach"), None)
        modal = self.prev and {self.prev.get("screen"), per.get("screen")} <= {"world", "dialogue", "menu"} and self.prev.get("screen") != per.get("screen") and "world" in (self.prev.get("screen"), per.get("screen")) and not (self.pending_cut or before_set.blank_seen)
        place = self.mem.match_place(per, self.step_no + 1, prev=prior, cut=bool((self.pending_cut or prior and cut(self.prev_frame, before, before_set.blank_seen)) and not modal), via=self.pending_via or via)
        self.place, self.pending_cut, self.pending_via = place, False, None
        ids = self.mem.observe(per, place, self.step_no + 1)
        for text in per.get("text", []):
            if text not in (self.prev or {}).get("text", []): self.mem.know(text, src="game_text", step=self.step_no + 1)
        task = self.tasks.active(); self.tasks.current_place, self.tasks.perception, self.tasks.settled = place, per, before_set.settled
        changed_tasks = self.tasks.check(self.step_no + 1); task = self.tasks.active()
        on_screen = [ids[i] for i in ids if i < len(per.get("entities", []))]
        ctx = {"place": place, "task": task["id"], "on_screen": on_screen, "recent": self.recent, "step": self.step_no + 1}
        view, view_ids = self.mem.view("chooser", ctx)
        affs = per.get("affordances", []); targets = [self.mem.resolve(a.get("target", ""), place) for a in affs]
        preds = [predict(self.mem, a, targets[i], place) for i, a in enumerate(affs)]
        choice = choose(self.model, view, per, task, preds, defaults(self.game)) if affs else None
        if choice and choice.called:
            row = {"step": self.step_no + 1, "view": view, "affordances": affs, "task": task, "preds": preds,
                   "choice": choice.__dict__, "view_ids": view_ids, "recent": [r for r in self.recent if r.get("place")==place and r.get("frame_hash")==frame_hash], "place": place,
                    "frame_hash": frame_hash, "frame": f"frames/{self.step_no+1:06d}.png"}
            with (self.mem.root.parent / "chooser.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps(row, default=str) + "\n")
        unknown = bool(choice and affs[choice.i].get("verb") in ("approach", "interact") and not self.env.pointer and
                       not any(e.get("kind", "").casefold() == "player" for e in per.get("entities", [])))
        skillctx = Ctx(self.env, self.model, self.tile_px, self.move_frames)
        try: skill = wait_until(skillctx) if not affs else run_affordance(skillctx, affs[choice.i], per, text=choice.text)
        except BaseException: self.pending_cut, self.pending_via = cut(before, self.env.screenshot()), affs[choice.i].get("target") if choice else None; self.cached = self.cached_hash = None; raise
        self.tile_px = skillctx.tile_px
        scene_cut = skill.blank_seen or skill.reason == "scene cut" or (cut(skill.before, skill.after) and (self.env.pointer or not choice or affs[choice.i].get("verb") not in ("approach", "interact") or skill.scroll == (0, 0)))
        self.pending_cut, self.pending_via = scene_cut, affs[choice.i].get("target") if choice else None
        try: after_set = wait_settled(self.env); after = after_set.frame; after_per, after_hash, _, _ = self._see(after, place)
        except BaseException: self.cached = self.cached_hash = None; raise
        modal = {per.get("screen"), after_per.get("screen")} <= {"world", "dialogue", "menu"} and per.get("screen") != after_per.get("screen") and "world" in (per.get("screen"), after_per.get("screen")) and not (skill.blank_seen or skill.reason == "scene cut")
        scene_cut &= not modal
        next_place = place if modal else self.mem.match_place(after_per, self.step_no + 1, prev=place, cut=scene_cut, via=(affs[choice.i].get("target") if choice else None))
        if skill.scroll != (0, 0) and next_place == place and self.mem.get("places", place).get("state") == "candidate":
            source = self.mem.get("places", place); source["state"] = "place"; self.mem.upsert("places", source)
        outcome = effects(per, after_per, skill, place, next_place, place_after_candidate=bool(self.mem.get("places", next_place).get("state") == "candidate"), scene_cut=scene_cut)
        pred = (preds[choice.i] or {"effect": choice.effect, "detail": choice.detail, "basis": "guess"}) if choice else None
        if choice and str(affs[choice.i].get("target", "")).startswith("exit"):
            pred = {**pred, "effect": "place_changes", "detail": pred.get("detail") if pred.get("effect") == "place_changes" else None}
        v = "unobservable" if not affs else verdict(pred, outcome, blocked=skill.status == "blocked")
        if choice and targets[choice.i]:
            for effect in outcome["effects"]:
                detail = outcome.get("to") if effect == "place_changes" else choice.detail or effect
                self.mem.relate(targets[choice.i], PRED[effect], str(detail or ""),
                    src=outcome["basis"].get(effect, "model"), step=self.step_no + 1,
                    hit=True if outcome["basis"].get(effect) == "pixel" else None)
            if pred and pred["effect"] not in outcome["effects"]:
                self.mem.relate(targets[choice.i], PRED[pred["effect"]], str(pred.get("detail") or ""),
                                src="observed", step=self.step_no+1, hit=False)
        edge_rows = self.mem.get("places", place).get("edges", [])
        verified_edge = bool(choice and scene_cut and next_place != place and any(outcome["basis"].get(e) == "pixel" for e in ("place_changes", "screen_changes")) and
            not any((e.get("verb"), e.get("via"), e.get("to")) == (affs[choice.i]["verb"], affs[choice.i].get("target", ""), next_place) and e.get("ok") for e in edge_rows))
        if choice and scene_cut:
            self.mem.add_edge(place, affs[choice.i]["verb"], affs[choice.i].get("target", ""), next_place, True)
        row = {"step": self.step_no + 1, "t": time.time(), "place": place, "screen": per.get("screen"),
            "task": task["id"], "frame": f"frames/{self.step_no+1:06d}.png", "options": len(affs),
            "choice": ({"i": choice.i, **affs[choice.i], "id": targets[choice.i], "called": choice.called, "text": choice.text, "why": choice.why} if choice else None),
            "pred": pred, "used": choice.used if choice else [], "skill": {"status": skill.status, "reason": skill.reason, "inputs": skill.inputs, "calls": skill.calls},
            "outcome": outcome, "verdict": v, "calls": self.model.calls-calls, "ms": int((time.perf_counter()-start)*1000), "shortcut": None,
            "perception_reused": reused, "frame_hash": frame_hash,
            "scene_cut": scene_cut, "next_place": next_place, "verified_edge": verified_edge, "view_ids": view_ids,
            "task_budget_exhausted": any(t.get("last_failure") == "budget exhausted" for t in changed_tasks)}
        self.step_no += 1; self.trace.write(row); self.recent = [*self.recent, row][-5:]
        (self.mem.root.parent / row["frame"]).parent.mkdir(parents=True, exist_ok=True); before.save(self.mem.root.parent / row["frame"])
        self.mem.observe(after_per, next_place, self.step_no)
        for text in after_per.get("text", []):
            if text not in per.get("text", []): self.mem.know(text, src="game_text", step=self.step_no)
        self.place, self.prev, self.prev_frame, self.cached, self.cached_hash = next_place, after_per, after, after_per, after_hash
        self.pending_cut, self.pending_via = False, None; reason = self.stuck.update(row); self.repair_pending = self.parse_failed and self.empty_streak >= 8 and not self.repair_used; self.repair_used |= self.repair_pending
        if unknown: reason = "player position unknown"
        if reason:
            self.stuck.n = self.stuck.same_action = 0
            for sub in escalate(self.model, view, reason): self.tasks.add(**sub, parent=task["id"])
        return row
    def state(self):
        task = next((r.get("id") for r in reversed(self.mem.rows("tasks")) if r.get("status")=="active"),None)
        return {"step": self.step_no, "place": self.place, "tile_px": self.tile_px, "move_frames": self.move_frames, "stuck": self.stuck.state(), "perception": self.cached, "frame_hash": self.cached_hash, "perception_version": self.cached_version, "parse_failed": self.parse_failed, "empty_streak": self.empty_streak, "repair_used": self.repair_used, "repair_pending": self.repair_pending, "pending_cut": self.pending_cut, "pending_via": self.pending_via, "task": task}
    def restore(self, state):
        self.step_no, self.place = int(state.get("step", 0)), state.get("place")
        self.tile_px, self.move_frames = state.get("tile_px"), int(state.get("move_frames", 16))
        self.stuck.restore(state.get("stuck", {})); self.prev = state.get("perception"); self.prev_frame = self.env.screenshot()
        digest = hashlib.sha256(self.prev_frame.tobytes()).hexdigest()
        self.cached, self.cached_hash, self.cached_version = state.get("perception"), state.get("frame_hash"), state.get("perception_version")
        self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = bool(state.get("parse_failed")), int(state.get("empty_streak", 0)), bool(state.get("repair_used")), bool(state.get("repair_pending")); self.pending_cut, self.pending_via = bool(state.get("pending_cut")), state.get("pending_via")
        if self.cached_version != self.prompt_version or digest != self.cached_hash: self.cached = self.cached_hash = None
