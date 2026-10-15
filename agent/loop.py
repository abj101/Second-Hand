"""One game-neutral perception, choice, action, and learning cycle."""
import hashlib, json, os, re, time
from types import SimpleNamespace
from agent.act import act
from agent.brief import GENERAL, _load, defaults, load_brief
from agent.decide import Choice
from agent.memory import _key, place_aliases, place_name
from agent.notebook import FINISHED, Notebook
from agent.observer import cut, for_model, wait_settled
from agent.perceive import perceive
from agent.plan import chapter, plan
from agent.predict import PRED, effects, predict, verdict
from agent.skills import Ctx, SkillResult, press_seq, run_affordance, wait_until
from agent.tasks import Stuck
from agent.trace import Trace
from agent.space import DIRS, frontier, outward, route
from agent.world import World, fail_key, open_cells
from PIL import Image
CAPTION = 'The sprite inside the red box is the player\'s own character (you); list it with kind "player".'
def failure_cause(intent, label, stage, reason, tile=None, blocker=None):
    """The one factual cause of a failed act, read by planner and chooser: intent, failed stage, blocker when known, crossing tried or not."""
    text = f"{label} [{intent}] " + (f"failed at {stage}" if stage else f"failed: {reason}")
    if stage and reason not in ("route blocked", "exit not crossed", ""): text += f": {reason}"
    if tile: text += f"; blocked at ({tile[0]},{tile[1]})" + (f" by thing:{blocker}" if blocker else "")
    if stage == "route": text += "; no crossing attempted"
    elif stage == "crossing": text += "; crossing attempted"
    return text
