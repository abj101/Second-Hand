"""One game-neutral perception, choice, action, and learning cycle."""
import hashlib, json, os, time
from agent.act import act
from agent.brief import GENERAL, _load, defaults, load_brief
from agent.decide import Choice
from agent.memory import _key
from agent.notebook import FINISHED, Notebook
from agent.observer import cut, for_model, wait_settled
from agent.perceive import perceive
from agent.plan import plan
from agent.predict import PRED, effects, predict, verdict
from agent.skills import Ctx, press_seq, run_affordance, wait_until
from agent.tasks import Stuck
from agent.trace import Trace
from agent.space import DIRS, frontier, outward, route
from agent.world import World, fail_key
CAPTION = 'The sprite inside the red box is the player\'s own character (you); list it with kind "player".'
class Loop:
    def __init__(self, env, model, mem, game, act_text=False):
        self.act_text = act_text  # act sees description + spatial layer only, no pixels
        self.env, self.model, self.mem, self.game = env, model, mem, game
        self.notebook, self.flags = Notebook(mem.root), []
        self.trace = Trace(mem.root.parent / "trace.jsonl"); self.step_no = 0
        self.place = None; self.prev = None; self.tile_px = getattr(env, "tile_px", None)
        self.move_frames = 16; self.stuck = Stuck()
        self.recent = self.trace.tail(5); self.cached = None; self.cached_hash = None; self.cached_marked = False; self.prev_frame = None; self.prev_frame_mark_eligible = None; self.prompt_version = "e5-perceiver-9"; self.cached_version = self.prompt_version
        self.seen_image, self.seen_marked = None, False
        self.pending_cut, self.pending_via, self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = False, None, False, 0, False, False
        self.reperception_used = False
        self.world = World(mem, self.tile_px)
        self.last_plan, self.text_pending, self.focus_closed, self._plan_row = None, False, None, None
        self.fails, self.moves, self.urgent = {}, [], None  # per-place failed actions, recent place moves, a replan reason that skips the throttle
    @property
    def pos(self): return self.world.pos
    @pos.setter
    def pos(self, value): self.world.pos = tuple(value)
    def _see(self, frame, previous, *, force=False, relook=False, screen_was_world=False):
        key = hashlib.sha256(frame.tobytes()).hexdigest()
        image, marked = self.world.mark(frame, screen_was_world)
        self.seen_image, self.seen_marked = image, marked
        if not force and key == self.cached_hash and marked == self.cached_marked and self.cached is not None and self.cached_version == self.prompt_version and not self.repair_pending: return self.cached, key, True, []
        ctx = GENERAL + "\n" + load_brief(self.game) + ("\nThe previous JSON was empty or malformed. Retry with compact JSON containing string screen and list text and entities; no prose. Use empty lists only for genuinely absent content and report visible details only." if self.repair_pending else "")
        if marked: ctx += "\n" + CAPTION
        if relook: ctx += "\nYour last labels may be wrong; look again at who the player is."
        per, reply = perceive(self.model, for_model(image), ctx); data = reply.data
        valid = isinstance(data, dict) and isinstance(data.get("screen"), str) and isinstance(data.get("text"), (list, str)) and isinstance(data.get("entities"), list)
        self.cached, self.cached_hash, self.cached_marked, self.cached_version = per, key, marked, self.prompt_version; self.repair_pending = False; self.parse_failed = not valid or not any((per["screen"], per["text"], per["entities"]))
        return per, key, False, []
    def step(self):
        start, calls = time.perf_counter(), self.model.calls
        before_set = wait_settled(self.env); before = before_set.frame; prior = self.place;
        if self.stuck.n < 2: self.reperception_used = False
        relook = self.stuck.n >= 2 and not self.reperception_used
        before_mark = bool(self.prev and self.prev.get("screen") == "world" and self.prev_frame is not None and not cut(self.prev_frame, before, before_set.blank_seen))
        if self.prev_frame is not None and hashlib.sha256(before.tobytes()).hexdigest() == hashlib.sha256(self.prev_frame.tobytes()).hexdigest() and self.prev_frame_mark_eligible is not None:
            before_mark = self.prev_frame_mark_eligible
        per, frame_hash, reused, _ = self._see(before, prior, force=relook, relook=relook, screen_was_world=before_mark); model_frame, marked = self.seen_image, self.seen_marked
        if relook: self.reperception_used = True
        player_fix = self.world.fix_player(per, before.size); self.empty_streak = self.empty_streak + 1 if self.parse_failed else 0; self.repair_used = self.repair_used if self.parse_failed else False
        modal = self.prev and {self.prev.get("screen"), per.get("screen")} <= {"world", "dialogue", "menu"} and self.prev.get("screen") != per.get("screen") and "world" in (self.prev.get("screen"), per.get("screen")) and not (self.pending_cut or before_set.blank_seen)
        place = self.mem.match_place(per, self.step_no + 1, prev=prior, cut=bool((self.pending_cut or prior and cut(self.prev_frame, before, before_set.blank_seen)) and not modal), via=self.pending_via, frame=before, tile_px=self.tile_px)
        if prior and place != prior: self.world.unseen_cross(prior, place, self.step_no + 1); self._moved(prior, place, self.pending_via, self.step_no + 1)
        self.place, self.pending_cut, self.pending_via = place, False, None
        size = before.size
        proj = self.world.before(per, place, prior, size, self.step_no + 1)[0]
        drift_fix = None
        probe = None
        if per.get("screen") == "world" and not modal and not self.world._self_from_motion and self.tile_px:
            probe_ctx = Ctx(self.env, self.model, self.tile_px, self.move_frames); probe_ctx.step = self.step_no + 1
            probe = self.world.probe(probe_ctx)
            if probe["presses"]:
                probe_set = wait_settled(self.env); probe_frame = probe_set.frame
                probe_per, probe_hash, probe_reused, _ = self._see(probe_frame, place, force=True, screen_was_world=per.get("screen") == "world" and not probe["scene_cut"]); model_frame, marked = self.seen_image, self.seen_marked
                probe_fix = self.world.fix_player(probe_per, probe_frame.size); player_fix = player_fix or probe_fix
                if probe["scene_cut"]:
                    probe_place = self.mem.match_place(probe_per, self.step_no + 1, prev=place, cut=True, frame=probe_frame, tile_px=self.tile_px)
                    self.world.after(probe["skill"], place, probe_place, True, self.step_no + 1)
                    if probe_place != place: self._moved(place, probe_place, "a step taken to locate yourself", self.step_no + 1)
                    place = self.place = probe_place
                per, frame_hash, reused = probe_per, probe_hash, probe_reused
                before, size = probe_frame, probe_frame.size
                proj = self.world.before(per, place, place, size, self.step_no + 1)[0]
        self._learn(per.get("text", []), (self.prev or {}).get("text", []), self.step_no + 1, place)
        self.world.view_flags = self.flags
        view = self.world.view(self._goal_text(), place)
        self._plan_row = None
        if (trigger := self._plan_trigger(per)):
            p = plan(self.model, trigger, self.notebook, view, per.get("text", []), [f"{(r.get('choice') or {}).get('verb')} {(r.get('choice') or {}).get('target') or ''} -> {r.get('verdict')}" for r in self.recent])
            ops = self.notebook.apply(p.ops, self.step_no + 1); self.notebook.set_focus(p.focus or self.notebook.focus, self.step_no + 1)
            if p.lesson: self.notebook.note(self.step_no + 1, f"lesson: {p.lesson}")
            self.last_plan, self.text_pending, self.focus_closed, self.urgent = self.step_no + 1, False, None, None
            self._plan_row = {"trigger": trigger, "focus": self.notebook.focus, "ops": ops, "why": p.why}
            view = self.world.view(self._goal_text(), place)
        affs, targets, preds = [], [], []
        choice, player = None, next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        act_route = travel_legs = None
        act_flags = list(self.flags)
        if marked and not self.act_text: act_flags.append('the sprite inside the red box is you')
        if self.recent or self.stuck.n:
            runs = []
            for recent in self.recent:
                action = recent.get("choice") or {}; skill = recent.get("skill") or {}; outcome = recent.get("outcome") or {}
                verb, target = str(action.get("verb") or "action"), str(action.get("target") or "")
                status = str(skill.get("status") or recent.get("verdict") or "tried")[:12]
                reason = str(skill.get("reason") or "")[:40]
                unchanged = "no_change" in outcome.get("effects", [])
                key, result = (verb, target), (status, reason)
                if runs and runs[-1][0] == key:
                    runs[-1][2] += 1; runs[-1][3] |= runs[-1][4] != result; runs[-1][4] = result; runs[-1][5] += unchanged
                else:
                    runs.append([key, recent.get("step", self.step_no), 1, False, result, int(unchanged)])
            summaries = [f"no effect x{self.stuck.n}"] if self.stuck.n else []
            for (verb, target), step, count, mixed, (status, reason), unchanged in runs:
                label = f"{verb[:16]} {target[:24]}".strip()
                summary = (f"{label} x{count} since step {step}; last {status}" if mixed else
                           f"{label} -> {status}" + (f" ({reason})" if reason else "") + f" x{count} since step {step}")
                if mixed and reason: summary += f" ({reason})"
                if unchanged: summary += f"; screen unchanged x{unchanged}"
                summaries.append(summary)
            recent_flag = "recent: " + "; ".join(summaries)
            while len(recent_flag) > 200 and len(summaries) > 1:
                summaries.pop(1 if self.stuck.n else 0); recent_flag = "recent: " + "; ".join(summaries)
            act_flags.append(recent_flag[:197] + "..." if len(recent_flag) > 200 else recent_flag)
        act_flags.append(self.world.here_flag())
        options = self.world.options(per, place, self.fails, self.step_no + 1, inputs=list(self.env.inputs()))  # failure history is in the option notes
        a = act(self.model, None if self.act_text else model_frame, per, self.notebook.render_focus(), self.world.view(self._goal_text(), place, compact=True), act_flags, "\n".join(_load(self.game).get("controls", [])), defaults(self.game), list(self.env.inputs()), places=[self.world.place_label(p["id"]) for p in sorted((p for p in self.mem.rows("places") if p.get("state") != "merged" and p.get("screen") == "world"), key=lambda p: p.get("last", p.get("first", 0)), reverse=True)[:12]], options=options)
        picked = next((o for o in options if o["n"] == getattr(a, "option", None)), None)
        applied = self.notebook.apply(a.ops, self.step_no + 1, structure=False)
        closed = self.notebook.data["goals"].get(self.notebook.focus or "", {})
        if closed.get("status") in FINISHED: self.focus_closed, self.moves = f"focus {self.notebook.focus} {closed['status']}: {closed.get('evidence', '')}", []  # progress: the place-move history starts over
        named = self.world.name(place, a.place, self.step_no + 1, frame=before)
        if named != place:  # model says this new arrival is a known place
            place = self.place = named
            if self.world.new_arrival: self.world.pos = next(((r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == place and (r.get("exit") or {}).get("to") == prior), self.world.pos)
            proj = self.world.before(per, place, place, size, self.step_no + 1)[0]
        if per.get("screen") == "world" and not modal and self.tile_px: self.mem.remember_view(place, self.world.pos, before)
        action, i = a.action, None
        if action and "go_to" in action and action["go_to"].casefold().startswith("unexplored"):
            act_route = self._unexplored(place)
            if act_route and player: affs.append({"verb": "approach", "target": "unexplored", "at": player.get("at")}); targets.append(None); preds.append(None); i = len(affs) - 1
            else: act_route = action = None
        if action and "go_to" in action and act_route is None:
            resolved = self.world.resolve(action["go_to"], per, place); e = next((e for n, e in enumerate(per.get("entities", [])) if self.world.entity_ids.get(n) == resolved.get("id")), None) if resolved and resolved["kind"] == "screen" else None
            if resolved and (e or resolved["kind"] != "screen"):
                affs.append({"verb": "approach", "target": (e or {}).get("name", action["go_to"]), "at": (e or {}).get("at")}); targets.append(resolved.get("id")); preds.append(None); i = len(affs) - 1
                act_route = {"dirs": dirs, "push": outward(self.mem, place, t, None)} if e and self.tile_px and player and (t := proj(e.get("at"))) and (dirs := route(self.mem, place, self.pos, t)) is not None else None; travel_legs = self.world.plan(resolved) if not e else None; action = None if travel_legs is None and not e else action
            else: action = None
        if action and "buttons" in action:
            affs.append({"verb": "buttons", "target": " ".join(action["buttons"])}); targets.append(None); preds.append(None); i = len(affs) - 1
        if action and "thing" in action:
            n, verb = action["thing"], action.get("verb")
            if type(n) is int and 0 <= n < len(per.get("entities", [])) and verb in ("approach", "interact", "select", "click"):
                entity = per["entities"][n]
                aff = {"verb": verb, "target": entity.get("name", ""), "at": entity.get("at")}
                affs.append(aff); targets.append(self.world.entity_ids.get(n)); preds.append(predict(self.mem, aff, targets[0], place)); i = 0
            else: action = None
        if action and action.get("verb") in ("advance", "cancel", "type"):
            aff = {"verb": action["verb"], "target": "", "at": None}
            affs.append(aff); targets.append(None); preds.append(predict(self.mem, aff, None, place)); i = 0
        if action: choice = Choice(i, a.expect, "", [], a.why, True, a.text)
        unknown = bool(choice and affs[choice.i].get("verb") in ("approach", "interact") and not self.env.pointer and
                       not any(e.get("kind", "").casefold() == "player" for e in per.get("entities", [])))
        self._live(before, place, per, choice and {**affs[choice.i], "why": choice.why, "text": choice.text}, a)
        skillctx = Ctx(self.env, self.model, self.tile_px, self.move_frames); pressed_from = tuple(self.world.pos)
        try:
            skill = self.world.travel(skillctx, travel_legs, self.step_no + 1, self.trace) if travel_legs is not None and choice else wait_until(skillctx) if not affs or choice is None else press_seq(skillctx, affs[choice.i]["target"].split()) if affs[choice.i].get("verb") == "buttons" else run_affordance(skillctx, affs[choice.i], per, text=choice.text, exit=False, **({"route": act_route} if act_route else {}))
        except BaseException: self.pending_cut, self.pending_via = cut(before, self.env.screenshot()), affs[choice.i].get("target") if choice else None; self.cached = self.cached_hash = None; raise
        if choice and skill.status == "done" and affs[choice.i].get("verb") in ("approach", "interact") and choice.i < len(targets) and targets[choice.i]:
            self.world._promote([targets[choice.i]])
        self.tile_px = skillctx.tile_px; self.world.tile_px = self.tile_px
        scene_cut = self.world._travel_cut if travel_legs is not None else skill.blank_seen or skill.reason == "scene cut" or (cut(skill.before, skill.after) and (self.env.pointer or not choice or affs[choice.i].get("verb") not in ("approach", "interact") or skill.scroll == (0, 0)))
        moves = getattr(skill, "moves", []); spatial = self.world.prepare(skill, place, scene_cut, self.step_no + 1); exit_key, cut_dir = spatial["exit_key"], spatial["cut_dir"]
        self.pending_cut, self.pending_via = scene_cut, affs[choice.i].get("target") if choice else None
        after_mark_eligible = per.get("screen") == "world" and not scene_cut
        try: after_set = wait_settled(self.env); after = after_set.frame; after_per, after_hash, after_reused, _ = self._see(after, place, screen_was_world=after_mark_eligible); after_fix = self.world.fix_player(after_per, after.size); player_fix = player_fix or after_fix
        except BaseException: self.cached = self.cached_hash = None; raise
        modal = {per.get("screen"), after_per.get("screen")} <= {"world", "dialogue", "menu"} and per.get("screen") != after_per.get("screen") and "world" in (per.get("screen"), after_per.get("screen")) and not (skill.blank_seen or skill.reason == "scene cut")
        scene_cut &= not modal
        next_place = self.world.current_place if travel_legs is not None else place if modal else self.mem.match_place(after_per, self.step_no + 1, prev=place, cut=scene_cut, via=(affs[choice.i].get("target") if choice else None), exit=exit_key, via_id=targets[choice.i] if choice else None, frame=after, tile_px=self.tile_px)
        spatial = self.world.after(skill, place, next_place, scene_cut, self.step_no + 1); known_exit = spatial["known_exit"]
        if next_place != place: self._moved(place, next_place, affs[choice.i].get("target") if choice else None, self.step_no + 1)
        if skill.scroll != (0, 0) and next_place == place and self.mem.get("places", place).get("state") == "candidate" and self.mem.get("places", place).get("screen") == "world":
            source = self.mem.get("places", place); source["state"] = "place"; self.mem.upsert("places", source)
        world_exit = bool(scene_cut and next_place != place and cut_dir and per.get("screen") == after_per.get("screen") == "world")
        outcome = effects(per, after_per, skill, place, next_place, place_after_candidate=not world_exit and self.mem.get("places", next_place).get("state") == "candidate", scene_cut=scene_cut and next_place != place)
        blocked = skill.reason == "route blocked" and act_route and len(moves) < len(act_route["dirs"])
        pred = (preds[choice.i] or {"effect": choice.effect, "detail": choice.detail, "basis": "guess"}) if choice else None
        if choice and str(affs[choice.i].get("target", "")).startswith("exit"):
            pred = {**pred, "effect": "place_changes", "detail": pred.get("detail") if pred.get("effect") == "place_changes" else None}
        v = "unobservable" if not affs or not choice else verdict(pred, outcome, blocked=skill.status == "blocked", inputs=skill.inputs)
        if choice and targets[choice.i]:
            new_text = next((t for t in after_per.get("text", []) if t not in per.get("text", [])), "")
            for effect in outcome["effects"]:
                detail = outcome.get("to") if effect == "place_changes" else new_text if effect == "text_appears" else choice.detail or effect
                said = next((r for r in self.mem.rows("relations") if (r.get("s"), r.get("p"), r.get("o")) == (targets[choice.i], PRED[effect], detail)), None) if effect == "text_appears" else None
                said_step = said["step"] if said else None  # relate() updates the stored row in place
                rel = self.mem.relate(targets[choice.i], PRED[effect], str(detail or ""),
                    src=outcome["basis"].get(effect, "model"), step=self.step_no + 1,
                    hit=True if outcome["basis"].get(effect) == "pixel" else None,
                    verb=affs[choice.i]["verb"], pattern_id=pred.get("id") if pred and pred.get("basis") == "pattern" and effect == pred.get("effect") else None)
                if said_step is not None: self.mem.upsert("relations", {**rel, "why": f"says the same as before (step {said_step})"})  # ponytail: a note on the says row, not a new relation kind
            # A route stopped by a newly learned wall before the target says nothing about the target; replan next step.
            if pred and pred["effect"] not in outcome["effects"] and not blocked:
                self.mem.relate(targets[choice.i], PRED[pred["effect"]], str(pred.get("detail") or ""),
                                src="observed", step=self.step_no+1, hit=False, verb=affs[choice.i]["verb"],
                                pattern_id=pred.get("id") if pred.get("basis") == "pattern" else None)
        edge_rows = self.mem.get("places", place).get("edges", [])
        verified_edge = bool(choice and scene_cut and next_place != place and any(outcome["basis"].get(e) == "pixel" for e in ("place_changes", "screen_changes")) and
            not any((e.get("verb"), e.get("via"), e.get("to")) == (affs[choice.i]["verb"], affs[choice.i].get("target", ""), next_place) and e.get("ok") for e in edge_rows))
        if choice and scene_cut and next_place != place:  # a cut to a non-navigable screen is no edge
            self.mem.add_edge(place, affs[choice.i]["verb"], affs[choice.i].get("target", ""), next_place, True)
            if world_exit: self.world.mark_exit(place, cut_dir, next_place, self.world.pos, self.step_no+1)
        row = {"step": self.step_no + 1, "t": time.time(), "place": place, "screen": per.get("screen"),
            "task": None, "frame": f"frames/{self.step_no+1:06d}.png", "after": f"frames/{self.step_no+1:06d}_after.png", "options": len(affs),
            "choice": ({"i": choice.i, **affs[choice.i], "id": targets[choice.i], "called": choice.called, "text": choice.text, "why": choice.why} if choice else None),
            "pred": pred, "used": choice.used if choice else [], "skill": {"status": skill.status, "reason": skill.reason, "inputs": skill.inputs, "calls": skill.calls},
            "outcome": outcome, "verdict": v, "calls": self.model.calls-calls, "ms": int((time.perf_counter()-start)*1000), "shortcut": None,
            "ranker": None, "perception_reused": reused, "frame_hash": frame_hash, "marked": marked,
            "scene_cut": scene_cut, "next_place": next_place, "verified_edge": verified_edge, "view_ids": [], "tile": self.world.pos if self.tile_px else None, "drift_fix": list(drift_fix) if drift_fix else None, "player_fix": player_fix, "entities": self.world.entities, "scroll": spatial["scroll"], **({"exit": list(exit_key)} if scene_cut and not modal and next_place != place and exit_key else {}),
            "act": {"place": a.place, "ops": applied, "why": a.why, "action": a.action, "flags": act_flags},
            "option": {k: picked[k] for k in ("n", "tool", "label", "notes")} if picked else None, **({"flags": [f"merge_pending: {self.world.merge_pending}"]} if self.world.merge_pending else {})}
        row["plan"] = self._plan_row
        row["probe"] = {"presses": probe["presses"], "locked": probe["locked"]} if probe is not None else None
        self.step_no += 1
        after.save(self.mem.root.parent / row["after"])
        drift_fix = self.world.observe_after(after_per, after, after_reused, next_place, after.size, self.step_no, before=before, skill=skill)
        row["tile"], row["drift_fix"] = self.world.pos if self.tile_px else None, list(drift_fix) if drift_fix else None
        row["self_spot"], row["self_locked"] = self.world.self_spot, self.world._self_from_motion
        self._learn(after_per.get("text", []), per.get("text", []), self.step_no, next_place)
        self.place, self.prev, self.prev_frame, self.cached, self.cached_hash = next_place, after_per, after, after_per, after_hash
        self.prev_frame_mark_eligible = after_mark_eligible
        self.pending_cut, self.pending_via = False, None; reason = self.stuck.update(row); self.repair_pending = self.parse_failed and self.empty_streak >= 8 and not self.repair_used; self.repair_used |= self.repair_pending
        if unknown: reason = "player position unknown"
        if choice is not None:
            verb, target = affs[choice.i].get("verb"), affs[choice.i].get("target") or ""; key = self._fail_key(place, verb, target)
            failed = skill.status in ("blocked", "failed") or v == "miss" or "no_change" in outcome.get("effects", [])
            d = next((x.upper() for x in target.split() if x.upper() in DIRS), None) if verb == "buttons" else None  # a press aims at the tile it faces
            ahead = (pressed_from[0] + DIRS[d][0], pressed_from[1] + DIRS[d][1]) if d else None
            thing = targets[choice.i] or next((r["id"] for r in self.mem.rows("entities") if r.get("place") == place and r.get("tile") and tuple(r["tile"]) == ahead), None)
            if failed:
                f = self.fails.setdefault(key, {"n": 0}); f.update(n=f["n"] + 1, why=(skill.reason or f"verdict {v}")[:40], step=self.step_no, act=f"{verb} {target}".strip())
                if f["n"] == 2: self.urgent = self.urgent or f"the same action failed twice here: {f['act']} ({f['why']})"
                if thing: self.mem.relate(thing, PRED["no_change"], "", src="observed", step=self.step_no, hit=False, verb=verb)  # the thing itself is contradicted; never deleted
            elif thing and (failure := next((r for r in self.mem.rows("relations") if (r.get("s"), r.get("p"), r.get("o")) == (thing, PRED["no_change"], "")), None)):
                self.mem.upsert("relations", {**failure, "con": 0, "ev": [], "state": "live"})  # ponytail: a success clears the failure row in place (no sup accounting: predict reads it)
        if a.action and "go_to" in a.action and choice is None: reason = f'go_to "{a.action["go_to"]}": not in memory'
        if not a.action and not reason: reason = "your last reply had no valid action, so nothing happened: use one of the action shapes"
        self.flags = [x for x in (reason, f"last action {skill.status}: {skill.reason}" if skill.status != "done" else None, f"verdict {v}" if v in ("miss", "surprise") else None, f"merge_pending: {self.world.merge_pending}" if self.world.merge_pending else None) if x]; self.world.merge_pending = None; self.world.new_arrival = False
        self.trace.write(row); self.recent = [*self.recent, row][-5:]
        return row
    def _learn(self, texts, seen, step, place):
        """Store new game text and log it once, so dialogue is remembered after it scrolls away."""
        known = {_key(r.get("text", "")) for r in self.mem.rows("knowledge")}
        fresh = [t for t in texts if t not in seen and _key(t) not in known]
        for text in (t for t in texts if t not in seen): self.mem.know(text, src="game_text", step=step)
        if fresh: self.notebook.note(step, f"text at {self.world.place_label(place) if place else '?'}: " + " / ".join(f'"{t}"' for t in fresh)); self.text_pending = True
    def _unexplored(self, place):
        """Route to the nearest untried edge (edges into unseen cells first), plus the step across it."""
        if not self.tile_px: return None
        seen = set((self.mem.get("seen", place) or {}).get("cells", []))
        best = None
        for (x, y), d in frontier(self.mem, place):
            if ((self.mem.get("tiles", f"{place}@{x},{y}") or {}).get("exit") or {}).get("dir") == d: continue  # an exit, even inferred, is not unexplored (m1_gp2: walked back through the front door)
            dirs = route(self.mem, place, self.pos, (x, y))
            if dirs is None: continue
            key = (f"{x + DIRS[d][0]},{y + DIRS[d][1]}" in seen, len(dirs))
            if best is None or key < best[0]: best = (key, [*dirs, d])
        return {"dirs": best[1]} if best else None
    def _live(self, frame, place, per, choice, a):
        """The decision before it is acted on, for scripts/watch_run.py; the trace row only lands after the skill and verification."""
        root = self.mem.root.parent; name = f"frames/{self.step_no+1:06d}.png"
        (root / "frames").mkdir(parents=True, exist_ok=True); frame.save(root / name)
        live = {"step": self.step_no + 1, "t": time.time(), "frame": name, "place": place, "screen": per.get("screen"), "entities": self.world.entities,
                "choice": choice or None, "act": {"place": a.place, "why": a.why, "action": a.action}, "plan": self._plan_row, "self_spot": self.world.self_spot}
        try: (root / "live.tmp").write_text(json.dumps(live, default=str), encoding="utf-8"); os.replace(root / "live.tmp", root / "live.json")
        except OSError: pass  # a reader holding live.json on Windows; the trace row still lands
    def _fail_key(self, place, verb, target):
        """One key per place and intent: button presses collapse to their direction keys (else the first button), targets ignore the verb."""
        return fail_key(place, verb, target, self.env.inputs())
    def _moved(self, src, dst, via, step):
        label = self.world.place_label
        self.notebook.note(step, f"went from {label(src)} to {label(dst)}" + (f" via {via}" if via else ""))
        self.moves = [*self.moves, [step, src, dst, via or ""]][-6:]
        last = self.moves[-4:]  # A<->B four times running, however slowly; progress (a focus closing) clears self.moves
        if len(last) == 4 and len({(m[1], m[2]) for m in last}) == 2 and all(a[2] == b[1] for a, b in zip(last, last[1:])):
            self.urgent = f"cycle: went back and forth between {label(src)} and {label(dst)} {len(last)} times in {step - last[0][0]} steps without progress"; self.moves = []
            for m in last:  # act sees, in each of the two places, that the way it keeps taking only leads back
                if m[3]: f = self.fails.setdefault(self._fail_key(m[1], "", m[3]), {"n": 0}); f.update(n=f["n"] + 1, why=f"leads back to {label(m[2])}: cycle", step=step)
        elif (self.mem.get("places", dst) or {}).get("visits", 0) <= 1 and not any(m[1] == dst for m in self.moves):
            self.urgent = self.urgent or f"arrived somewhere new: {label(dst)}"
    def _goal_text(self):
        goals = self.notebook.data["goals"]; path = self.notebook.path()
        keys = path or [k for k, v in goals.items() if v.get("status", "active") == "active"]
        return " ".join(f"{k} {goals[k].get('text', '')}" for k in keys)
    def _plan_trigger(self, per):
        """Events that warrant replanning; the model decides what they mean. Closing the focus always replans, the rest at most every 3 steps."""
        if self.focus_closed: return self.focus_closed
        if self.urgent: return self.urgent
        if self.last_plan is not None and self.step_no + 1 - self.last_plan < 3: return None
        goal = self.notebook.data["goals"].get(self.notebook.focus or "")
        if not goal or goal.get("status") in FINISHED: return "no focus goal yet" if not goal else f"focus {self.notebook.focus} is {goal['status']}"
        if self.text_pending and per.get("screen") == "world": return "new game text was seen (see the log)"
        stuck = next((f for f in self.flags if f in ("same_action", "no_progress")), None)
        # ponytail: 8-step floor on stuck replans; m1_gp replanned every ~3 steps into button-level goals
        return f"stuck: {stuck}" if stuck and (self.last_plan is None or self.step_no + 1 - self.last_plan >= 8) else None
    def state(self):
        return {"step": self.step_no, "place": self.place, **self.world.state(), "tile_px": self.tile_px, "move_frames": self.move_frames, "stuck": self.stuck.state(), "reperception_used": self.reperception_used, "perception": self.cached, "frame_hash": self.cached_hash, "perception_marked": self.cached_marked, "frame_mark_eligible": self.prev_frame_mark_eligible, "perception_version": self.cached_version, "parse_failed": self.parse_failed, "empty_streak": self.empty_streak, "repair_used": self.repair_used, "repair_pending": self.repair_pending, "pending_cut": self.pending_cut, "pending_via": self.pending_via, "flags": self.flags, "planner": {"last": self.last_plan, "text_pending": self.text_pending, "focus_closed": self.focus_closed, "fails": self.fails, "moves": self.moves, "urgent": self.urgent}}
    def restore(self, state):
        self.step_no, self.place = int(state.get("step", 0)), state.get("place")
        self.tile_px, self.move_frames = state.get("tile_px"), int(state.get("move_frames", 16))
        self.world.tile_px = self.tile_px; self.world.restore(state)
        self.stuck.restore(state.get("stuck", {})); self.prev = state.get("perception"); self.prev_frame = self.env.screenshot()
        self.prev_frame_mark_eligible = state.get("frame_mark_eligible")
        self.reperception_used = bool(state.get("reperception_used", False))
        digest = hashlib.sha256(self.prev_frame.tobytes()).hexdigest()
        self.cached, self.cached_hash, self.cached_marked, self.cached_version = state.get("perception"), state.get("frame_hash"), bool(state.get("perception_marked", False)), state.get("perception_version")
        self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = bool(state.get("parse_failed")), int(state.get("empty_streak", 0)), bool(state.get("repair_used")), bool(state.get("repair_pending")); self.pending_cut, self.pending_via = bool(state.get("pending_cut")), state.get("pending_via"); self.flags = state.get("flags", [])
        planner = state.get("planner") or {}; self.last_plan, self.text_pending, self.focus_closed = planner.get("last"), bool(planner.get("text_pending")), planner.get("focus_closed")
        self.fails, self.moves, self.urgent = planner.get("fails") or {}, planner.get("moves") or [], planner.get("urgent")
        if self.cached_version != self.prompt_version or digest != self.cached_hash: self.cached = self.cached_hash = None; self.cached_marked = False
