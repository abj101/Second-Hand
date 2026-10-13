"""One game-neutral perception, choice, action, and learning cycle."""
import hashlib, json, time
from agent.act import act
from agent.brief import GENERAL, _load, defaults, load_brief
from agent.decide import Choice, choose, known_routes, next_test, navigation_candidates, plan, related_target_ids, resolve_target
from agent.notebook import Notebook
from agent.observer import cut, for_model, wait_settled
from agent.perceive import perceive
from agent.predict import PRED, effects, predict, verdict
from agent.skills import Ctx, press_seq, run_affordance, wait_until
from agent.tasks import Stuck, Tasks
from agent.trace import Trace
from agent.space import DIRS, describe, frontier, outward, route
from agent.world import World
class Loop:
    def __init__(self, env, model, mem, game, act_mode=False):
        self.env, self.model, self.mem, self.game = env, model, mem, game
        self.act_mode, self.notebook, self.flags = act_mode, Notebook(mem.root) if act_mode else None, []
        self.trace = Trace(mem.root.parent / "trace.jsonl"); self.step_no = 0
        self.place = self.commit = None; self.prev = None; self.tile_px = getattr(env, "tile_px", None)
        self.move_frames = 16; self.stuck = Stuck(); self.tasks = Tasks(mem)
        self.recent = self.trace.tail(5); self.cached = None; self.cached_hash = None; self.prev_frame = None; self.prompt_version = "e5-perceiver-8"; self.cached_version = self.prompt_version
        self.pending_cut, self.pending_via, self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = False, None, False, 0, False, False
        self.last_plan_step, self.pending_plan_reason = None, None
        self.last_plan_text_count = sum(r.get("src") == "game_text" for r in mem.rows("knowledge"))
        self.world = World(mem, self.tile_px)
    @property
    def pos(self): return self.world.pos
    @pos.setter
    def pos(self, value): self.world.pos = tuple(value)
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
        before_set = wait_settled(self.env); before = before_set.frame; prior = self.place; known_places = {p.get("id") for p in self.mem.rows("places")}
        per, frame_hash, reused, _ = self._see(before, prior); self.empty_streak = self.empty_streak + 1 if self.parse_failed else 0; self.repair_used = self.repair_used if self.parse_failed else False
        via = next((a.get("target") for a in per.get("affordances", []) if a.get("verb") == "approach"), None)
        modal = self.prev and {self.prev.get("screen"), per.get("screen")} <= {"world", "dialogue", "menu"} and self.prev.get("screen") != per.get("screen") and "world" in (self.prev.get("screen"), per.get("screen")) and not (self.pending_cut or before_set.blank_seen)
        place = self.mem.match_place(per, self.step_no + 1, prev=prior, cut=bool((self.pending_cut or prior and cut(self.prev_frame, before, before_set.blank_seen)) and not modal), via=self.pending_via or via)
        self.place, self.pending_cut, self.pending_via = place, False, None
        player = next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None); size = before.size
        proj, tiles, ids = self.world.before(per, place, prior, size, self.step_no + 1); pa = self.world.pa
        drift_fix = None
        for text in per.get("text", []):
            if text not in (self.prev or {}).get("text", []): self.mem.know(text, src="game_text", step=self.step_no + 1)
        task = self.tasks.active(); self.tasks.current_place, self.tasks.perception, self.tasks.settled = place, per, before_set.settled
        changed_tasks = self.tasks.check(self.step_no + 1); task = self.tasks.active()
        on_screen = [ids[i] for i in ids if i < len(per.get("entities", []))]
        ctx = {"place": place, "task": task["id"], "on_screen": on_screen, "recent": self.recent, "step": self.step_no + 1}
        if self.act_mode:
            view_ids = []
            self.world.view_flags = self.flags
            goals = " ".join(f"{key} {value.get('text', '')}" for key, value in self.notebook.data["goals"].items()
                              if value.get("status", "active") == "active")
            view = self.world.view(goals, place)
        else:
            view, view_ids = self.mem.view("chooser", ctx)
            view = (view + "\n" + describe(self.mem, place, self.pos)).strip()
        affs = list(per.get("affordances", [])); targets = [resolve_target(self.mem, a.get("target", ""), place, proj(a.get("at"))) for a in affs]
        preds = [predict(self.mem, a, targets[i], place) for i, a in enumerate(affs)]
        choice, exit_test, player = None, False, next((e for e in per.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
        candidate = act_route = travel_legs = None
        if self.act_mode:
            a = act(self.model, before, per, self.notebook.render(), view, self.flags, "\n".join(_load(self.game).get("controls", [])), defaults(self.game), list(self.env.inputs()), places=[self.world.place_label(p["id"]) for p in sorted((p for p in self.mem.rows("places") if p.get("state") != "merged"), key=lambda p: p.get("last", p.get("first", 0)), reverse=True)[:12]])
            applied = self.notebook.apply(a.ops, self.step_no + 1)
            named = self.world.name(place, a.place, self.step_no + 1)
            if named != place:  # model says this new arrival is a known place
                place = self.place = named; ctx["place"] = place
                if self.world.new_arrival: self.world.pos = next(((r["x"], r["y"]) for r in self.mem.rows("tiles") if r.get("place") == place and (r.get("exit") or {}).get("to") == prior), self.world.pos)
                proj, tiles, ids = self.world.before(per, place, place, size, self.step_no + 1)
                pa = self.world.pa
                targets = [resolve_target(self.mem, x.get("target", ""), place, proj(x.get("at"))) for x in affs]; preds = [predict(self.mem, x, targets[j], place) for j, x in enumerate(affs)]
            action, i = a.action, None
            if action and "go_to" in action:
                resolved = self.world.resolve(action["go_to"], per, place); e = next((e for n, e in enumerate(per.get("entities", [])) if self.world.entity_ids.get(n) == resolved.get("id")), None) if resolved and resolved["kind"] == "screen" else None
                if resolved and (e or resolved["kind"] != "screen"):
                    affs.append({"verb": "approach", "target": (e or {}).get("name", action["go_to"]), "at": (e or {}).get("at")}); targets.append(resolved.get("id")); preds.append(None); i = len(affs) - 1
                    act_route = {"dirs": dirs, "push": outward(self.mem, place, t, None)} if e and self.tile_px and player and (t := proj(e.get("at"))) and (dirs := route(self.mem, place, self.pos, t)) is not None else None; travel_legs = self.world.plan(resolved) if not e else None; action = None if travel_legs is None and not e else action
                else: action = None
            if action and "buttons" in action:
                affs.append({"verb": "buttons", "target": " ".join(action["buttons"])}); targets.append(None); preds.append(None); i = len(affs) - 1
            if action and "i" in action: i = action["i"]
            if action: choice = Choice(i, a.expect, "", [], a.why, True, a.text)
        if not self.act_mode and any(c == {"new_place": True} for c in task.get("done_when", [])) and per.get("screen") == "world" and player:
            entity_ids = {i: resolve_target(self.mem, e.get("name", ""), place, tiles.get(i)) if str(e.get("name", "")).casefold().startswith("exit:") else ids.get(i) for i, e in enumerate(per.get("entities", []))}
            candidates = navigation_candidates(per, targets, entity_ids, player_at=pa, tile_frac=(self.tile_px / before.width, self.tile_px / before.height) if self.tile_px else None)
            visited = {p["id"] for p in self.mem.rows("places")}; exits = [(r["x"],r["y"]) for r in self.mem.rows("tiles") if r.get("place")==place and (r.get("exit") or {}).get("to") in visited]
            candidates = [c for c in candidates if not (proj(c.get("at")) and any(max(abs(a-b) for a,b in zip(proj(c["at"]),e)) <= 1 for e in exits))]
            tested = related_target_ids(self.mem, place); spots = [e["tile"] for t in tested if (e := self.mem.get("entities", t) or {}).get("place") == place and e.get("tile")]
            candidates = [c for c in candidates if c.get("id") or not ((t := proj(c.get("at"))) and any(max(abs(a-b) for a,b in zip(t,s)) <= 1 for s in spots))]  # id-less duplicates of tested targets
            commit, self.commit = self.commit, None; candidate = next((c for c in candidates if commit and commit[0] == place and (c["id"] == commit[1] if commit[1] else c["name"] == commit[2])), None) or next_test(None, task.get("goal", ""), known_routes(self.mem), candidates, tested)
            if candidate and self.tile_px and player:
                target_tile = proj(candidate["at"])  # own tile: id matching gave id-less targets another entity's
                dirs = route(self.mem, place, self.pos, target_tile) if target_tile else None
                if dirs is not None: candidate.update(tile=target_tile, route={"dirs": dirs, "push": outward(self.mem, place, target_tile, candidate.get("placement"))}, push=outward(self.mem, place, target_tile, candidate.get("placement")))
            if not candidate and self.tile_px and player:
                pushes = [(p,d) for p,d in frontier(self.mem, place) if d not in (self.mem.get("tiles", f"{place}@{p[0]},{p[1]}") or {}).get("pushed", [])]
                if pushes:
                    tile, direction = min(pushes, key=lambda q: sum(abs(a-b) for a,b in zip(q[0], self.pos))); dirs = route(self.mem, place, self.pos, tile)
                    if dirs is not None:
                        at = [pa[j] + (tile[j]-self.pos[j]) * self.tile_px / before.size[j] for j in (0,1)]; aff = {"verb":"approach", "target":f"frontier {tile}", "at":at}
                        candidate = {"id":None, "tile":tile, "affordance":aff, "route":{"dirs":dirs,"push":direction}, "push":direction}
            if candidate:
                affs.append(candidate["affordance"]); targets.append(candidate.get("id")); preds.append(None); choice = Choice(len(affs) - 1, "place_changes", "", [], "goal-directed target test", False); exit_test = True
        if choice is None and not self.act_mode:
            choice = choose(self.model, view, per, task, preds, defaults(self.game), controls="\n".join(_load(self.game).get("controls", []))) if affs else None
        if choice and choice.called:
            row = {"step": self.step_no + 1, "view": view, "affordances": affs, "task": task, "preds": preds,
                   "choice": choice.__dict__, "view_ids": view_ids, "recent": [r for r in self.recent if r.get("place")==place and r.get("frame_hash")==frame_hash], "place": place,
                    "frame_hash": frame_hash, "frame": f"frames/{self.step_no+1:06d}.png"}
            with (self.mem.root.parent / "chooser.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps(row, default=str) + "\n")
        unknown = bool(choice and affs[choice.i].get("verb") in ("approach", "interact") and not self.env.pointer and
                       not any(e.get("kind", "").casefold() == "player" for e in per.get("entities", [])))
        skillctx = Ctx(self.env, self.model, self.tile_px, self.move_frames)
        planned_route = act_route or (candidate.get("route") if exit_test and candidate else None)
        try:
            if self.act_mode: skill = self.world.travel(skillctx, travel_legs, self.step_no + 1, self.trace) if travel_legs is not None and choice else wait_until(skillctx) if not affs or choice is None else press_seq(skillctx, affs[choice.i]["target"].split()) if affs[choice.i].get("verb") == "buttons" else run_affordance(skillctx, affs[choice.i], per, text=choice.text, exit=False, **({"route": planned_route} if planned_route else {}))
            else: skill = wait_until(skillctx) if not affs else run_affordance(skillctx, affs[choice.i], per, text=choice.text, exit=exit_test or bool(planned_route), **({"route": planned_route} if planned_route else {}))
        except BaseException: self.pending_cut, self.pending_via = cut(before, self.env.screenshot()), affs[choice.i].get("target") if choice else None; self.cached = self.cached_hash = None; raise
        if choice and skill.status == "done" and affs[choice.i].get("verb") in ("approach", "interact") and choice.i < len(targets) and targets[choice.i]:
            self.world._promote([targets[choice.i]])
        self.tile_px = skillctx.tile_px; self.world.tile_px = self.tile_px
        scene_cut = self.world._travel_cut if travel_legs is not None else skill.blank_seen or skill.reason == "scene cut" or (cut(skill.before, skill.after) and (self.env.pointer or not choice or affs[choice.i].get("verb") not in ("approach", "interact") or skill.scroll == (0, 0)))
        moves = getattr(skill, "moves", []); spatial = self.world.prepare(skill, place, scene_cut, self.step_no + 1); exit_key, cut_dir = spatial["exit_key"], spatial["cut_dir"]
        self.pending_cut, self.pending_via = scene_cut, affs[choice.i].get("target") if choice else None
        try: after_set = wait_settled(self.env); after = after_set.frame; after_per, after_hash, after_reused, _ = self._see(after, place)
        except BaseException: self.cached = self.cached_hash = None; raise
        modal = {per.get("screen"), after_per.get("screen")} <= {"world", "dialogue", "menu"} and per.get("screen") != after_per.get("screen") and "world" in (per.get("screen"), after_per.get("screen")) and not (skill.blank_seen or skill.reason == "scene cut")
        scene_cut &= not modal
        next_place = self.world.current_place if travel_legs is not None else place if modal else self.mem.match_place(after_per, self.step_no + 1, prev=place, cut=scene_cut, via=(affs[choice.i].get("target") if choice else None), exit=exit_key, via_id=targets[choice.i] if choice else None)
        spatial = self.world.after(skill, place, next_place, scene_cut, self.step_no + 1); known_exit = spatial["known_exit"]
        if skill.scroll != (0, 0) and next_place == place and self.mem.get("places", place).get("state") == "candidate":
            source = self.mem.get("places", place); source["state"] = "place"; self.mem.upsert("places", source)
        world_exit = bool(scene_cut and next_place != place and cut_dir and per.get("screen") == after_per.get("screen") == "world")
        outcome = effects(per, after_per, skill, place, next_place, place_after_candidate=not world_exit and self.mem.get("places", next_place).get("state") == "candidate", scene_cut=scene_cut)
        blocked = skill.reason == "route blocked" and planned_route and len(moves) < len(planned_route["dirs"])
        if blocked and candidate and next_place == place: self.commit = (place, candidate.get("id"), candidate.get("name"))  # keep the target after a wall bump; the ranker would wander
        pred = (preds[choice.i] or {"effect": choice.effect, "detail": choice.detail, "basis": "guess"}) if choice else None
        if choice and exit_test:
            pred = {**pred, "effect": "place_changes", "detail": None}
        elif choice and str(affs[choice.i].get("target", "")).startswith("exit"):
            pred = {**pred, "effect": "place_changes", "detail": pred.get("detail") if pred.get("effect") == "place_changes" else None}
        v = "unobservable" if not affs or not choice else verdict(pred, outcome, blocked=skill.status == "blocked", inputs=skill.inputs)
        if choice and targets[choice.i]:
            for effect in outcome["effects"]:
                detail = outcome.get("to") if effect == "place_changes" else choice.detail or effect
                self.mem.relate(targets[choice.i], PRED[effect], str(detail or ""),
                    src=outcome["basis"].get(effect, "model"), step=self.step_no + 1,
                    hit=True if outcome["basis"].get(effect) == "pixel" else None,
                    verb=affs[choice.i]["verb"], pattern_id=pred.get("id") if pred and pred.get("basis") == "pattern" and effect == pred.get("effect") else None)
            # A route stopped by a newly learned wall before the target says nothing about the target; replan next step.
            if pred and pred["effect"] not in outcome["effects"] and not blocked:
                self.mem.relate(targets[choice.i], PRED[pred["effect"]], str(pred.get("detail") or ""),
                                src="observed", step=self.step_no+1, hit=False, verb=affs[choice.i]["verb"],
                                pattern_id=pred.get("id") if pred.get("basis") == "pattern" else None)
        edge_rows = self.mem.get("places", place).get("edges", [])
        verified_edge = bool(choice and scene_cut and next_place != place and any(outcome["basis"].get(e) == "pixel" for e in ("place_changes", "screen_changes")) and
            not any((e.get("verb"), e.get("via"), e.get("to")) == (affs[choice.i]["verb"], affs[choice.i].get("target", ""), next_place) and e.get("ok") for e in edge_rows))
        if choice and scene_cut:
            self.mem.add_edge(place, affs[choice.i]["verb"], affs[choice.i].get("target", ""), next_place, True)
            if world_exit: self.world.mark_exit(place, cut_dir, next_place, tuple(known_exit["arrive"]) if known_exit else (0,0), self.step_no+1)
        if skill.reason == "exit not crossed" and candidate and candidate.get("push") and planned_route and len(moves) > len(planned_route["dirs"]):
            pushed = self.mem.get("tiles", f"{place}@{(candidate.get('tile') or self.pos)[0]},{(candidate.get('tile') or self.pos)[1]}")
            if pushed: pushed["pushed"] = list(dict.fromkeys([*pushed.get("pushed", []), candidate["push"]])); self.mem.upsert("tiles", pushed)
        row = {"step": self.step_no + 1, "t": time.time(), "place": place, "screen": per.get("screen"),
            "task": task["id"], "frame": f"frames/{self.step_no+1:06d}.png", "options": len(affs),
            "choice": ({"i": choice.i, **affs[choice.i], "id": targets[choice.i], "called": choice.called, "text": choice.text, "why": choice.why} if choice else None),
            "pred": pred, "used": choice.used if choice else [], "skill": {"status": skill.status, "reason": skill.reason, "inputs": skill.inputs, "calls": skill.calls},
            "outcome": outcome, "verdict": v, "calls": self.model.calls-calls, "ms": int((time.perf_counter()-start)*1000), "shortcut": None,
            "ranker": None, "perception_reused": reused, "frame_hash": frame_hash,
            "scene_cut": scene_cut, "next_place": next_place, "verified_edge": verified_edge, "view_ids": view_ids, "tile": self.world.pos if self.tile_px else None, "drift_fix": list(drift_fix) if drift_fix else None, "entities": self.world.entities, "scroll": spatial["scroll"], **({"exit": list(exit_key)} if scene_cut and not modal and next_place != place and exit_key else {}),
            "task_budget_exhausted": any(t.get("last_failure") == "budget exhausted" for t in changed_tasks), "wake": None,
            **({"act": {"place": a.place, "ops": applied, "why": a.why, "action": a.action}, **({"flags": [f"merge_pending: {self.world.merge_pending}"]} if self.world.merge_pending else {})} if self.act_mode else {})}
        self.step_no += 1
        (self.mem.root.parent / row["frame"]).parent.mkdir(parents=True, exist_ok=True); before.save(self.mem.root.parent / row["frame"])
        drift_fix = self.world.observe_after(after_per, after, after_reused, next_place, after.size, self.step_no)
        row["tile"], row["drift_fix"] = self.world.pos if self.tile_px else None, list(drift_fix) if drift_fix else None
        for text in after_per.get("text", []):
            if text not in per.get("text", []): self.mem.know(text, src="game_text", step=self.step_no)
        self.place, self.prev, self.prev_frame, self.cached, self.cached_hash = next_place, after_per, after, after_per, after_hash
        self.pending_cut, self.pending_via = False, None; reason = self.stuck.update(row); self.repair_pending = self.parse_failed and self.empty_streak >= 8 and not self.repair_used; self.repair_used |= self.repair_pending
        if unknown: reason = "player position unknown"
        if self.act_mode and a.action and "go_to" in a.action and choice is None: reason = f'go_to "{a.action["go_to"]}": not in memory'
        if self.act_mode: self.flags = [x for x in (reason, f"last action {skill.status}: {skill.reason}" if skill.status != "done" else None, f"verdict {v}" if v in ("miss", "surprise") else None, f"merge_pending: {self.world.merge_pending}" if self.world.merge_pending else None) if x]; self.world.merge_pending = None; self.world.new_arrival = False
        ended = changed_tasks[-1] if changed_tasks else {}; game_text_count = sum(r.get("src") == "game_text" for r in self.mem.rows("knowledge"))
        self.pending_plan_reason = (f"goal {ended.get('status')}: {ended.get('goal', '')}" if changed_tasks else reason or self.pending_plan_reason or ("new game text" if game_text_count > self.last_plan_text_count else None))
        if not self.pending_plan_reason: self.pending_plan_reason = "surprise" if v == "surprise" else "new_place" if verified_edge and next_place not in known_places else "periodic" if self.step_no % 50 == 0 else None
        if self.pending_plan_reason and (self.last_plan_step is None or self.step_no - self.last_plan_step >= 15): row["wake"] = self.pending_plan_reason
        self.trace.write(row); self.recent = [*self.recent, row][-5:]
        if not self.act_mode and self.pending_plan_reason and (self.last_plan_step is None or self.step_no - self.last_plan_step >= 15):
            ended = changed_tasks[-1] if changed_tasks else next((t for t in reversed(self.mem.rows("tasks")) if self.pending_plan_reason == f"goal {t.get('status')}: {t.get('goal', '')}"), {})
            plan_view, _ = self.mem.view("orchestrator", {**ctx, "task": task.get("id")}); planned = plan(self.model, plan_view + "\n" + describe(self.mem, place, self.pos), self.pending_plan_reason, ended.get("goal", task.get("goal", "")), ended.get("status", ""), per.get("text", []), pattern_names={p["id"]: p.get("name", "") for p in self.mem.rows("patterns") if p.get("id")})
            parent = task.get("id")
            for sub in planned:
                try:
                    added = self.tasks.add(**sub, parent=parent); added["plan_step"] = self.step_no; self.mem.upsert("tasks", added); parent = added["id"]
                except ValueError: continue
            for pattern in self.mem.rows("patterns"):
                name = getattr(planned, "pattern_names", {}).get(pattern.get("id"))
                if isinstance(name, str) and name.strip(): pattern["name"] = name.strip(); self.mem.upsert("patterns", pattern)
            self.last_plan_step, self.last_plan_text_count, self.pending_plan_reason = self.step_no, game_text_count, None
        return row
    def state(self):
        task = next((r.get("id") for r in reversed(self.mem.rows("tasks")) if r.get("status")=="active"),None)
        return {"step": self.step_no, "place": self.place, **self.world.state(), "tile_px": self.tile_px, "move_frames": self.move_frames, "stuck": self.stuck.state(), "perception": self.cached, "frame_hash": self.cached_hash, "perception_version": self.cached_version, "parse_failed": self.parse_failed, "empty_streak": self.empty_streak, "repair_used": self.repair_used, "repair_pending": self.repair_pending, "pending_cut": self.pending_cut, "pending_via": self.pending_via, "task": task, "last_plan_step": self.last_plan_step, "last_plan_text_count": self.last_plan_text_count, "pending_plan_reason": self.pending_plan_reason, "commit": self.commit, "flags": self.flags}
    def restore(self, state):
        self.step_no, self.place, self.last_plan_step = int(state.get("step", 0)), state.get("place"), state.get("last_plan_step")
        self.last_plan_text_count, self.pending_plan_reason = int(state.get("last_plan_text_count", sum(r.get("src") == "game_text" for r in self.mem.rows("knowledge")))), state.get("pending_plan_reason")
        self.tile_px, self.move_frames = state.get("tile_px"), int(state.get("move_frames", 16))
        self.world.tile_px = self.tile_px; self.world.restore(state)
        self.stuck.restore(state.get("stuck", {})); self.prev = state.get("perception"); self.prev_frame = self.env.screenshot()
        digest = hashlib.sha256(self.prev_frame.tobytes()).hexdigest()
        self.cached, self.cached_hash, self.cached_version = state.get("perception"), state.get("frame_hash"), state.get("perception_version")
        self.parse_failed, self.empty_streak, self.repair_used, self.repair_pending = bool(state.get("parse_failed")), int(state.get("empty_streak", 0)), bool(state.get("repair_used")), bool(state.get("repair_pending")); self.pending_cut, self.pending_via = bool(state.get("pending_cut")), state.get("pending_via"); self.commit = tuple(state["commit"]) if state.get("commit") else None; self.flags = state.get("flags", [])
        if self.cached_version != self.prompt_version or digest != self.cached_hash: self.cached = self.cached_hash = None