class Loop:
    def __init__(self, env, model, mem, game, act_text=False, decide=False):
        self.act_text = act_text  # act sees description + spatial layer only, no pixels
        self.decide = decide  # act is text-only and answers only an option number (act.py _decide)
        self.env, self.model, self.mem, self.game = env, model, mem, game
        self.flags = []
        self.trace = Trace(mem.root.parent / "trace.jsonl"); self.step_no = 0
        self.place = None; self.prev = None; self.tile_px = getattr(env, "tile_px", None)
        self.move_frames = 16; self.stuck = Stuck()
        self.recent = self.trace.tail(5); self.cached = None; self.cached_hash = None; self.cached_marked = False; self.prev_frame = None; self.prev_frame_mark_eligible = None; self.prompt_version = "e5-perceiver-10"; self.cached_version = self.prompt_version
        self.seen_image, self.seen_marked = None, False
        self.pending_cut, self.pending_via, self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = False, None, False, 0, False, False
        self.pending_key = None  # intent key of the last action, for a cycle mark on the move it caused
        self.cause = None  # failure_cause text of the last failed act (planner and chooser read it)
        self.reported = None  # the failure evidence last raised as an urgent replan: [intent, stage, blocker or tile]
        self.reperception_used = False
        self.world = World(mem, self.tile_px)
        self.entered, self.events, self.chapter_after, self._chapter_due = {}, [], 0, False  # entered: place -> step last entered; events: [{kind T1..T5, text, step}] consumed whole by one plan call
        self.notebook = Notebook(mem.root, buttons=env.inputs(), places=self._places)
        self.last_plan, self._plan_row = None, None
        self.conversation, self.text_for_trigger = {"place": None, "lines": []}, ""
        self.fails, self.moves = {}, []
        self.stall_unchanged, self.plan_flags, self.cycle_flag, self.new_places = 0, [], None, []
        self.planner_evidence, self.fail_baseline = [], {}
        self.pending = None  # an action whose after-frame had not settled: its state for _resolve (None when none)
        self.unsettled = 0  # outer-step waits in a row that never settled; HF contract 5: one observe-only retry, then act on the latest frame (ponytail: not checkpointed, a restore may hold once more)
        self.invocation = None  # the runner's manifest invocation number (set by scripts/run_agent.py)
        self.provenance_incomplete = False  # restored from a checkpoint with no recorded modes: earlier calls and modes are unknown
        self._seq0 = None  # model.seq when the current outer step began: its model calls are (_seq0, seq]
    def _places(self):  # the current place for reach checks: [(step last entered, [names])]
        row = self.mem.get("places", self.place) if self.place else None
        return [(self.entered.get(self.place, 0), [n for n in (place_name(row), *place_aliases(row)) if n])] if row else []
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
        start, calls, self._seq0 = time.perf_counter(), self.model.calls, getattr(self.model, "seq", None)
        before_set = wait_settled(self.env); before = before_set.frame; prior = self.place; self.unsettled = 0 if before_set.settled else self.unsettled + 1
        if self.pending is not None: return self._resolve(before_set, start, calls)
        if self.unsettled == 1: return self._pre_hold(before_set, start, calls)  # never settled: no perception, no input, once; after that act on the latest frame
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
        if prior and place != prior: self.world.unseen_cross(prior, place, self.step_no + 1); self._moved(prior, place, self.pending_via, self.step_no + 1, self.pending_key)
        self.place, self.pending_cut, self.pending_via, self.pending_key = place, False, None, None
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
        self._learn(per.get("text", []), (self.prev or {}).get("text", []), self.step_no + 1, place, per.get("screen"))
        self.world.view_flags = self.flags
        view = self.world.view(self._goal_text(), place)
        self._plan_row = None
        if (trigger := self._plan_trigger(per)):
            top_before, events, self.events = self.notebook.focus, self.events, []
            evidence = self._evidence()
            p = plan(self.model, trigger, self.notebook, view, per.get("text", []), [f"{(r.get('choice') or {}).get('verb')} {(r.get('choice') or {}).get('target') or ''} -> {r.get('verdict')}" for r in self.recent], evidence=evidence)
            self.new_places, self.planner_evidence = [], []
            result = self._apply_plan(p, trigger, self.step_no + 1, top_before)
            self.last_plan = self.step_no + 1
            self.fail_baseline = {key: value.get("n", 0) for key, value in self.fails.items()}
            self._plan_row = {"trigger": trigger, "events": events, "focus": self.notebook.focus, "quests_changed": result["quests_changed"], "beliefs_changed": result["beliefs_changed"], "flags": result["flags"], "why": p.why}
            view = self.world.view(self._goal_text(), place)
        affs, targets, preds, intents = [], [], [], []  # intents: each affordance's intent key, resolved before input
        choice, player = None, next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        act_route = travel_legs = None; refused = False  # refused: no route to an on-screen target avoids the known blockers (occupied tiles, remembered avoid tile)
        act_flags = list(self.flags)
        if self.cycle_flag: act_flags.append(self.cycle_flag)
        if marked and not (self.act_text or self.decide): act_flags.append('the sprite inside the red box is you')
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
                if status == "done" and unchanged == count: status, unchanged = "no effect", 0  # every run left the screen as it was
                summary = (f"{label} x{count} since step {step}; last {status}" if mixed else
                           f"{label} -> {status}" + (f" ({reason})" if reason else "") + f" x{count} since step {step}")
                if mixed and reason: summary += f" ({reason})"
                if unchanged: summary += f"; screen unchanged x{unchanged}"
                summaries.append(summary)
            recent_flag = "recent: " + "; ".join(summaries)
            while len(recent_flag) > 200 and len(summaries) > 1:
                summaries.pop(1 if self.stuck.n else 0); recent_flag = "recent: " + "; ".join(summaries)
            act_flags.append(recent_flag[:197] + "..." if len(recent_flag) > 200 else recent_flag)
        act_flags.append(self.world.here_flag(self.fails))
        options = self.world.options(per, place, self.fails, self.step_no + 1, inputs=list(self.env.inputs()))  # failure history is in the option notes
        a = act(self.model, None if self.act_text or self.decide else model_frame, per, self.notebook.render_focus(), self.world.view(self._goal_text(), place, compact=True), act_flags, "\n".join(_load(self.game).get("controls", [])), defaults(self.game), list(self.env.inputs()), places=[self.world.place_label(p["id"]) for p in sorted((p for p in self.mem.rows("places") if p.get("state") != "merged" and p.get("screen") == "world"), key=lambda p: p.get("last", p.get("first", 0)), reverse=True)[:12]], options=options, **({"decide": True} if self.decide else {}))
        applied = 0
        picked = next((o for o in options if o["n"] == getattr(a, "option", None)), None)
        own_pick = picked if picked and picked.get("action") == a.action else None  # the option act picked, when its action is the one it answered with
        named = self.world.name(place, a.place or per.get("place") or "", self.step_no + 1, frame=before)
        if named != place:  # model says this new arrival is a known place
            place = self.place = named
            if self.world.new_arrival: self.world.pos = next(((r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == place and (r.get("exit") or {}).get("to") == prior), self.world.pos)
            proj = self.world.before(per, place, place, size, self.step_no + 1)[0]
        if per.get("screen") == "world" and not modal and self.tile_px: self.mem.remember_view(place, self.world.pos, before)
        action, i = a.action, None
        if action and "go_to" in action and action["go_to"].casefold().startswith("unexplored"):
            act_route = self._unexplored(place)
            if act_route and player: affs.append({"verb": "approach", "target": "unexplored", "at": player.get("at")}); targets.append(None); preds.append(None); intents.append(self._fail_key(place, "approach", "unexplored")); i = len(affs) - 1
            else: act_route = action = None
        if action and "go_to" in action and act_route is None:
            resolved = self.world.resolve(action["go_to"], per, place); e = next((e for n, e in enumerate(per.get("entities", [])) if self.world.entity_ids.get(n) == resolved.get("id")), None) if resolved and resolved["kind"] == "screen" else None
            if resolved and (e or resolved["kind"] != "screen"):
                ref = f"{'place' if resolved['kind'] == 'place' else 'thing'}:{resolved['id']}" if resolved.get("id") else None  # the name resolved to what it names
                affs.append({"verb": "approach", "target": (e or {}).get("name", action["go_to"]), "at": (e or {}).get("at")}); targets.append(resolved.get("id")); preds.append(None); intents.append(self._fail_key(place, "approach", affs[-1]["target"], ref=ref)); i = len(affs) - 1
                held = (self.fails.get(intents[-1]) or {}).get("avoid")  # a route blocker held this intent's last route: go around its tile
                t = proj(e.get("at")) if e and self.tile_px and player else None
                act_route = self._screen_route(place, t, outward(self.mem, place, t, None), [tuple(held)] if held else ()) if t else None; refused = bool(t and act_route is None)
                travel_legs = self.world.plan(resolved, exit=own_pick.get("exit") if own_pick else None) if not e else None; action = None if travel_legs is None and not e else action
            else: action = None
        if action and "buttons" in action:
            affs.append({"verb": "buttons", "target": " ".join(action["buttons"])}); targets.append(None); preds.append(None); intents.append(self._fail_key(place, "buttons", affs[-1]["target"])); i = len(affs) - 1
        if action and "thing" in action:
            n, verb = action["thing"], action.get("verb")
            if type(n) is int and 0 <= n < len(per.get("entities", [])) and verb in ("approach", "interact", "select", "click"):
                entity = per["entities"][n]
                aff = {"verb": verb, "target": entity.get("name", ""), "at": entity.get("at")}
                affs.append(aff); targets.append((ident := self.world.entity_ids.get(n))); preds.append(predict(self.mem, aff, targets[0], place)); intents.append(self._fail_key(place, verb, aff["target"], ref=f"thing:{ident}" if ident else None)); i = 0
            else: action = None
        if action and action.get("verb") in ("advance", "cancel", "type"):
            aff = {"verb": action["verb"], "target": "", "at": None}
            affs.append(aff); targets.append(None); preds.append(predict(self.mem, aff, None, place)); intents.append(self._fail_key(place, action["verb"], "")); i = 0
        if action: choice = Choice(i, a.expect, "", [], a.why, True, a.text)
        intent = ((own_pick or {}).get("intent_key") or intents[choice.i]) if choice else None  # captured before input: the same key execution, counters and trace use
        unknown = bool(choice and affs[choice.i].get("verb") in ("approach", "interact") and not self.env.pointer and
                       not any(e.get("kind", "").casefold() == "player" for e in per.get("entities", [])))
        self._live(before, place, per, choice and {**affs[choice.i], "why": choice.why, "text": choice.text}, a)
        skillctx = Ctx(self.env, self.model, self.tile_px, self.move_frames); pressed_from = tuple(self.world.pos)
        try:
            skill = SkillResult("blocked", "no route avoids blocker", 0, 0, before, before, (0, 0), False, failure={"stage": "route", "reason": "no route avoids blocker", "direction": None}) if refused else self.world.travel(skillctx, travel_legs, self.step_no + 1, self.trace) if travel_legs is not None and choice else wait_until(skillctx) if not affs or choice is None else press_seq(skillctx, affs[choice.i]["target"].split()) if affs[choice.i].get("verb") == "buttons" else run_affordance(skillctx, affs[choice.i], per, text=choice.text, exit=False, **({"route": act_route} if act_route else {}))
        except BaseException: self.pending_cut, self.pending_via, self.pending_key = cut(before, self.env.screenshot()), affs[choice.i].get("target") if choice else None, intent; self.cached = self.cached_hash = None; raise
        if choice and skill.status == "done" and affs[choice.i].get("verb") in ("approach", "interact") and choice.i < len(targets) and targets[choice.i]:
            self.world._promote([targets[choice.i]])
        self.tile_px = skillctx.tile_px; self.world.tile_px = self.tile_px
        scene_cut = self.world._travel_cut if travel_legs is not None else skill.blank_seen or skill.reason == "scene cut" or (cut(skill.before, skill.after) and (self.env.pointer or not choice or affs[choice.i].get("verb") not in ("approach", "interact") or skill.scroll == (0, 0)))
        spatial = self.world.prepare(skill, place, scene_cut, self.step_no + 1); exit_key, cut_dir = spatial["exit_key"], spatial["cut_dir"]
        self.pending_cut, self.pending_via, self.pending_key = scene_cut, affs[choice.i].get("target") if choice else None, intent
        pred = (preds[choice.i] or {"effect": choice.effect, "detail": choice.detail, "basis": "guess"}) if choice else None
        if choice and str(affs[choice.i].get("target", "")).startswith("exit"):
            pred = {**pred, "effect": "place_changes", "detail": pred.get("detail") if pred.get("effect") == "place_changes" else None}
        row_choice = {"i": choice.i, **affs[choice.i], "id": targets[choice.i], "called": choice.called, "text": choice.text, "why": choice.why, "intent": intent} if choice else None
        # the action's state, JSON-safe: what a deferred resolution needs once the screen settles (see _resolve)
        s = {"action_step": self.step_no + 1, "before_settled": before_set.settled, "frame": f"frames/{self.step_no + 1:06d}.png", "place": place, "per": per, "reused": reused, "frame_hash": frame_hash, "marked": marked,
             "player_fix": player_fix, "options": len(affs), "choice": row_choice, "aff": affs[choice.i] if choice else None, "tid": targets[choice.i] if choice else None,
             "pred": pred, "detail": choice.detail if choice else None, "used": choice.used if choice else [], "intent": intent, "unknown": unknown, "scene_cut": scene_cut,
             "exit_key": list(exit_key) if exit_key else None, "cut_dir": cut_dir, "travel": travel_legs is not None, "route_len": len(act_route["dirs"]) if act_route else None,
             "refused": refused, "pressed_from": list(pressed_from), "go_to": f'go_to "{a.action["go_to"]}": not in memory' if a.action and "go_to" in a.action and choice is None else None,
             "no_action": not a.action, "act": {"place": a.place, "ops": applied, "why": a.why, "action": a.action, "flags": act_flags},
             "option": {k: picked[k] for k in ("n", "tool", "label", "notes")} if picked else None, "plan": self._plan_row,
             "probe": {"presses": probe["presses"], "locked": probe["locked"]} if probe is not None else None,
             "world": {"cut_dir": cut_dir, "pos": list(self.world.pos), "travelled": skill is self.world._travelled, "travel_open": self.world._travel_open, "current_place": self.world.current_place}}
        try: after_set = wait_settled(self.env)
        except BaseException: self.cached = self.cached_hash = None; raise
        if after_set.settled and getattr(skill, "settled", None) is not False or self.unsettled: return self._settle(s, skill, after_set, start, calls)  # an unsettled streak already had its retry
        return self._hold_action(s, skill, start, calls, keep=bool(skill.inputs))  # inputs were sent: wait for the settled frame; none sent: no evidence
    def _settle(self, s, skill, after_set, start, calls, *, resolved=False):
        """Evidence and the row for an action whose after-frame settled: one tail for an immediate action and a resolved pending one."""
        per, place, choice, aff, tid, pred, intent = s["per"], s["place"], s["choice"], s["aff"], s["tid"], s["pred"], s["intent"]
        scene_cut, cut_dir, exit_key, refused = s["scene_cut"], s["cut_dir"], tuple(s["exit_key"]) if s["exit_key"] else None, s["refused"]
        after = after_set.frame; after_mark_eligible = per.get("screen") == "world" and not scene_cut
        player_fix = s["player_fix"]
        try:
            after_per, after_hash, after_reused, _ = self._see(after, place, screen_was_world=after_mark_eligible); after_fix = self.world.fix_player(after_per, after.size); player_fix = player_fix or after_fix
        except BaseException: self.cached = self.cached_hash = None; raise
        modal = {per.get("screen"), after_per.get("screen")} <= {"world", "dialogue", "menu"} and per.get("screen") != after_per.get("screen") and "world" in (per.get("screen"), after_per.get("screen")) and not (skill.blank_seen or skill.reason == "scene cut")
        scene_cut &= not modal
        next_place = self.world.current_place if s["travel"] and not self.world._travel_open else place if modal else self.mem.match_place(after_per, self.step_no + 1, prev=place, cut=scene_cut, via=(aff.get("target") if aff else None), exit=exit_key, via_id=tid, frame=after, tile_px=self.tile_px)
        spatial = self.world.after(skill, place, next_place, scene_cut, self.step_no + 1)
        if next_place != place: self._moved(place, next_place, aff.get("target") if aff else None, self.step_no + 1, intent)
        if skill.scroll != (0, 0) and next_place == place and self.mem.get("places", place).get("state") == "candidate" and self.mem.get("places", place).get("screen") == "world":
            source = self.mem.get("places", place); source["state"] = "place"; self.mem.upsert("places", source)
        world_exit = bool(scene_cut and next_place != place and cut_dir and per.get("screen") == after_per.get("screen") == "world")
        outcome = effects(per, after_per, skill, place, next_place, place_after_candidate=not world_exit and self.mem.get("places", next_place).get("state") == "candidate", scene_cut=scene_cut and next_place != place)
        route = (getattr(skill, "failure", None) or {}).get("stage") in ("route", "interaction")  # never reached the thing (a route stopped short, a choice refused before any press): no evidence about it
        blocked = route or (skill.reason == "route blocked" and s["route_len"] is not None and len(getattr(skill, "moves", [])) < s["route_len"])
        v = "unobservable" if not choice else verdict(pred, outcome, blocked=skill.status == "blocked", inputs=skill.inputs)
        if choice and tid and not route:  # a route that never reached the thing says nothing about it
            new_text = next((t for t in after_per.get("text", []) if t not in per.get("text", [])), "")
            for effect in outcome["effects"]:
                detail = outcome.get("to") if effect == "place_changes" else new_text if effect == "text_appears" else s["detail"] or effect
                said = next((r for r in self.mem.rows("relations") if (r.get("s"), r.get("p"), r.get("o")) == (tid, PRED[effect], detail)), None) if effect == "text_appears" else None
                said_step = said["step"] if said else None  # relate() updates the stored row in place
                rel = self.mem.relate(tid, PRED[effect], str(detail or ""),
                    src=outcome["basis"].get(effect, "model"), step=self.step_no + 1,
                    hit=True if outcome["basis"].get(effect) == "pixel" else None,
                    verb=aff["verb"], pattern_id=pred.get("id") if pred and pred.get("basis") == "pattern" and effect == pred.get("effect") else None)
                if said_step is not None: self.mem.upsert("relations", {**rel, "why": f"says the same as before (step {said_step})"})  # ponytail: a note on the says row, not a new relation kind
                if effect == "text_appears":
                    entity = self.mem.get("entities", tid)
                    speaker = re.match(r"^([A-Z][A-Za-z.' -]{0,19}):\s*", new_text)
                    if entity and self.world._actor(tid, entity) and speaker and sum(c.isalpha() for c in speaker.group(1)) >= 2:
                        self.mem.call(tid, speaker.group(1), self.step_no + 1)
            # A route stopped by a newly learned wall before the target says nothing about the target; replan next step.
            if pred and pred["effect"] not in outcome["effects"] and not blocked:
                self.mem.relate(tid, PRED[pred["effect"]], str(pred.get("detail") or ""),
                                src="observed", step=self.step_no+1, hit=False, verb=aff["verb"],
                                pattern_id=pred.get("id") if pred.get("basis") == "pattern" else None)
        edge_rows = self.mem.get("places", place).get("edges", [])
        verified_edge = bool(choice and scene_cut and next_place != place and any(outcome["basis"].get(e) == "pixel" for e in ("place_changes", "screen_changes")) and
            not any((e.get("verb"), e.get("via"), e.get("to")) == (aff["verb"], aff.get("target", ""), next_place) and e.get("ok") for e in edge_rows))
        if choice and scene_cut and next_place != place:  # a cut to a non-navigable screen is no edge
            self.mem.add_edge(place, aff["verb"], aff.get("target", ""), next_place, True)
            if world_exit: self.world.mark_exit(place, cut_dir, next_place, self.world.pos, self.step_no+1)
        row = {"step": self.step_no + 1, "t": time.time(), "place": place, "screen": per.get("screen"),
            "task": None, "frame": s["frame"], "after": f"frames/{self.step_no+1:06d}_after.png", "options": s["options"],
            "choice": choice, "pred": pred, "used": s["used"], "skill": {"status": skill.status, "reason": skill.reason, "inputs": skill.inputs, "calls": skill.calls, "failure": getattr(skill, "failure", None), "settled": getattr(skill, "settled", None)},
            "outcome": outcome, "verdict": v, "calls": self.model.calls-calls, "ms": int((time.perf_counter()-start)*1000), "shortcut": None,
            "ranker": None, "perception_reused": s["reused"], "frame_hash": s["frame_hash"], "marked": s["marked"],
            "scene_cut": scene_cut, "next_place": next_place, "verified_edge": verified_edge, "view_ids": [], "tile": self.world.pos if self.tile_px else None, "drift_fix": None, "player_fix": player_fix, "entities": self.world.entities, "scroll": spatial["scroll"], **({"exit": list(exit_key)} if scene_cut and not modal and next_place != place and exit_key else {}),
            "act": None if resolved else s["act"], "option": None if resolved else s["option"], "plan": None if resolved else s["plan"], "probe": None if resolved else s["probe"],
            "observation_status": "settled" if after_set.settled and s.get("before_settled", True) else "timed_out", "execution": skill.status, "action_step": s["action_step"] if resolved else None, **self._provenance(),
            **({"flags": [f"merge_pending: {self.world.merge_pending}"]} if self.world.merge_pending else {})}
        self.step_no += 1
        after.save(self.mem.root.parent / row["after"])
        drift_fix = self.world.observe_after(after_per, after, after_reused, next_place, after.size, self.step_no, skill=skill)
        row["tile"], row["drift_fix"] = self.world.pos if self.tile_px else None, list(drift_fix) if drift_fix else None
        row["self_spot"], row["self_locked"] = self.world.self_spot, self.world._self_from_motion
        self._learn(after_per.get("text", []), per.get("text", []), self.step_no, next_place, after_per.get("screen"))
        self.place, self.prev, self.prev_frame, self.cached, self.cached_hash = next_place, after_per, after, after_per, after_hash
        self.prev_frame_mark_eligible = after_mark_eligible
        self.pending_cut, self.pending_via, self.pending_key = False, None, None; reason = self.stuck.update(row)
        if any(at == self.step_no and dst == next_place for at, dst in self.new_places): self.stuck.progress()  # world progress only (Stuck.update counts first-time edges itself); text and repeat crossings never count
        if reason == "no_progress" or (self.stall_unchanged > 0 and self.stuck.progress_n >= 10): self._event("T4", "stuck: no_progress", self.step_no)
        self.repair_pending = self.parse_failed and self.empty_streak >= 8 and not self.repair_used; self.repair_used |= self.repair_pending
        if s["unknown"]: reason = "player position unknown"
        self.cause = None
        if choice is not None:
            verb, target = aff.get("verb"), aff.get("target") or ""
            failed = skill.status in ("blocked", "failed") or v == "miss" or "no_change" in outcome.get("effects", [])
            pressed_from = tuple(s["pressed_from"])
            d = next((x.upper() for x in target.split() if x.upper() in DIRS), None) if verb == "buttons" else None  # a press aims at the tile it faces
            ahead = (pressed_from[0] + DIRS[d][0], pressed_from[1] + DIRS[d][1]) if d else None
            thing = tid or next((r["id"] for r in self.mem.rows("entities") if r.get("place") == place and r.get("tile") and tuple(r["tile"]) == ahead), None)
            if failed:
                prior = self.fails.get(intent) or {}
                stage, step_dir = (getattr(skill, "failure", None) or {}).get("stage"), (getattr(skill, "failure", None) or {}).get("direction")
                reuse = refused and prior.get("avoid")  # a refusal on a first attempt has no held tile yet
                held = tuple(prior["avoid"]) if reuse else (self.world.pos[0] + DIRS[step_dir][0], self.world.pos[1] + DIRS[step_dir][1]) if step_dir in DIRS else None  # the tile a route stopped at
                blocker = prior.get("blocker") if reuse else next((r["id"] for r in self.mem.rows("entities") if r.get("place") == place and r.get("tile") and held and tuple(r["tile"]) == held and r["id"] != tid), None) if stage == "route" else None
                self.cause = failure_cause(intent, f"{verb} {target}".strip(), stage, skill.reason or ("no change" if "no_change" in outcome.get("effects", []) else f"verdict {v}"), held if stage == "route" else None, blocker)
                f = self.fails.setdefault(intent, {"n": 0}); f.update(n=f["n"] + 1, why=(skill.reason or f"verdict {v}")[:40], step=self.step_no, act=f"{verb} {target}".strip())
                if stage == "route" and blocker and held and not refused and blocker != tid: f.update(avoid=list(held), blocker=blocker)  # the next route goes around it
                evidence = [intent, stage, blocker or (list(held) if held else None)]  # a repeat of the same failure is not new evidence: one replan per change
                self.reported = evidence
                if thing and not route: self.mem.relate(thing, PRED["no_change"], "", src="observed", step=self.step_no, hit=False, verb=verb)  # the thing itself is contradicted; never deleted
            else:
                if intent in self.fails and not str(self.fails[intent].get("why", "")).endswith(": cycle"): self.fails.pop(intent)  # a success clears this intent's own counter, never another's
                if thing and (failure := next((r for r in self.mem.rows("relations") if (r.get("s"), r.get("p"), r.get("o")) == (thing, PRED["no_change"], "")), None)):
                    self.mem.upsert("relations", {**failure, "con": 0, "ev": [], "state": "live"})  # ponytail: a success clears the failure row in place (no sup accounting: predict reads it)
        if s["go_to"]: reason = s["go_to"]
        if s["no_action"] and not reason: reason = "your last reply had no valid action, so nothing happened: use one of the action shapes"
        self.flags = [x for x in (reason, f"last action {skill.status}: {skill.reason}" if skill.status != "done" else None, f"cause: {self.cause}" if self.cause else None, f"verdict {v}" if v in ("miss", "surprise") else None, self.cycle_flag, f"merge_pending: {self.world.merge_pending}" if self.world.merge_pending else None) if x]; self.cycle_flag = None; self.world.merge_pending = None; self.world.new_arrival = False
        self._after_outcome(self.step_no)
        self.planner_evidence.append({"step": row["step"], "place": place, "target": (aff or {}).get("target"), "verdict": v})
        self.planner_evidence = self.planner_evidence[-40:]
        self.trace.write(row); self.recent = [*self.recent, row][-5:]
        self.pending = None
        return row
    def _resolve(self, wait, start, calls):
        """An outer step while an action waits for its settled frame: observe only, no input, resolved once the frame settles."""
        p = self.pending
        if not wait.settled and not p.get("retries"):
            p["retries"] = p.get("retries", 0) + 1; name = f"frames/{self.step_no + 1:06d}.png"; (self.mem.root.parent / "frames").mkdir(parents=True, exist_ok=True); wait.frame.save(self.mem.root.parent / name)
            return self._hold_row(name, p["place"], start, calls, action_step=p["action_step"], retries=p["retries"])
        path = self.mem.root.parent / p["frame"]
        if not path.exists(): return self._degraded(p, wait, start, calls)
        skill = self._skill_from(p["skill"], Image.open(path).convert("RGB"), wait.frame)
        w = p["world"]  # the world state the action's own tail read: prepared at action time, not folded again
        self.world._prepared = (skill, p["place"], p["action_step"], w["cut_dir"], tuple(w["pos"]))
        if w["travelled"]: self.world._travelled = skill
        self.world._travel_open, self.world.current_place = w["travel_open"], w["current_place"]
        self._plan_row = None
        return self._settle(p, skill, wait, start, calls, resolved=True)
    def _degraded(self, p, wait, start, calls):
        """The before-frame is gone: the execution and its failure count are attributed once; no relation, edge or place evidence."""
        sk, aff = p["skill"], p["aff"] or {}
        self.prev, self.prev_frame, self.prev_frame_mark_eligible, self.cached, self.cached_hash = None, wait.frame, False, None, None
        if sk["status"] in ("blocked", "failed") and p["intent"] is not None:
            f = self.fails.setdefault(p["intent"], {"n": 0}); f.update(n=f["n"] + 1, why=(sk["reason"] or "degraded")[:40], step=self.step_no + 1, act=f'{aff.get("verb", "")} {aff.get("target", "")}'.strip())
        self.flags = [f"last action {sk['status']}: {sk['reason']}"] if sk["status"] != "done" else []
        self.pending = None
        return self._hold_row(p["frame"], p["place"], start, calls, verdict="unobservable", observation_status="settled", execution=sk["status"], action_step=p["action_step"],
                              choice=p["choice"], pred=p["pred"], skill={"status": sk["status"], "reason": sk["reason"], "inputs": sk["inputs"], "calls": sk["calls"]}, degraded=True)
    def _hold_action(self, s, skill, start, calls, *, keep):
        """The action's inputs are done (keep) or none were sent: one pending row, no evidence; keep stores the action for its resolution."""
        if keep: self.pending = {**s, "skill": self._summary(skill), "retries": 0}
        self.pending_cut, self.pending_via, self.pending_key = False, None, None
        return self._hold_row(s["frame"], s["place"], start, calls, choice=s["choice"], pred=s["pred"], act=s["act"], option=s["option"], plan=s["plan"], probe=s["probe"],
                              skill={"status": skill.status, "reason": skill.reason, "inputs": skill.inputs, "calls": skill.calls}, execution=skill.status)
    def _pre_hold(self, wait, start, calls):
        """The screen never settled before the action: no perception, no input; one pending row."""
        name = f"frames/{self.step_no + 1:06d}.png"; (self.mem.root.parent / "frames").mkdir(parents=True, exist_ok=True); wait.frame.save(self.mem.root.parent / name)
        return self._hold_row(name, self.place, start, calls)
    def _hold_row(self, frame, place, start, calls, **fields):
        """A row for an outer step whose outcome is not settled: no evidence, no counters, no stuck update."""
        step = self.step_no + 1
        row = {"step": step, "t": time.time(), "place": place, "screen": None, "task": None, "frame": frame, "after": None, "options": None,
            "choice": None, "pred": None, "used": [], "skill": None, "outcome": None, "verdict": "unobservable", "calls": self.model.calls - calls, "ms": int((time.perf_counter() - start) * 1000),
            "shortcut": None, "ranker": None, "perception_reused": False, "frame_hash": None, "marked": False, "scene_cut": False, "next_place": place, "verified_edge": False,
            "view_ids": [], "tile": self.world.pos if self.tile_px else None, "drift_fix": None, "player_fix": False, "entities": self.world.entities, "scroll": None,
            "act": None, "option": None, "plan": None, "probe": None, "observation_status": "pending", "execution": None, "action_step": None, **self._provenance(), **fields}
        self.step_no = step; self.trace.write(row); return row
    def _provenance(self):
        """Row fields tying a row to its invocation and to the model calls made during its outer step."""
        end = getattr(self.model, "seq", None)
        call_ids = [] if end is None or self._seq0 is None else list(range(self._seq0 + 1, end + 1))
        return {"invocation": self.invocation, "call_ids": call_ids, **({"provenance_incomplete": True} if self.provenance_incomplete else {})}
    @staticmethod
    def _summary(skill):
        """The checkpoint-safe part of a skill result that the tail reads; _skill_from rebuilds it."""
        return {"status": skill.status, "reason": skill.reason, "inputs": skill.inputs, "calls": skill.calls, "blank_seen": bool(skill.blank_seen), "scroll": list(skill.scroll),
                "moves": list(getattr(skill, "moves", []) or []), "self_at": list(skill.self_at) if getattr(skill, "self_at", None) else None,
                "failure": getattr(skill, "failure", None), "settled": getattr(skill, "settled", None)}
    @staticmethod
    def _skill_from(sk, before, after):
        moves = [{**m, "shift": tuple(m["shift"]) if isinstance(m.get("shift"), list) else m.get("shift")} if isinstance(m, dict) else m for m in sk["moves"]]
        return SkillResult(sk["status"], sk["reason"], sk["inputs"], sk["calls"], before, after, tuple(sk["scroll"]), sk["blank_seen"], moves,
                           tuple(sk["self_at"]) if sk["self_at"] else None, sk["failure"], sk["settled"])
    def _learn(self, texts, seen, step, place, screen="world"):
        """Store new game text and log dialogue once when its screen sequence ends."""
        known = {_key(r.get("text", "")) for r in self.mem.rows("knowledge")}
        fresh = [t for t in texts if t not in seen and _key(t) not in known]
        for text in (t for t in texts if t not in seen): self.mem.know(text, src="game_text", step=step)
        if self.conversation["lines"] and (screen == "world" or place != self.conversation["place"]):
            self._flush_conversation(step)
        if screen == "menu": return  # menu text is knowledge only: no conversation, no trigger
        if screen == "world":
            if fresh:
                self.text_for_trigger = " / ".join(fresh)
                self.notebook.note(step, f"text at {self.world.place_label(place) if place else '?'}: " + " / ".join(f'"{t}"' for t in fresh)); self._event("T1", f"new game text: {self.text_for_trigger[:200]}", step)
            return
        if not self.conversation["lines"]: self.conversation["place"] = place
        lines = self.conversation["lines"]
        for text in fresh:
            line = " ".join(str(text).replace("▼", "").replace("▶", "").split())
            if not line: continue
            if lines and (line.startswith(lines[-1]) or lines[-1].startswith(line)): lines[-1] = max(lines[-1], line, key=len)
            elif lines:
                previous = lines[-1]
                overlap = next((n for n in range(min(len(previous), len(line)), 0, -1) if previous[-n:] == line[:n]), 0)
                if overlap: lines[-1] = previous + line[overlap:]
                else: lines.append(line)
            else: lines.append(line)

    def _flush_conversation(self, step):
        lines = self.conversation["lines"]
        if not lines: return
        joined = " ".join(lines)
        match = re.match(r"^([A-Z][A-Za-z.' -]{0,19}):\s*", joined)
        speaker = match.group(1) if match and sum(c.isalpha() for c in match.group(1)) >= 2 else None
        if speaker: joined = joined[match.end():]
        place = self.conversation["place"]
        self.text_for_trigger = joined
        self.notebook.note(step, f'{speaker or "text"} at {self.world.place_label(place) if place else "?"}: "{joined}"')
        self._event("T1", f"new game text: {joined[:200]}", step)
        self.conversation = {"place": None, "lines": []}
    def _screen_route(self, place, t, push, avoid=()):  # to an on-screen entity at tile t; an actor holds its tile, so stop beside it, facing it; avoid: tiles a route blocker held
        occupied = self.world.occupied(place)
        if t not in occupied:
            dirs = route(self.mem, place, self.pos, t, occupied=occupied | set(avoid))
            return {"dirs": dirs, "push": push} if dirs is not None else None
        found = self.world.beside(place, self.pos, t)
        return {"dirs": found[2] + [found[1]], "push": push} if found else None
    def _unexplored(self, place):
        """Route to the nearest untried edge (edges into unseen cells first), plus the step across it."""
        if not self.tile_px: return None
        seen = set((self.mem.get("seen", place) or {}).get("cells", []))
        past = [tuple(map(int, c.split(","))) for c in open_cells(self.mem, place)]
        best, occupied = None, self.world.occupied(place)
        for (x, y), d in frontier(self.mem, place):
            if ((self.mem.get("tiles", f"{place}@{x},{y}") or {}).get("exit") or {}).get("dir") == d: continue  # an exit, even inferred, is not unexplored (m1_gp2: walked back through the front door)
            if (x + DIRS[d][0], y + DIRS[d][1]) in occupied: continue  # the appended step would walk into an actor
            dirs = route(self.mem, place, self.pos, (x, y), occupied=occupied)
            if dirs is None: continue
            key = (f"{x + DIRS[d][0]},{y + DIRS[d][1]}" in seen, min((abs(x - c[0]) + abs(y - c[1]) for c in past), default=0), len(dirs))  # toward open screen edges
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
    def _fail_key(self, place, verb, target, ref=None):
        """The intent key (World's fail_key): place | family | the resolved entity, exit or place (ref), else the target's name."""
        return fail_key(place, verb, target, self.env.inputs(), ref)
    def _migrate(self, fails):  # older checkpoints: name-keyed counters stay as history; one that names exactly one intent moves there; merged places fold in, the larger count kept
        out, legacy = {}, []
        def put(key, f):
            if key not in out or f.get("n", 0) > out[key].get("n", 0): out[key] = f
        for key, f in fails.items():
            place, _, rest = key.partition("|")
            if "|" in rest: put(f"{self.world.canon_place(place)}|{rest}", f)
            elif str(f.get("act", "")).startswith("buttons "): put(f"{self.world.canon_place(place)}|buttons|{rest}", f)  # a press key kept its subject: one intent
            else: legacy.append((self.world.canon_place(place), rest, f))
        for place, name, f in legacy:
            names = self.world.named_intents(place, name)
            put(next(iter(names)) if len(names) == 1 else f"{place}|{name}", dict(f))
        return out
    def _moved(self, src, dst, via, step, key=None):
        self.cycle_flag = None; self.entered[dst] = step
        label = self.world.place_label
        self.notebook.note(step, f"went from {label(src)} to {label(dst)}" + (f" via {via}" if via else ""))
        self.moves = [*self.moves, [step, src, dst, via or "", key]][-6:]
        last = self.moves[-4:]  # A<->B four times running, however slowly; progress (a focus closing) clears self.moves
        if len(last) == 4 and len({(m[1], m[2]) for m in last}) == 2 and all(a[2] == b[1] for a, b in zip(last, last[1:])):
            self.cycle_flag = f"cycle: went back and forth between {label(src)} and {label(dst)} {len(last)} times without progress"; self.moves = []
            for m in last:  # act sees, in each of the two places, that the way it keeps taking only leads back
                if m[3]: f = self.fails.setdefault((m[4] if len(m) > 4 else None) or self._fail_key(m[1], "", m[3]), {"n": 0}); f.update(n=f["n"] + 1, why=f"leads back to {label(m[2])}: cycle", step=step)
        elif (self.mem.get("places", dst) or {}).get("visits", 0) <= 1 and not any(place == dst for _, place in self.new_places):
            self._event("T2", f"arrived somewhere new: {label(dst)}", step)
            self.new_places.append((step, dst))
    def _goal_text(self):
        quest = next((q for q in self.notebook.data["quests"] if q.get("status") == "open"), None)
        return " ".join(str(quest.get(k, "")) for k in ("text", "approach") if quest and quest.get(k))
    def _event(self, kind, text, step):  # T4 and T5 wait at most once
        if kind in ("T4", "T5") and any(e["kind"] == kind for e in self.events): return
        self.events.append({"kind": kind, "text": text, "step": step})
    def _closed(self, keys, step, top):  # a quest closed: progress, one T3 event, and a chapter when it was the top or heard
        if not keys: return
        self._event("T3", f"quest {', '.join(keys)} closed", step); self.stuck.progress(); self.moves = []
        quests = [q for q in self.notebook.data["quests"] if q.get("key") in keys]
        self._chapter_due |= any(q.get("key") == top or self.notebook._heard_quest(q) for q in quests)
    def _apply_plan(self, p, trigger, step, top_before=None):
        old = {q.get("key"): q.get("status") for q in self.notebook.data["quests"]}
        result = self.notebook.apply_plan(p, step)
        self._closed([q.get("key") for q in self.notebook.data["quests"] if q.get("status") in FINISHED and old.get(q.get("key")) not in FINISHED], step, top_before)
        if "stuck:" in trigger and not (result["quests_changed"] or result["beliefs_changed"]):
            self.stall_unchanged += 1
            if self.stall_unchanged >= 2:
                self.stall_unchanged = 0
                fallback = {"key": "explore_untried_exit", "text": "explore an exit or area not yet tried", "approach": "Try an exit or area absent from the evidence packet.", "done_when": "reach a new area or verify a route", "status": "open", "why": "stall"}
                norm = lambda t: " ".join(re.sub(r"[^\w\s]", " ", str(t).casefold()).split())
                if not any(q.get("key") == fallback["key"] or norm(q.get("text")) == norm(fallback["text"]) for q in self.notebook.data["quests"]):
                    extra = self.notebook.apply_plan(SimpleNamespace(quests=[fallback], beliefs=[], facts=[], note="", why="stall", focus=""), step)  # only the fallback: the plan is not applied twice
                    result = {**result, "quests_changed": result["quests_changed"] + extra["quests_changed"], "flags": [*result["flags"], *extra["flags"]]}
        elif result["quests_changed"] or result["beliefs_changed"]:
            self.stall_unchanged = 0
        self.plan_flags = result["flags"]
        return result
    def _evidence(self):
        since = self.last_plan or 0
        rows = [r for r in self.planner_evidence if r.get("step", 0) > since]
        if not rows: rows = [r for r in self.recent if r.get("step", 0) > since]
        places = [label for at, place in self.new_places if at > since for label in [self.world.place_label(place)]]
        tried = {}
        for row in rows:
            choice = row.get("choice") or {}
            target = row.get("target") or choice.get("target") or choice.get("go_to")
            if target:
                counts = tried.setdefault(self.world.place_label(row.get("place")), {}).setdefault(str(target), {})
                outcome = str(row.get("verdict") or "tried")
                counts[outcome] = counts.get(outcome, 0) + 1
        heard = [str(e.get("text", "")) for e in self.notebook.log if e.get("step", 0) > since and " at " in str(e.get("text", ""))]
        closed = [f"{q.get('key')} at {item.get('step')}" for q in self.notebook.data["quests"]
                  for item in q.get("check_history", []) if item.get("result") == "pass" and item.get("step", 0) > since]
        failed = [f"{self.world.place_label(str(key).split('|', 1)[0])}: {value.get('act') or key} -> {value.get('why', 'failed')} x{value.get('n', 1) - self.fail_baseline.get(key, 0)}"
                  for key, value in self.fails.items() if value.get("n", 0) > self.fail_baseline.get(key, 0)]
        moved = {}
        for at, src, dst, via, *_ in self.moves:
            if at <= since: continue
            exits = moved.setdefault(self.world.place_label(src), {})
            exits[via or "unknown"] = exits.get(via or "unknown", 0) + 1
        def fit(label, items, cap, sep=", "):  # whole entries only, newest first, the line within cap characters
            kept, n = [], len(label) + 1
            for item in items:
                if n + len(item) > cap: break
                kept.append(item); n += len(item) + len(sep)
            return f"{label} {sep.join(kept) or ('none' if not items else f'({len(items)} too long)')}"
        dump = lambda k, v: f"{k}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
        lines = [fit("new places:", places[::-1], 60), fit("tried by place:", [dump(k, v) for k, v in reversed(tried.items())], 100),
                 fit("moves:", [dump(k, v) for k, v in reversed(moved.items())], 60), fit("failed attempts:", failed[::-1], 60, "; "),
                 fit("new text since last plan:", heard[::-1], 80, " / "), fit("closed checks:", closed[::-1], 55),
                 f"steps since last progress: {self.stuck.progress_n} (stall at 20)",
                 fit("reconcile flags:", self.plan_flags[::-1], 80, "; ") if self.plan_flags else "",
                 "last stall changed nothing; revise a belief or reorder" if self.stall_unchanged else ""]
        lines = [line for line in lines if line]
        while len("\n".join(lines)) > 600: lines.pop()
        return "\n".join(lines)
    def _after_outcome(self, step):
        top = self.notebook.focus
        self._closed(self.notebook.run_checks(step), step, top)
        notes = len(self.notebook.data["notes"])
        if self._chapter_due or (notes > 12 and notes >= self.chapter_after):
            if not self.notebook.apply_chapter(chapter(self.model, self.notebook), step): self.chapter_after = notes + 6  # a failed call waits for 6 more notes
        self._chapter_due = False
    def _plan_trigger(self, per):
        """Replan once the three-step floor has passed and an event is waiting (T1 text only on the world screen); no open quest queues one."""
        if self.notebook.focus is None: self._event("T5", "no open quest", self.step_no + 1)
        if self.last_plan is not None and self.step_no + 1 - self.last_plan < 3: return None
        if not any(e["kind"] != "T1" or per.get("screen") == "world" for e in self.events): return None
        return "; ".join(e["text"] for kind in ("T3", "T1", "T2", "T5", "T4") for e in self.events if e["kind"] == kind)
    def state(self):
        state = {"step": self.step_no, "place": self.place, **self.world.state(), "tile_px": self.tile_px, "move_frames": self.move_frames, "stuck": self.stuck.state(), "reperception_used": self.reperception_used, "perception": self.cached, "frame_hash": self.cached_hash, "perception_marked": self.cached_marked, "frame_mark_eligible": self.prev_frame_mark_eligible, "perception_version": self.cached_version, "parse_failed": self.parse_failed, "empty_streak": self.empty_streak, "repair_used": self.repair_used, "repair_pending": self.repair_pending, "pending_cut": self.pending_cut, "pending_via": self.pending_via, "pending_key": self.pending_key, "pending": self.pending, "flags": self.flags, "planner": {"last": self.last_plan, "events": self.events, "entered": self.entered, "chapter_after": self.chapter_after, "fails": self.fails, "moves": self.moves, "stall_unchanged": self.stall_unchanged, "plan_flags": self.plan_flags, "cycle_flag": self.cycle_flag, "new_places": self.new_places, "planner_evidence": self.planner_evidence, "fail_baseline": self.fail_baseline, "conversation": self.conversation, "text_for_trigger": self.text_for_trigger, "cause": self.cause, "reported": self.reported}}
        if not self.provenance_incomplete: state["modes"] = {"decide": bool(self.decide), "act_text": bool(self.act_text)}  # after a legacy restore the modes are not written: they were never recorded
        return state
    def _old_events(self, planner, state):  # older checkpoints kept the triggers as loose flags: convert once
        step, out = int(state.get("step", 0)), []
        if planner.get("focus_closed"): out.append({"kind": "T3", "text": str(planner["focus_closed"]), "step": step})
        if planner.get("text_pending"): out.append({"kind": "T1", "text": f"new game text: {str(planner.get('text_for_trigger', ''))[:200]}", "step": step})
        if str(planner.get("arrived") or "").startswith("arrived somewhere new:"): out.append({"kind": "T2", "text": planner["arrived"], "step": step})
        if planner.get("stall_pending"): out.append({"kind": "T4", "text": "stuck: no_progress", "step": step})
        return out
    def restore(self, state):
        modes = state.get("modes")
        if modes is not None:  # checked before anything is restored: a conflict changes nothing
            saved, current = {"decide": bool(modes.get("decide")), "act_text": bool(modes.get("act_text"))}, {"decide": bool(self.decide), "act_text": bool(self.act_text)}
            if saved != current: raise ValueError(f"loop modes {current} conflicts with checkpoint modes {saved}")
        self.provenance_incomplete = modes is None
        self.step_no, self.place = int(state.get("step", 0)), state.get("place")
        self.tile_px, self.move_frames = state.get("tile_px"), int(state.get("move_frames", 16))
        self.world.tile_px = self.tile_px; self.world.restore(state)
        self.stuck.restore(state.get("stuck", {})); self.prev = state.get("perception"); self.prev_frame = self.env.screenshot()
        self.prev_frame_mark_eligible = state.get("frame_mark_eligible")
        self.reperception_used = bool(state.get("reperception_used", False))
        digest = hashlib.sha256(self.prev_frame.tobytes()).hexdigest()
        self.cached, self.cached_hash, self.cached_marked, self.cached_version = state.get("perception"), state.get("frame_hash"), bool(state.get("perception_marked", False)), state.get("perception_version")
        self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = bool(state.get("parse_failed")), int(state.get("empty_streak", 0)), bool(state.get("repair_used")), bool(state.get("repair_pending")); self.pending_cut, self.pending_via, self.pending_key = bool(state.get("pending_cut")), state.get("pending_via"), state.get("pending_key"); self.flags = state.get("flags", [])
        planner = state.get("planner") or {}; self.last_plan = planner.get("last")
        self.fails, self.moves = self._migrate(planner.get("fails") or {}), planner.get("moves") or []
        self.entered, self.chapter_after = planner.get("entered") or {}, int(planner.get("chapter_after", 0))
        self.events = [dict(e) for e in planner["events"]] if isinstance(planner.get("events"), list) else self._old_events(planner, state)
        self.stall_unchanged, self.plan_flags, self.cycle_flag = int(planner.get("stall_unchanged", 0)), planner.get("plan_flags") or [], planner.get("cycle_flag")
        self.new_places = planner.get("new_places") or []
        self.planner_evidence = planner.get("planner_evidence") or []
        self.fail_baseline = planner.get("fail_baseline", {key: value.get("n", 0) for key, value in self.fails.items()} if self.last_plan is not None else {})
        self.conversation, self.text_for_trigger = planner.get("conversation") or {"place": None, "lines": []}, planner.get("text_for_trigger", "")
        self.cause, self.reported = planner.get("cause"), planner.get("reported")  # checkpoints before these fields: None
        self.pending = state.get("pending")  # checkpoints before this field: no pending action
        if self.cached_version != self.prompt_version or digest != self.cached_hash: self.cached = self.cached_hash = None; self.cached_marked = False
