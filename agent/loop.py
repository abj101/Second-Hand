"""One game-neutral perception, choice, action, and learning cycle."""
import hashlib, json, os, re, time
from PIL import Image
from agent.act import act
from agent.brief import GENERAL, _load, load_brief
from agent.decide import Choice
from agent.memory import _key
from agent.notebook import Notebook
from agent.observer import _blank, cut, for_model, snap, wait_settled
from agent.perceive import perceive
from agent.plan import plan
from agent.predict import PRED, effects, result as action_result
from agent.skills import Ctx, SkillResult, press_seq, run_affordance, wait_until
from agent.tasks import STALL, Stuck
from agent.trace import Trace
from agent.space import DIRS, outward, push_dirs, route
from agent.world import World, fail_key
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
        self.recent = self.trace.tail(5); self.cached = None; self.cached_hash = None; self.cached_marked = False; self.prev_frame = None; self.prev_frame_mark_eligible = None; self.prompt_version = "e5-perceiver-11"; self.cached_version = self.prompt_version
        self.seen_image, self.seen_marked = None, False
        self.cut_carry = False  # a cut seen while no world screen could be matched: the next world screen re-identifies the place
        self.pending_cut, self.pending_via, self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = False, None, False, 0, False, False
        self.pending_key = None  # intent key of the last action, for a cycle mark on the move it caused
        self.cause = None  # failure_cause text of the last failed act (planner and chooser read it)
        self.reperception_used = False  # ponytail: read only by invalidate_live_observation (live view); remove with it
        self.world = World(mem, self.tile_px); self.world.tracker.wrap(env)
        self.observation_stamp = self.observation_age_ms = None; self.live_invalidation_reason = None
        self.events, self.t5_armed = [], False  # t5_armed: T5 fires once per no-quest period, armed by a new T1/T2/T3 (nothing heard yet: nothing a plan could cite)
        self.notebook = Notebook(mem.root, ref=self.world.ref, canon=self.world.canon, label=self.world.label, ties=self.world.ties, route=self.world.exit_route, is_exit=self.world.is_exit, leads_to=self.world.leads_to, name=lambda ident, text, key, step: self.mem.call(ident, text, step, tier="text", key=key),
                                 refs=lambda: [f"place:{p['id']}" for p in mem.rows("places") if p.get("state") != "merged"] + [f"thing:{e['id']}" for e in mem.rows("entities") if e.get("state") != "merged"])
        self.last_plan, self._plan_row = None, None
        self.conversation, self.text_for_trigger = {"place": None, "lines": []}, ""
        self.fails, self.moves = {}, []
        self.new_places = []
        self.planner_evidence, self.fail_baseline = [], {}
        self.pending = None  # ponytail: read only by invalidate_live_observation (live view); remove with it
        self.unsettled = 0  # ponytail: read only by invalidate_live_observation (live view); remove with it
        self.invocation = None  # the runner's manifest invocation number (set by scripts/run_agent.py)
        self.provenance_incomplete = False  # restored from a checkpoint with no recorded modes: earlier calls and modes are unknown
        self._seq0 = None  # model.seq when the current outer step began: its model calls are (_seq0, seq]
    @property
    def pos(self): return self.world.pos
    @pos.setter
    def pos(self, value): self.world.pos = tuple(value)
    def invalidate_live_observation(self):
        """Discard pixel-dependent state after a live generation change or resume."""
        if not callable(getattr(self.env, "snapshot", None)): return
        had_pending = self.pending is not None
        self.cached = self.cached_hash = self.cached_version = None
        self.cached_marked = False; self.seen_image, self.seen_marked = None, False
        self.prev = self.prev_frame = None; self.prev_frame_mark_eligible = None
        last = self.mem.root.parent / "frames" / f"{self.step_no:06d}_after.png"  # what the agent last saw: the gap since then is checked like any step
        if last.exists(): self.prev_frame = Image.open(last).convert("RGB")
        else: self.cut_carry = True  # nothing to compare: re-identify at the next world screen
        self.pending = None; self.pending_cut = False; self.pending_via = self.pending_key = None
        self.repair_pending = False; self.parse_failed = False; self.empty_streak = self.repair_used = 0
        self.reperception_used = False; self.unsettled = 0; self.observation_stamp = self.observation_age_ms = None
        self.world.tracker.clear(); self.world._observed_per = self.world._prepared = None
        self.world.tracker.phase = None; self.world.tracker.ignore = []
        self.world.drift = self.world.drift_ref = None
        for hud in self.world._hud.values(): hud["crop"] = None
        self.world.self_spot, self.world._self_contradictions, self.world._self_from_motion = None, 0, False
        self.world.pa, self.world.actor_tiles, self.world._travelled = None, frozenset(), None
        self.world._cut_arrival = self.world._lone = None
        self.live_invalidation_reason = "live_resume_cleared_pending" if had_pending else "live_resume_invalidated_pixels"
    def _observation_fields(self, stamp=None):
        stamp = stamp or self.observation_stamp
        if not stamp: return {}
        age = self.observation_age_ms if stamp is self.observation_stamp and self.observation_age_ms is not None else max(0, int((time.monotonic() - stamp["captured_monotonic"]) * 1000))
        return {"observation_generation": stamp["generation"], "observation_frame_seq": stamp["frame_seq"], "observation_age_ms": age}
    def _live_observation_stale(self, frame, stamp):
        current = self.env.snapshot()
        age = max(0, int((time.monotonic() - stamp["captured_monotonic"]) * 1000))
        self.observation_age_ms = age
        changed_scene = current.generation != stamp["generation"] or cut(frame, current.image, _blank(current.image) and not _blank(frame))  # a mostly-white menu is "blank" but unchanged
        return changed_scene, current, age
    def _see(self, frame, previous, *, force=False, screen_was_world=False):
        key = hashlib.sha256(frame.tobytes()).hexdigest()
        image, marked = self.world.mark(frame, screen_was_world)
        self.seen_image, self.seen_marked = image, marked
        if not force and key == self.cached_hash and marked == self.cached_marked and self.cached is not None and self.cached_version == self.prompt_version and not self.repair_pending: return self.cached, key, True, []
        ctx = GENERAL + "\n" + load_brief(self.game) + ("\nThe previous JSON was empty or malformed. Retry with compact JSON containing string screen and list text and entities; no prose. Use empty lists only for genuinely absent content and report visible details only." if self.repair_pending else "")
        if marked: ctx += "\n" + CAPTION
        per, reply = perceive(self.model, for_model(image), ctx); data = reply.data
        if data is None:
            try: json.loads(getattr(reply, "text", ""))
            except json.JSONDecodeError as parse_error: error = parse_error
            else: error = ValueError("expected a JSON object")
            per, reply = perceive(self.model, for_model(image), ctx + f"\nYour last reply was not valid JSON: {str(error)[:120]}. Reply again with compact valid JSON."); data = reply.data
        valid = isinstance(data, dict) and isinstance(data.get("screen"), str) and isinstance(data.get("text"), (list, str)) and isinstance(data.get("entities"), list)
        self.cached, self.cached_hash, self.cached_marked, self.cached_version = per, key, marked, self.prompt_version; self.repair_pending = False; self.parse_failed = not valid or not any((per["screen"], per["text"], per["entities"]))
        return per, key, False, []
    def step(self):
        start, calls, self._seq0 = time.perf_counter(), self.model.calls, getattr(self.model, "seq", None)
        before_set = wait_settled(self.env); before = before_set.frame; prior = self.place
        self.observation_stamp = dict(before_set.frame_stamp) if before_set.frame_stamp else None
        self.observation_age_ms = None
        before_mark = bool(self.prev and self.prev.get("screen") == "world" and self.prev_frame is not None and not cut(self.prev_frame, before, before_set.blank_seen))
        if self.prev_frame is not None and hashlib.sha256(before.tobytes()).hexdigest() == hashlib.sha256(self.prev_frame.tobytes()).hexdigest() and self.prev_frame_mark_eligible is not None:
            before_mark = self.prev_frame_mark_eligible
        per, frame_hash, reused, _ = self._see(before, prior, screen_was_world=before_mark); marked = self.seen_marked
        player_fix = self.world.fix_player(per, before.size); self.empty_streak = self.empty_streak + 1 if self.parse_failed else 0; self.repair_used = self.repair_used if self.parse_failed else False
        modal = self.prev and {self.prev.get("screen"), per.get("screen")} <= {"world", "dialogue", "menu"} and self.prev.get("screen") != per.get("screen") and "world" in (self.prev.get("screen"), per.get("screen")) and not (self.pending_cut or self.cut_carry or before_set.blank_seen)
        place = self.mem.match_place(per, self.step_no + 1, prev=prior, cut=(is_cut := bool((self.pending_cut or self.cut_carry or prior and self.prev_frame is not None and cut(self.prev_frame, before, before_set.blank_seen)) and not modal)), via=self.pending_via, frame=before, tile_px=self.tile_px)
        unseen_crossing = None
        if prior and place != prior:
            crossings = {r["id"] for r in self.mem.rows("crossings")}
            self.world.unseen_cross(prior, place, self.step_no + 1)
            unseen_crossing = next((r["id"] for r in self.mem.rows("crossings") if r["id"] not in crossings), None)
            self._moved(prior, place, self.pending_via, self.step_no + 1, self.pending_key)
        self.place, self.pending_cut, self.pending_via, self.pending_key = place, False, None, None
        self.cut_carry = is_cut and per.get("screen") != "world"  # match_place keeps the place on a non-world screen: the cut waits for a world one
        size = before.size
        proj = self.world.before(per, place, prior, size, self.step_no + 1)[0]
        drift_fix = None
        probe = None
        if per.get("screen") == "world" and not modal and not self.world._self_from_motion and self.tile_px:
            probe_ctx = Ctx(self.env, self.model, self.tile_px, self.move_frames); probe_ctx.step = self.step_no + 1
            probe = self.world.probe(probe_ctx)
            if probe["presses"]:
                probe_set = wait_settled(self.env); probe_frame = probe_set.frame
                self.observation_stamp = dict(probe_set.frame_stamp) if probe_set.frame_stamp else None
                self.observation_age_ms = None
                probe_per, probe_hash, probe_reused, _ = self._see(probe_frame, place, force=True, screen_was_world=per.get("screen") == "world" and not probe["scene_cut"]); marked = self.seen_marked
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
            events, self.events = self.events, []
            evidence = self._evidence()
            recent = [f"{(r.get('choice') or {}).get('verb')} {(r.get('choice') or {}).get('target') or ''} -> {r.get('verdict')}" for r in self.recent]
            p = plan(self.model, trigger, self.notebook, view, per.get("text", []), recent, evidence=evidence)
            self.new_places, self.planner_evidence = [], []
            results = self.notebook.apply_ops(p.ops, self.step_no + 1, p.why)
            if p.ops and self.notebook.focus is None and any(r["result"] != "ok" or r["op"] in ("done", "drop") for r in results):
                p = plan(self.model, "no open quest", self.notebook, self.world.view("", place), per.get("text", []), recent)
                more = self.notebook.apply_ops(p.ops, self.step_no + 1, p.why); results += more
                if p.ops and self.notebook.focus is None and all(r["result"] == "ok" for r in more):  # ok, but its quest was already accomplished
                    p = plan(self.model, "no open quest", self.notebook, self.world.view("", place), per.get("text", []), recent)
                    results += self.notebook.apply_ops(p.ops, self.step_no + 1, p.why)
            changed = any(r["result"] == "ok" for r in results)
            if trigger.startswith("stuck:"): self.stuck.replanned(changed)
            self.last_plan = self.step_no + 1
            self.fail_baseline = {key: value.get("n", 0) for key, value in self.fails.items()}
            self._plan_row = {"trigger": trigger, "events": events, "focus": self.notebook.focus, "ops": results, "why": p.why}
            view = self.world.view(self._goal_text(), place)
        affs, targets, intents = [], [], []  # intents: each affordance's intent key, resolved before input
        choice, player = None, next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        act_route = travel_legs = None; refused = False  # refused: no route to an on-screen target avoids the known blockers (occupied tiles, remembered avoid tile)
        options = self.world.options(per, place, self.fails, self.step_no + 1, inputs=list(self.env.inputs()),
                                     targets=(q["target"],) if (q := self.notebook.focused_quest()) and q.get("target") else ())  # failure history is in the option notes
        recent = [f"step {r.get('step')}: {(r.get('choice') or {}).get('verb', '')} {(r.get('choice') or {}).get('target') or ''} -> {r.get('verdict')}" for r in self.recent[-4:]]
        place_row = self.mem.get("places", place) or {}; here = self.world.here(place)
        if place_row.get("name"): here += " (a name read from the picture)"
        focus = f"{self.notebook.render_focus(self.world.label, recent=recent)}\nHere: {here}"
        a = act(self.model, per, focus, options, list(self.env.inputs()), "\n".join(_load(self.game).get("controls", [])))
        picked = next((o for o in options if o["n"] == getattr(a, "option", None)), None)
        own_pick = picked if picked and picked.get("action") == a.action else None  # the option act picked, when its action is the one it answered with
        if per.get("screen") == "world" and not modal and self.tile_px: self.mem.remember_view(place, self.world.pos, before)
        action, i = a.action, None
        if action and "go_to" in action and action["go_to"].casefold().startswith("unexplored"):
            act_route = self._unexplored(place, action.get("dir"))
            if act_route and player: affs.append({"verb": "approach", "target": "unexplored", "at": player.get("at")}); targets.append(None); intents.append(self._fail_key(place, "approach", "unexplored")); i = len(affs) - 1
            else: act_route = action = None
        if action and "go_to" in action and act_route is None:
            resolved = self.world.resolve(action["go_to"], per, place); e = next((e for n, e in enumerate(per.get("entities", [])) if self.world.entity_ids.get(n) == resolved.get("id")), None) if resolved and resolved["kind"] == "screen" else None
            if resolved and (e or resolved["kind"] != "screen"):
                ref = f"{'place' if resolved['kind'] == 'place' else 'thing'}:{resolved['id']}" if resolved.get("id") else None  # the name resolved to what it names
                affs.append({"verb": "approach", "target": (e or {}).get("name", action["go_to"]), "at": (e or {}).get("at")}); targets.append(resolved.get("id")); intents.append(self._fail_key(place, "approach", affs[-1]["target"], ref=ref)); i = len(affs) - 1
                if action.get("push"): intents[-1] += f">{action['push']}"
                t, act_route, refused = self._approach_route(place, e, player, proj, intents[-1], push=action.get("push"))
                travel_legs = self.world.plan(resolved, exit=own_pick.get("exit") if own_pick else None, push=action.get("push")) if not e else None; action = None if travel_legs is None and not e else action
            else: action = None
        if action and "buttons" in action:
            affs.append({"verb": "buttons", "target": " ".join(action["buttons"])}); targets.append(None); intents.append(self._fail_key(place, "buttons", affs[-1]["target"])); i = len(affs) - 1
        if action and "thing" in action:
            n, verb = action["thing"], action.get("verb")
            ident, entity = None, None
            if type(n) is int and 0 <= n < len(per.get("entities", [])):
                entity, ident = per["entities"][n], self.world.entity_ids.get(n)
            elif isinstance(n, str) and n.startswith("thing:"):
                ref = self.world.canon(n)
                if ref and ref.startswith("thing:"):
                    ident = ref.partition(":")[2]; row = self.mem.get("entities", ident)
                    if row and row.get("place") == place and row.get("status") != "gone":
                        anchor = snap(self.world.pa, self.world.size, self.tile_px) if row.get("tile") and self.world.pa and self.tile_px and self.world.size else None
                        at = [anchor[i] + (row["tile"][i] - self.world.pos[i]) * self.tile_px / self.world.size[i] for i in (0, 1)] if anchor else None
                        entity = {**row, "at": at}
            if entity and verb in ("approach", "interact", "select", "click"):
                aff = {"verb": verb, "target": entity.get("name", ""), "at": entity.get("at")}
                affs.append(aff); targets.append(ident); intents.append(self._fail_key(place, verb, aff["target"], ref=f"thing:{ident}" if ident else None)); i = 0
                if action.get("push"): intents[-1] += f">{action['push']}"
                if verb == "approach":
                    t, act_route, refused = self._approach_route(place, entity, player, proj, intents[-1], push=action.get("push"))
                elif verb == "interact" and self.tile_px and player and not self.env.pointer:
                    t, act_route, refused = self._approach_route(place, entity, player, proj, intents[-1], beside=True)
            else: action = None
        if action and action.get("verb") in ("advance", "cancel", "type"):
            aff = {"verb": action["verb"], "target": "", "at": None}
            affs.append(aff); targets.append(None); intents.append(self._fail_key(place, action["verb"], "")); i = 0
        if action: choice = Choice(i, "screen_changes", "", [], a.why, True, a.text)
        intent = ((own_pick or {}).get("intent_key") or intents[choice.i]) if choice else None  # captured before input: the same key execution, counters and trace use
        unknown = bool(choice and affs[choice.i].get("verb") in ("approach", "interact") and not self.env.pointer and
                       not any(e.get("kind", "").casefold() == "player" for e in per.get("entities", [])))
        if choice and self.observation_stamp:
            stale, current, age = self._live_observation_stale(before, self.observation_stamp)
            if stale:
                name = f"frames/{self.step_no + 1:06d}.png"; path = self.mem.root.parent / name
                path.parent.mkdir(parents=True, exist_ok=True); before.save(path)
                self.cached = self.cached_hash = None
                return self._hold_row(name, place, start, calls, observation_status="stale", execution="stale",
                    settle_frames={"before": before_set.waited, "after": None},
                    observation_generation=self.observation_stamp["generation"], observation_frame_seq=self.observation_stamp["frame_seq"],
                    observation_age_ms=age, stale_reason="scene_cut" if current.generation == self.observation_stamp["generation"] else "generation_changed")
        self._live(before, place, per, choice and {**affs[choice.i], "why": choice.why, "text": choice.text}, a)
        if per.get("screen") == "world": self.world._record_view(per, before, reused, place, size, self.step_no + 1, self.world.pa, self.world.entity_ids)
        def actor_boxes():
            tracker = self.world.tracker
            if tracker.arr is None: return ()
            h, w = tracker.arr.shape[:2]; r = (self.tile_px or tracker.tile_px) / 2
            player = self.world.self_spot
            px, py = (player[0] * w, player[1] * h) if player else (None, None)
            return tuple((max(0, int(t["x"] - r)), max(0, int(t["y"] - r)),
                          min(w, int(t["x"] + r)), min(h, int(t["y"] + r)))
                         for t in tracker.tracks.values() if t["status"] == "live" and t["actor"] and
                         (px is None or max(abs(t["x"] - px), abs(t["y"] - py)) > r))
        skillctx = Ctx(self.env, self.model, self.tile_px, self.move_frames, actor_boxes=actor_boxes); pressed_from = tuple(self.world.pos)
        try:
            skill = SkillResult("blocked", "no route beside target" if choice and affs[choice.i].get("verb") == "interact" else "no route avoids blocker", 0, 0, before, before, (0, 0), False, failure={"stage": "route", "reason": "no route beside target" if choice and affs[choice.i].get("verb") == "interact" else "no route avoids blocker", "direction": None}) if refused else self.world.travel(skillctx, travel_legs, self.step_no + 1, self.trace) if travel_legs is not None and choice else wait_until(skillctx) if not affs or choice is None else press_seq(skillctx, affs[choice.i]["target"].split()) if affs[choice.i].get("verb") == "buttons" else run_affordance(skillctx, affs[choice.i], per, text=choice.text, exit=False, **({"route": act_route} if act_route else {}))
        except BaseException: self.pending_cut, self.pending_via, self.pending_key = cut(before, self.env.screenshot()), affs[choice.i].get("target") if choice else None, intent; self.cached = self.cached_hash = None; raise
        if choice and skill.status == "done" and affs[choice.i].get("verb") in ("approach", "interact") and choice.i < len(targets) and targets[choice.i]:
            self.world._promote([targets[choice.i]])
        self.tile_px = skillctx.tile_px; self.world.tile_px = self.tile_px
        scene_cut = self.world._travel_cut if travel_legs is not None else skill.blank_seen or skill.reason == "scene cut" or (cut(skill.before, skill.after) and (self.env.pointer or not choice or affs[choice.i].get("verb") not in ("approach", "interact") or skill.scroll == (0, 0)))
        spatial = self.world.prepare(skill, place, scene_cut, self.step_no + 1); exit_key, cut_dir = spatial["exit_key"], spatial["cut_dir"]
        self.pending_cut, self.pending_via, self.pending_key = scene_cut, affs[choice.i].get("target") if choice else None, intent
        row_choice = {"i": choice.i, **affs[choice.i], "id": targets[choice.i], "called": choice.called, "text": choice.text, "why": choice.why, "intent": intent} if choice else None
        # the action's state, JSON-safe: values used by the outcome tail below
        s = {"action_step": self.step_no + 1, "before_settled": before_set.settled, "before_waited": before_set.waited, "frame": f"frames/{self.step_no + 1:06d}.png", "place": place, "per": per, "reused": reused, "frame_hash": frame_hash, "marked": marked, "observation_stamp": self.observation_stamp,
             "player_fix": player_fix, "options": len(affs), "choice": row_choice, "aff": affs[choice.i] if choice else None, "tid": targets[choice.i] if choice else None,
             "detail": choice.detail if choice else None, "intent": intent, "unknown": unknown, "scene_cut": scene_cut,
             "exit_key": list(exit_key) if exit_key else None, "unseen_crossing": unseen_crossing, "cut_dir": cut_dir, "travel": travel_legs is not None, "route_len": len(act_route["dirs"]) if act_route else None,
             "refused": refused, "pressed_from": list(pressed_from), "push": self.world._travel_push if travel_legs is not None and choice else (act_route or {}).get("push"), "go_to": f'go_to "{a.action["go_to"]}": not in memory' if a.action and "go_to" in a.action and choice is None else None,
             "no_action": not a.action, "act": {"place": self.world.place_label(place), "why": a.why, "action": a.action},
             "option": {k: picked[k] for k in ("n", "tool", "label", "notes", "state")} if picked else None, "pick_key": picked.get("intent_key") if picked else None, "plan": self._plan_row,
             "probe": {"presses": probe["presses"], "locked": probe["locked"]} if probe is not None else None,
             "world": {"cut_dir": cut_dir, "pos": list(self.world.pos), "travelled": skill is self.world._travelled, "travel_open": self.world._travel_open, "current_place": self.world.current_place}}
        try: after_set = wait_settled(self.env)
        except BaseException: self.cached = self.cached_hash = None; raise
        return self._settle(s, skill, after_set, start, calls)
    def _settle(self, s, skill, after_set, start, calls):
        """Evidence and the row for an action, using its latest after-frame."""
        per, place, choice, aff, tid, intent = s["per"], s["place"], s["choice"], s["aff"], s["tid"], s["intent"]
        scene_cut, cut_dir, exit_key, refused = s["scene_cut"], s["cut_dir"], tuple(s["exit_key"]) if s["exit_key"] else None, s["refused"]
        after = after_set.frame; after_mark_eligible = per.get("screen") == "world" and not scene_cut
        player_fix = s["player_fix"]
        try:
            after_per, after_hash, after_reused, _ = self._see(after, place, screen_was_world=after_mark_eligible); after_fix = self.world.fix_player(after_per, after.size); player_fix = player_fix or after_fix
        except BaseException: self.cached = self.cached_hash = None; raise
        modal = {per.get("screen"), after_per.get("screen")} <= {"world", "dialogue", "menu"} and per.get("screen") != after_per.get("screen") and "world" in (per.get("screen"), after_per.get("screen")) and not (skill.blank_seen or skill.reason == "scene cut")
        scene_cut &= not modal
        if not scene_cut and (getattr(skill, "failure", None) or {}).get("stage") == "crossing": self.world.failed_push(skill, place, s["pressed_from"], s.get("push"), self.step_no + 1)
        next_place = self.world.current_place if s["travel"] and not self.world._travel_open else place if modal else self.mem.match_place(after_per, self.step_no + 1, prev=place, cut=scene_cut or self.cut_carry, via=(aff.get("target") if aff else None), exit=exit_key, via_id=tid, frame=after, tile_px=self.tile_px)
        spatial = self.world.after(skill, place, next_place, scene_cut, self.step_no + 1)
        self.cut_carry = bool((scene_cut or self.cut_carry) and not (s["travel"] and not self.world._travel_open or not modal and after_per.get("screen") == "world"))
        if next_place != place: self._moved(place, next_place, aff.get("target") if aff else None, self.step_no + 1, intent)
        if skill.scroll != (0, 0) and next_place == place and self.mem.get("places", place).get("state") == "candidate" and self.mem.get("places", place).get("screen") == "world":
            source = self.mem.get("places", place); source["state"] = "place"; self.mem.upsert("places", source)
        world_exit = bool(scene_cut and next_place != place and cut_dir and per.get("screen") == after_per.get("screen") == "world")
        outcome = effects(per, after_per, skill, place, next_place, place_after_candidate=not world_exit and self.mem.get("places", next_place).get("state") == "candidate", scene_cut=scene_cut and next_place != place)
        route =(getattr(skill, "failure", None) or {}).get("stage") in ("route", "interaction")  # never reached the thing (a route stopped short, a choice refused before any press): no evidence about it
        said_step, repeated = None, False
        if choice and tid and not route:  # a route that never reached the thing says nothing about it
            new_text = next((t for t in after_per.get("text", []) if t not in per.get("text", [])), "")
            for effect in outcome["effects"]:
                detail = outcome.get("to") if effect == "place_changes" else new_text if effect == "text_appears" else s["detail"] or effect
                normalize = lambda text: " ".join(str(text).replace("▼", "").replace("▶", "").split()).casefold()
                new_reply = normalize(detail)
                said = next((r for r in self.mem.rows("relations") if (r.get("s"), r.get("p")) == (tid, PRED[effect]) and (old_reply := normalize(r.get("o", ""))) and new_reply and (old_reply == new_reply or min(len(old_reply), len(new_reply)) >= 8 and (old_reply.startswith(new_reply) or new_reply.startswith(old_reply)))), None) if effect == "text_appears" else None
                said_step = said["step"] if said else None  # relate() updates the stored row in place
                repeated |= said_step is not None
                rel = self.mem.relate(tid, PRED[effect], str(detail or ""),
                    src=outcome["basis"].get(effect, "model"), step=self.step_no + 1,
                    hit=True if outcome["basis"].get(effect) == "pixel" else None,
                    verb=aff["verb"])
                if said_step is not None:
                    self.mem.upsert("relations", {**rel, "why": f"says the same as before (step {said_step})"})  # ponytail: a note on the says row, not a new relation kind
                if effect == "text_appears":
                    self.conversation["who"] = f"thing:{tid}"  # the dialogue it starts is that thing's
                    entity = self.mem.get("entities", tid)
                    speaker = re.match(r"^([A-Z][A-Za-z.' -]{0,19}):\s*", new_text)
                    if entity and self.world._actor(tid, entity) and speaker and sum(c.isalpha() for c in speaker.group(1)) >= 2:
                        self.mem.call(tid, speaker.group(1), self.step_no + 1)
            if skill.status == "done" and next_place == place and aff.get("verb") in ("interact", "approach"):
                thing = self.world.canon(f"thing:{tid}").split(":", 1)[1]
                for ref in self.world.leads_to(f"thing:{thing}"):
                    self.mem.relate(thing, "leads_to", ref.split(":", 1)[1], src="observed", step=self.step_no + 1, hit=False)
        crossing = self.mem.get("crossings", f"{place}@{exit_key[0]},{exit_key[1]}>{next_place}") if exit_key else None
        unseen_crossing = self.mem.get("crossings", s.get("unseen_crossing")) if s.get("unseen_crossing") else None
        verified_edge = bool((next_place != place and crossing and crossing.get("n") == 1) or (unseen_crossing and unseen_crossing.get("n") == 1))
        if choice and scene_cut and next_place != place:  # a cut to a non-navigable screen is no edge
            if world_exit: self.world.mark_exit(place, cut_dir, next_place, self.world.pos, self.step_no+1)
        row = {"step": self.step_no + 1, "t": time.time(), "place": place, "screen": per.get("screen"),
            "task": None, "frame": s["frame"], "after": f"frames/{self.step_no+1:06d}_after.png", "options": s["options"],
            "choice": choice, "pred": None, "used": [], "skill": {"status": skill.status, "reason": skill.reason, "inputs": skill.inputs, "calls": skill.calls, "failure": getattr(skill, "failure", None), "settled": getattr(skill, "settled", None)},
            "outcome": outcome, "verdict": "unobservable", "calls": self.model.calls-calls, "ms": int((time.perf_counter()-start)*1000), "shortcut": None,
            "ranker": None, "perception_reused": s["reused"], "frame_hash": s["frame_hash"], "marked": s["marked"],
            "scene_cut": scene_cut, "next_place": next_place, "verified_edge": verified_edge, "view_ids": [], "tile": self.world.pos if self.tile_px else None, "drift_fix": None, "player_fix": player_fix, "entities": self.world.entities, "scroll": spatial["scroll"], **({"exit": list(exit_key)} if scene_cut and not modal and next_place != place and exit_key else {}),
            "act": s["act"], "option": s["option"], "plan": s["plan"], "probe": s["probe"],
            "observation_status": "settled" if after_set.settled and s.get("before_settled", True) else "timed_out", "execution": skill.status, "action_step": None, **self._provenance(),
            "settle_frames": {"before": s["before_waited"], "after": after_set.waited},
            }
        self.step_no += 1
        after.save(self.mem.root.parent / row["after"])
        drift_fix = self.world.observe_after(after_per, after, after_reused, next_place, after.size, self.step_no, skill=skill)
        closed = self.notebook.observe(self.step_no, f"place:{next_place}" if next_place else None)
        self._closed(closed, self.step_no)
        first_visit = (self.mem.get("places", next_place) or {}).get("first") == self.step_no
        novel = bool(closed) or self.world.novel > 0 or verified_edge or (next_place != place and first_visit)
        idle = bool((choice or s.get("pick_key")) and not skill.inputs and skill.status == "done")  # a picked option that sent no input yet "succeeded" did nothing: a failure of that option, so the menu changes (blocked/failed keep their own reason)
        idle |= bool(choice and skill.status == "done" and next_place == place and not scene_cut and sum((r.get("choice") or {}).get("intent") == intent and r.get("frame_hash") == s["frame_hash"] and r.get("execution") == "done" for r in self.recent) >= 2)  # the same action from the same screen for the third time in five steps changed nothing that lasts
        idle |= repeated  # this reply said the same thing as before
        v = "progress" if closed else "failed" if idle else action_result(skill.status, outcome["effects"], novel) if choice else "unobservable"
        row["verdict"] = v
        row["tile"], row["drift_fix"] = self.world.pos if self.tile_px else None, list(drift_fix) if drift_fix else None
        row["self_spot"], row["self_locked"] = self.world.self_spot, self.world._self_from_motion
        fresh_text = self._learn(after_per.get("text", []), per.get("text", []), self.step_no, next_place, after_per.get("screen"))
        self.place, self.prev, self.prev_frame, self.cached, self.cached_hash = next_place, after_per, after, after_per, after_hash
        self.prev_frame_mark_eligible = after_mark_eligible
        self.pending_cut, self.pending_via, self.pending_key = False, None, None; reason = self.stuck.update({**row, "fresh_text": fresh_text})
        if any(at == self.step_no and dst == next_place for at, dst in self.new_places): self.stuck.progress()  # world progress only (Stuck.update counts first-time edges itself); text and repeat crossings never count
        if reason == "no_progress": self._event("T4", "stuck: no_progress", self.step_no)
        self.repair_pending = self.parse_failed and self.empty_streak >= 8 and not self.repair_used; self.repair_used |= self.repair_pending
        if s["unknown"]: reason = "player position unknown"
        self.cause = None
        if choice is not None:
            verb, target = aff.get("verb"), aff.get("target") or ""
            last = next((r for r in reversed(self.recent) if (r.get("choice") or {}).get("intent") == intent), {})
            failed = v in ("failed", "no_change") or skill.status in ("blocked", "failed") and last.get("execution") in ("blocked", "failed")  # a repeated blocked intent fails even when it revealed cells
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
                f = self.fails.setdefault(intent, {"n": 0}); f.update(n=f["n"] + 1, why=("same reply" if repeated else "did nothing" if idle else skill.reason or f"verdict {v}")[:40], step=self.step_no, act=f"{verb} {target}".strip())
                if stage == "route" and blocker and held and not refused and blocker != tid: f.update(avoid=list(held), blocker=blocker)  # the next route goes around it
                if thing and not route: self.mem.relate(thing, PRED["no_change"], "", src="observed", step=self.step_no, hit=False, verb=verb)  # the thing itself is contradicted; never deleted
            else:
                if intent in self.fails and not str(self.fails[intent].get("why", "")).endswith(": cycle"): self.fails.pop(intent)  # a success clears this intent's own counter, never another's
                if thing and (failure := next((r for r in self.mem.rows("relations") if (r.get("s"), r.get("p"), r.get("o")) == (thing, PRED["no_change"], "")), None)):
                    self.mem.upsert("relations", {**failure, "con": 0, "ev": [], "state": "live"})  # ponytail: clear the no-change relation in place
        elif idle:  # the picked option never became an action (an unresolved go_to): count it under the option's own key
            f = self.fails.setdefault(s["pick_key"], {"n": 0}); f.update(n=f["n"] + 1, why="did nothing", step=self.step_no, act=s["option"]["label"])
        if s["go_to"]: reason = s["go_to"]
        if s["no_action"] and not reason: reason = "your last reply had no valid action, so nothing happened: use one of the action shapes"
        self.flags = [x for x in (reason, f"last action {skill.status}: {skill.reason}" if skill.status != "done" else None, f"cause: {self.cause}" if self.cause else None) if x]
        self.planner_evidence.append({"step": row["step"], "place": place, "target": (aff or {}).get("target"), "verdict": v})
        self.planner_evidence = self.planner_evidence[-40:]
        row.update(self._observation_fields(s.get("observation_stamp")))
        if self.live_invalidation_reason: row["live_invalidation_reason"] = self.live_invalidation_reason; self.live_invalidation_reason = None
        self.trace.write(row); self.recent = [*self.recent, row][-5:]
        return row
    def _hold_row(self, frame, place, start, calls, **fields):
        """A no-input row when the live observation is stale."""
        step = self.step_no + 1
        row = {"step": step, "t": time.time(), "place": place, "screen": None, "task": None, "frame": frame, "after": None, "options": None,
            "choice": None, "pred": None, "used": [], "skill": None, "outcome": None, "verdict": "unobservable", "calls": self.model.calls - calls, "ms": int((time.perf_counter() - start) * 1000),
            "shortcut": None, "ranker": None, "perception_reused": False, "frame_hash": None, "marked": False, "scene_cut": False, "next_place": place, "verified_edge": False,
            "view_ids": [], "tile": self.world.pos if self.tile_px else None, "drift_fix": None, "player_fix": False, "entities": self.world.entities, "scroll": None,
            "act": None, "option": None, "plan": None, "probe": None, "observation_status": "pending", "execution": None, "action_step": None, **self._provenance(),
            **self._observation_fields(), **({"live_invalidation_reason": self.live_invalidation_reason} if self.live_invalidation_reason else {}), **fields}
        self.live_invalidation_reason = None
        self.step_no = step; self.trace.write(row); return row
    def _provenance(self):
        """Row fields tying a row to its invocation and to the model calls made during its outer step."""
        end = getattr(self.model, "seq", None)
        call_ids = [] if end is None or self._seq0 is None else list(range(self._seq0 + 1, end + 1))
        return {"invocation": self.invocation, "call_ids": call_ids, **({"provenance_incomplete": True} if self.provenance_incomplete else {})}
    def _learn(self, texts, seen, step, place, screen="world"):
        """Store new game text and log dialogue once when its screen sequence ends."""
        known = {_key(r.get("text", "")) for r in self.mem.rows("knowledge")}
        fresh = [t for t in texts if t not in seen and _key(t) not in known]
        for text in (t for t in texts if t not in seen): self.mem.know(text, src="game_text", step=step)
        if self.conversation["lines"] and (screen != self.conversation.get("screen", screen) or place != self.conversation["place"]):  # a conversation ends when its screen kind or place changes (dialogue -> menu, not only -> world)
            self._flush_conversation(step)
        if screen in ("menu", "title", "keyboard", "other"): return bool(fresh)  # non-dialogue, non-world text is knowledge only: no conversation, no trigger
        if screen == "world":
            self.conversation.pop("who", None)  # back in the world with no dialogue: nothing was said by the thing last touched
            if fresh:
                self.text_for_trigger = " / ".join(fresh)
                for text in fresh: self.notebook.heard(step, None, self.world.place_label(place) if place else "?", text)
                self._event("T1", f"new game text: {self.text_for_trigger[:200]}", step)
            return bool(fresh)
        if not self.conversation["lines"]: self.conversation.update(place=place, screen=screen)
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
        return bool(fresh)

    def _flush_conversation(self, step):
        lines = self.conversation["lines"]
        if not lines: return
        joined = " ".join(lines)
        match = re.match(r"^([A-Z][A-Za-z.' -]{0,19}):\s*", joined)
        speaker = match.group(1) if match and sum(c.isalpha() for c in match.group(1)) >= 2 else None
        if speaker: joined = joined[match.end():]
        place = self.conversation["place"]
        self.text_for_trigger = joined
        self.notebook.heard(step, speaker, self.world.place_label(place) if place else "?", joined, who=self.conversation.get("who"))
        self._event("T1", f"new game text: {joined[:200]}", step)
        self.conversation = {"place": None, "lines": []}
    def _screen_route(self, place, t, push, avoid=(), *, beside=False):  # to an on-screen entity at tile t; an actor holds its tile, so stop beside it, facing it; avoid: tiles a route blocker held
        occupied = self.world.occupied(place)
        if not beside and t not in occupied:
            dirs = route(self.mem, place, self.pos, t, occupied=occupied | set(avoid))
            return {"dirs": dirs, "push": push} if dirs is not None else None
        found = self.world.beside(place, self.pos, t)
        return {"dirs": found[2] + [found[1]], "push": push} if found else None
    def _approach_route(self, place, entity, player, proj, intent, *, beside=False, push=None):
        t = proj(entity.get("at")) if entity and self.tile_px and player else None
        held = (self.fails.get(intent) or {}).get("avoid")
        if t is not None and not beside and entity.get("kind", "").casefold() == "exit":
            tx, ty = t
            exits = []
            for row in self.mem.rows("tiles"):
                if row.get("place") != place: continue
                edge = row.get("exit") or {}; direction = edge.get("dir")
                dx, dy = DIRS.get(direction, (0, 0)); vx, vy = tx - row["x"], ty - row["y"]
                if direction not in DIRS or max(abs(vx-dx), abs(vy-dy))>1: continue
                dirs = route(self.mem, place, self.pos, (row["x"], row["y"]), occupied=self.world.occupied(place) | ({tuple(held)} if held else set()))
                if dirs is not None: exits.append((len(dirs), dirs, direction))
            if exits and push is None:
                _, dirs, push = min(exits)
                return t, {"dirs": dirs, "push": push}, False
        push = push or (next(iter(push_dirs(self.mem, place, t, entity.get("placement"))), None) if t is not None else None)
        result = self._screen_route(place, t, push, [tuple(held)] if held else (), beside=beside) if t is not None and (push or beside) else None
        return t, result, bool(t is not None and result is None or beside and t is None)
    def _unexplored(self, place, d=None):
        routes = self.world.explore_dirs(place)
        return routes.get(d) if d is not None else min(routes.values(), key=lambda row: len(row["dirs"]), default=None)
    def _live(self, frame, place, per, choice, a):
        """The decision before it is acted on, for scripts/watch_run.py; the trace row only lands after the skill and verification."""
        root = self.mem.root.parent; name = f"frames/{self.step_no+1:06d}.png"
        (root / "frames").mkdir(parents=True, exist_ok=True); frame.save(root / name)
        live = {"step": self.step_no + 1, "t": time.time(), "frame": name, "place": place, "screen": per.get("screen"), "entities": self.world.entities,
                "choice": choice or None, "act": {"place": self.world.place_label(place), "why": a.why, "action": a.action}, "plan": self._plan_row, "self_spot": self.world.self_spot}
        try: (root / "live.tmp").write_text(json.dumps(live, default=str), encoding="utf-8"); os.replace(root / "live.tmp", root / "live.json")
        except OSError: pass  # a reader holding live.json on Windows; the trace row still lands
    def _fail_key(self, place, verb, target, ref=None):
        """The intent key (World's fail_key): place | family | the resolved entity, exit or place (ref), else the target's name."""
        return fail_key(place, verb, target, self.env.inputs(), ref)
    def _moved(self, src, dst, via, step, key=None):
        label = self.world.place_label
        self.notebook.note(step, f"went from {label(src)} to {label(dst)}" + (f" via {via}" if via else ""))
        self.moves = [*self.moves, [step, src, dst, via or "", key]][-6:]
        if (self.mem.get("places", dst) or {}).get("visits", 0) <= 1 and not any(place == dst for _, place in self.new_places):
            self._event("T2", f"arrived somewhere new: {label(dst)}", step)
            self.new_places.append((step, dst))
    def _goal_text(self):
        quest = next((q for q in self.notebook.data["quests"] if q.get("status") == "open"), None)
        return str(quest.get("text", "")) if quest else ""
    def _event(self, kind, text, step):  # T4 and T5 wait at most once
        if kind in ("T1", "T2", "T3"): self.t5_armed = True
        if kind in ("T4", "T5") and any(e["kind"] == kind for e in self.events): return
        self.events.append({"kind": kind, "text": text, "step": step})
    def _closed(self, keys, step):
        if not keys: return
        self._event("T3", f"quest {', '.join(keys)} closed", step); self.stuck.progress(); self.moves = []
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
        failed = [f"{self.world.place_label(str(key).split('|', 1)[0])}: {value.get('act') or key} -> {value.get('why', 'failed')} x{value.get('n', 1) - self.fail_baseline.get(key, 0)}"
                  for key, value in self.fails.items() if value.get("n", 0) > self.fail_baseline.get(key, 0)]
        moved = {}
        for at, src, dst, via, *_ in self.moves:
            if at <= since: continue
            exits = moved.setdefault(self.world.place_label(src), {})
            exits[via or "unknown"] = exits.get(via or "unknown", 0) + 1
        def fit(label, items, cap, sep=", "):  # whole entries newest first within cap characters; if none fits, the newest cut with …
            kept, n = [], len(label) + 1
            for item in items:
                if n + len(item) > cap: break
                kept.append(item); n += len(item) + len(sep)
            if not kept and items: kept = [items[0][:max(0, cap - len(label) - 2)] + "…"]
            return f"{label} {sep.join(kept) or 'none'}"
        dump = lambda k, v: f"{k}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
        lines = [fit("new places:", places[::-1], 60), fit("tried by place:", [dump(k, v) for k, v in reversed(tried.items())], 100),
                 fit("moves:", [dump(k, v) for k, v in reversed(moved.items())], 60), fit("failed attempts:", failed[::-1], 60, "; "),
                 f"steps since last progress: {self.stuck.progress_n} (stall at {STALL * 2 ** self.stuck.u})"]
        lines = [line for line in lines if line]
        while len("\n".join(lines)) > 600: lines.pop()
        return "\n".join(lines)
    def _plan_trigger(self, per):
        """Replan after the three-step floor with an event waiting; T1 waits during dialogue or battle, and no open quest queues one."""
        if self.notebook.focus is None and self.t5_armed: self._event("T5", "no open quest", self.step_no + 1); self.t5_armed = False
        if self.last_plan is not None and self.step_no + 1 - self.last_plan < 3: return None
        if not any(e["kind"] != "T1" or per.get("screen") in ("world", "menu", "keyboard") for e in self.events): return None
        return "; ".join(e["text"] for kind in ("T3", "T1", "T2", "T5", "T4") for e in self.events if e["kind"] == kind)
    def state(self):
        state = {"step": self.step_no, "place": self.place, **self.world.state(), "tile_px": self.tile_px, "move_frames": self.move_frames, "stuck": self.stuck.state(), "perception": self.cached, "frame_hash": self.cached_hash, "perception_marked": self.cached_marked, "frame_mark_eligible": self.prev_frame_mark_eligible, "perception_version": self.cached_version, "parse_failed": self.parse_failed, "empty_streak": self.empty_streak, "repair_used": self.repair_used, "repair_pending": self.repair_pending, "pending_cut": self.pending_cut, "cut_carry": self.cut_carry, "pending_via": self.pending_via, "pending_key": self.pending_key, "flags": self.flags, "planner": {"last": self.last_plan, "events": self.events, "t5_armed": self.t5_armed, "fails": self.fails, "moves": self.moves, "new_places": self.new_places, "planner_evidence": self.planner_evidence, "fail_baseline": self.fail_baseline, "conversation": self.conversation, "text_for_trigger": self.text_for_trigger, "cause": self.cause}}
        if self.observation_stamp: state["observation_stamp"] = self.observation_stamp
        if not self.provenance_incomplete: state["modes"] = {"decide": bool(self.decide), "act_text": bool(self.act_text)}  # after a legacy restore the modes are not written: they were never recorded
        return state
    def restore(self, state):
        modes = state.get("modes")
        if modes is not None:  # checked before anything is restored: a conflict changes nothing
            saved, current = {"decide": bool(modes.get("decide")), "act_text": bool(modes.get("act_text"))}, {"decide": bool(self.decide), "act_text": bool(self.act_text)}
            if saved != current: raise ValueError(f"loop modes {current} conflicts with checkpoint modes {saved}")
        self.provenance_incomplete = modes is None
        self.step_no, self.place = int(state.get("step", 0)), state.get("place")
        self.observation_stamp = state.get("observation_stamp")
        self.tile_px, self.move_frames = state.get("tile_px"), int(state.get("move_frames", 16))
        self.world.tile_px = self.tile_px; self.world.restore(state)
        self.stuck.restore(state.get("stuck", {})); self.prev = state.get("perception"); self.prev_frame = self.env.screenshot()
        self.prev_frame_mark_eligible = state.get("frame_mark_eligible")
        digest = hashlib.sha256(self.prev_frame.tobytes()).hexdigest()
        self.cached, self.cached_hash, self.cached_marked, self.cached_version = state.get("perception"), state.get("frame_hash"), bool(state.get("perception_marked", False)), state.get("perception_version")
        self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = bool(state.get("parse_failed")), int(state.get("empty_streak", 0)), bool(state.get("repair_used")), bool(state.get("repair_pending")); self.pending_cut, self.pending_via, self.pending_key = bool(state.get("pending_cut")), state.get("pending_via"), state.get("pending_key"); self.cut_carry = bool(state.get("cut_carry")); self.flags = state.get("flags", [])
        planner = state.get("planner") or {}; self.last_plan = planner.get("last")
        self.fails, self.moves = planner.get("fails") or {}, planner.get("moves") or []
        self.events, self.t5_armed = [dict(e) for e in planner.get("events", [])], planner.get("t5_armed", False)
        self.new_places = planner.get("new_places") or []
        self.planner_evidence = planner.get("planner_evidence") or []
        self.fail_baseline = planner.get("fail_baseline", {key: value.get("n", 0) for key, value in self.fails.items()} if self.last_plan is not None else {})
        self.conversation, self.text_for_trigger = planner.get("conversation") or {"place": None, "lines": []}, planner.get("text_for_trigger", "")
        self.cause = planner.get("cause")
        if self.cached_version != self.prompt_version or digest != self.cached_hash: self.cached = self.cached_hash = None; self.cached_marked = False
