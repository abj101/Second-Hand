"""Single authority: scene + schema → snapshot, invoke, verify."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Sequence

from loop.adaptive.evidence import iou
from loop.adaptive.orchestrate import registry_offers as eligible_registry_offers
from loop.adaptive.schema import (
    ActionInstance,
    ActionInvocation,
    ActionSpec,
    ActionSurface,
    AdaptiveSchema,
    BindingDomain,
    EffectPredicate,
    EntityTypeSpec,
    FieldSpec,
    ParamSpec,
    PrimitiveStep,
    SceneGraph,
    SceneNode,
    SensorRecipe,
    StateSnapshot,
    eval_precondition,
)

PrimitivesRunner = Callable[[tuple[PrimitiveStep, ...], dict[str, Any]], None]


class InvocationError(Exception):
    """Illegal invocation or execution."""


def _field_attr_keys(field_id: str) -> tuple[str, ...]:
    keys = [field_id]
    if field_id.startswith("field_"):
        keys.append(field_id[6:])
    if field_id == "field_scalar":
        keys.append("numeric")
    return tuple(dict.fromkeys(keys))


def _node_has_field(node: SceneNode, field_id: str) -> bool:
    return any(k in node.attributes for k in _field_attr_keys(field_id))


def _read_field_from_attrs(attributes: dict, field_id: str) -> Any:
    for key in _field_attr_keys(field_id):
        if key in attributes:
            return attributes[key]
    return None


def _node_matches_entity_type(node: SceneNode, spec: EntityTypeSpec) -> bool:
    if node.attributes.get("type_id") == spec.type_id:
        return True
    if not spec.field_ids:
        return False
    return all(_node_has_field(node, fid) for fid in spec.field_ids)


def _node_matches_classifier(node: SceneNode, classifier: dict) -> bool:
    for key, want in classifier.items():
        if key == "type_id":
            if node.attributes.get("type_id") != want:
                return False
        elif key == "clickable":
            if bool(node.attributes.get("clickable")) != bool(want):
                return False
        elif key in ("numeric", "has_numeric"):
            if ("numeric" in node.attributes) != bool(want):
                return False
        elif key == "has_label":
            if ("label" in node.attributes) != bool(want):
                return False
        elif node.attributes.get(key) != want:
            return False
    return True


def _mode_activation_matches(
    activation: dict,
    fields: dict[str, Any],
    entities: dict[str, dict[str, Any]],
) -> bool:
    for key, want in activation.items():
        if key in fields:
            if fields[key] != want:
                return False
        elif key == "type_id":
            if not any(row.get("type_id") == want for row in entities.values()):
                return False
        elif key == "min_fields":
            if len(fields) < int(want):
                return False
    return True


def _sensor_maps(
    sensors: tuple[SensorRecipe, ...],
) -> tuple[dict[str, SensorRecipe], dict[str, SensorRecipe]]:
    by_field_id: dict[str, SensorRecipe] = {}
    by_sensor_id: dict[str, SensorRecipe] = {}
    for recipe in sensors:
        by_sensor_id[recipe.sensor_id] = recipe
        fid = recipe.config.get("field_id")
        if fid:
            by_field_id[str(fid)] = recipe
    return by_field_id, by_sensor_id


def _field_sensor(
    fs: FieldSpec,
    by_field_id: dict[str, SensorRecipe],
    by_sensor_id: dict[str, SensorRecipe],
) -> SensorRecipe | None:
    if fs.sensor_id:
        return by_sensor_id.get(fs.sensor_id)
    return by_field_id.get(fs.field_id)


def _collect_global_fields(scene: SceneGraph, schema: AdaptiveSchema) -> dict[str, Any]:
    by_field_id, by_sensor_id = _sensor_maps(schema.sensors)
    out: dict[str, Any] = {}
    for fs in schema.fields:
        sensor = _field_sensor(fs, by_field_id, by_sensor_id)
        roi = sensor.roi if sensor is not None else None
        for node in scene.nodes:
            if roi is not None and iou(node.bbox, roi) < 0.05:
                continue
            val = _read_field_from_attrs(node.attributes, fs.field_id)
            if val is None and fs.selector:
                if _node_matches_selector(node, fs.selector):
                    val = node.attributes.get("numeric")
            if val is not None:
                out[fs.field_id] = val
                break
    return out


def _collect_entities(scene: SceneGraph, schema: AdaptiveSchema) -> dict[str, dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    for et in schema.entity_types:
        for node in scene.nodes:
            if et.classifier:
                if not _node_matches_classifier(node, et.classifier):
                    continue
            elif not _node_matches_entity_type(node, et):
                continue
            row: dict[str, Any] = {"type_id": et.type_id}
            for fid in et.field_ids:
                val = _read_field_from_attrs(node.attributes, fid)
                if val is not None:
                    row[fid] = val
            entities[node.node_id] = row
    return entities


def _infer_mode(
    schema: AdaptiveSchema,
    fields: dict[str, Any],
    entities: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    if not schema.modes:
        return None
    ents = entities if entities is not None else {}
    for ms in schema.modes:
        if ms.activation and not _mode_activation_matches(ms.activation, fields, ents):
            continue
        probe = StateSnapshot(
            snapshot_id="",
            schema_revision_id=schema.revision.revision_id,
            mode_id=ms.mode_id,
            fields=fields,
            entities={},
            surface=ActionSurface("", schema.revision.revision_id, ()),
        )
        if all(eval_precondition(p, probe) for p in _mode_preconditions(schema, ms.mode_id)):
            return ms.mode_id
    return None


def _mode_preconditions(schema: AdaptiveSchema, mode_id: str) -> tuple[str, ...]:
    # ponytail: modes only gated via explicit mode== preconditions on actions today
    return (f"mode=={mode_id}",)


def _rgb_close(a: object, b: object, *, tol: float = 25.0) -> bool:
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)):
        return False
    if len(a) < 3 or len(b) < 3:
        return False
    return sum(abs(int(a[i]) - int(b[i])) for i in range(3)) / 3.0 < tol


def _node_matches_selector(node: SceneNode, selector: dict) -> bool:
    # Selector clauses are conjunctive.  Treating them as alternatives makes a
    # label OR a coarse color sufficient and can bind multiple unrelated nodes.
    checks: list[bool] = []
    if "label" in selector:
        want = str(selector["label"]).casefold()
        checks.append(str(node.attributes.get("label", "")).casefold() == want)
    if "mean_rgb" in selector:
        rgb = node.attributes.get("mean_rgb")
        checks.append(rgb is not None and _rgb_close(rgb, selector["mean_rgb"]))
    if "clickable" in selector:
        checks.append(bool(node.attributes.get("clickable")) == bool(selector["clickable"]))
    bbox = selector.get("bbox")
    if bbox is not None:
        try:
            want_bb = tuple(float(x) for x in bbox)
            checks.append(iou(node.bbox, want_bb) >= 0.3)
        except (TypeError, ValueError):
            checks.append(False)
    return bool(checks) and all(checks)


def resolve_selector(scene: SceneGraph, selector: dict) -> tuple[str, ...]:
    return _selector_matched_node_ids(scene.nodes, selector)


def _selector_matched_node_ids(
    nodes: tuple[SceneNode, ...],
    selector: dict,
) -> tuple[str, ...]:
    matched = [n for n in nodes if _node_matches_selector(n, selector)]
    track_hits = [n for n in matched if n.node_id.startswith("track_")]
    if len(track_hits) == 1:
        matched = track_hits
    if "label" in selector:
        want = str(selector["label"]).casefold()
        matched.sort(
            key=lambda n: (
                str(n.attributes.get("label", "")).casefold() != want,
                n.node_id,
            )
        )
    else:
        matched.sort(key=lambda n: n.node_id)
    return tuple(n.node_id for n in matched)


def _param_candidates(
    kind: str,
    entities: dict[str, dict[str, Any]],
    nodes: tuple[SceneNode, ...],
    selector: dict | None = None,
) -> tuple[str, ...]:
    sel = selector or {}
    if kind == "entity":
        if sel:
            return _selector_matched_node_ids(nodes, sel)
        ids = set(entities)
        for node in nodes:
            if node.attributes.get("clickable"):
                ids.add(node.node_id)
        return tuple(sorted(ids))
    if kind == "point":
        if sel:
            return _selector_matched_node_ids(nodes, sel)
        return tuple(sorted(n.node_id for n in nodes))
    return ()


def _bound_node_id(domains: tuple[BindingDomain, ...]) -> str | None:
    by_name = {d.name: d for d in domains}
    target = by_name.get("target")
    if target and len(target.candidates) == 1:
        return target.candidates[0]
    entity_cands = [c for d in domains if d.kind == "entity" for c in d.candidates]
    if len(entity_cands) == 1:
        return entity_cands[0]
    return None


def _build_surface(
    schema: AdaptiveSchema,
    snapshot: StateSnapshot,
    scene: SceneGraph,
    *,
    registry_offers: Sequence[object] = (),
) -> ActionSurface:
    instances: list[ActionInstance] = []
    for action in schema.actions:
        if not action.expected_effects:
            continue
        if not all(eval_precondition(p, snapshot) for p in action.preconditions):
            continue
        domains = tuple(
            BindingDomain(
                param.name,
                param.kind,
                _param_candidates(
                    param.kind,
                    snapshot.entities,
                    scene.nodes,
                    action.selector,
                ),
            )
            for param in action.parameters
        )
        instances.append(
            ActionInstance(
                action_id=action.action_id,
                parameters=action.parameters,
                domains=domains,
                expected_effects=action.expected_effects,
                bound_node_id=_bound_node_id(domains),
            )
        )
    instances.sort(key=lambda i: i.action_id)
    # Registry controls are intentionally provisional.  They become legal on
    # the same frame they are seen, without mutating the durable schema.  The
    # trusted click plan is compiled by ``candidates``; the runtime only
    # contributes a frozen target domain to the legal surface.
    existing = {instance.action_id for instance in instances}
    for offer in registry_offers:
        offer_id = str(getattr(offer, "offer_id", "") or "")
        node_id = str(getattr(offer, "node_id", "") or "")
        if not offer_id or not node_id or offer_id in existing:
            continue
        if not any(node.node_id == node_id for node in scene.nodes):
            continue
        instances.append(
            ActionInstance(
                action_id=offer_id,
                parameters=(ParamSpec("target", "entity"),),
                domains=(BindingDomain("target", "entity", (node_id,)),),
                # A provisional action has no promoted effect yet.  This
                # sentinel keeps invocation validation strict while ensuring
                # promotion, not this transient offer, owns verification.
                expected_effects=(EffectPredicate("__registry_offer__", "changed"),),
                bound_node_id=node_id,
            )
        )
        existing.add(offer_id)
    instances.sort(key=lambda i: i.action_id)
    action_ids = tuple(i.action_id for i in instances)
    return ActionSurface(
        snapshot_id=scene.snapshot_id,
        schema_revision_id=schema.revision.revision_id,
        action_ids=action_ids,
        actions=tuple(instances),
    )


def project(
    scene: SceneGraph,
    schema: AdaptiveSchema,
    *,
    registry_offers: Sequence[object] | None = None,
    forbidden_registry_ids: set[str] | frozenset[str] = frozenset(),
) -> StateSnapshot:
    fields = _collect_global_fields(scene, schema)
    entities = _collect_entities(scene, schema)
    mode_id = _infer_mode(schema, fields, entities)
    draft = StateSnapshot(
        snapshot_id=scene.snapshot_id,
        schema_revision_id=schema.revision.revision_id,
        mode_id=mode_id,
        fields=fields,
        entities=entities,
        surface=ActionSurface("", schema.revision.revision_id, ()),
    )
    if not isinstance(scene, SceneGraph):
        # A few receipt paths pass the already projected snapshot back as the
        # post-capture value.  It has no scene nodes to offer, but remains
        # sufficient for field-only projection and existing schema surfaces.
        registry_offers = ()
    elif registry_offers is None and not schema.actions:
        registry_offers = eligible_registry_offers(scene, forbidden_registry_ids)
    elif registry_offers is None:
        registry_offers = ()
    else:
        registry_offers = tuple(
            offer
            for offer in registry_offers
            if str(getattr(offer, "offer_id", "") or "") not in forbidden_registry_ids
            and str(getattr(offer, "label", "") or "").strip()
        )
    surface = _build_surface(schema, draft, scene, registry_offers=registry_offers)
    return StateSnapshot(
        snapshot_id=scene.snapshot_id,
        schema_revision_id=schema.revision.revision_id,
        mode_id=mode_id,
        fields=fields,
        entities=entities,
        surface=surface,
    )


def project_registry_offers(
    scene: SceneGraph,
    schema: AdaptiveSchema,
    offers: Sequence[object],
    *,
    forbidden_registry_ids: set[str] | frozenset[str] = frozenset(),
) -> StateSnapshot:
    """Explicit projection entry point for the capture/steer Offer phase."""

    return project(
        scene,
        schema,
        registry_offers=offers,
        forbidden_registry_ids=forbidden_registry_ids,
    )


def legal_surface(snapshot: StateSnapshot) -> ActionSurface:
    return snapshot.surface


def select_invocation(snapshot: StateSnapshot) -> ActionInvocation | None:
    if not snapshot.surface.actions:
        return None
    inst = snapshot.surface.actions[0]
    domain_by_name = {d.name: d for d in inst.domains}
    bindings: dict[str, Any] = {}
    for param in inst.parameters:
        if not param.required:
            continue
        domain = domain_by_name.get(param.name)
        if param.name == "target" and inst.bound_node_id:
            val: Any = inst.bound_node_id
        elif param.name == "target":
            return None
        elif domain and len(domain.candidates) == 1:
            val = domain.candidates[0]
        else:
            return None
        if domain and domain.candidates and val not in domain.candidates:
            return None
        bindings[param.name] = val
    return ActionInvocation(
        inst.action_id,
        bindings,
        snapshot.snapshot_id,
        schema_revision_id=snapshot.schema_revision_id,
    )


def _surface_instance(surface: ActionSurface, action_id: str) -> ActionInstance | None:
    for inst in surface.actions:
        if inst.action_id == action_id:
            return inst
    return None


def _binding_type_ok(param: ParamSpec, value: Any) -> bool:
    if param.kind in ("entity", "point", "enum"):
        return isinstance(value, str)
    if param.kind == "scalar":
        return isinstance(value, (int, float))
    return True


def invoke(snapshot: StateSnapshot, invocation: ActionInvocation) -> None:
    if invocation.snapshot_id != snapshot.snapshot_id:
        raise InvocationError(
            f"snapshot_id mismatch: {invocation.snapshot_id!r} != {snapshot.snapshot_id!r}"
        )
    if invocation.schema_revision_id != snapshot.schema_revision_id:
        raise InvocationError(
            "schema_revision_id mismatch: "
            f"{invocation.schema_revision_id!r} != {snapshot.schema_revision_id!r}"
        )
    surface = snapshot.surface
    inst = _surface_instance(surface, invocation.action_id)
    if inst is None and invocation.action_id not in surface.action_ids:
        raise InvocationError(f"action_id not legal: {invocation.action_id!r}")
    if inst is not None:
        if not inst.expected_effects:
            raise InvocationError(f"action has no expected_effects: {invocation.action_id!r}")
        domain_by_name = {d.name: d for d in inst.domains}
        for param in inst.parameters:
            if param.required and param.name not in invocation.bindings:
                raise InvocationError(f"missing binding: {param.name!r}")
            if param.name not in invocation.bindings:
                continue
            val = invocation.bindings[param.name]
            if not _binding_type_ok(param, val):
                raise InvocationError(f"binding type mismatch: {param.name!r}")
            domain = domain_by_name.get(param.name)
            if domain and domain.candidates and val not in domain.candidates:
                raise InvocationError(f"binding not in domain: {param.name!r}={val!r}")
    elif invocation.action_id in surface.action_ids:
        raise InvocationError(f"action surface missing instance: {invocation.action_id!r}")


def execute(
    invocation: ActionInvocation,
    schema: AdaptiveSchema,
    primitives_runner: PrimitivesRunner,
) -> None:
    if (
        invocation.schema_revision_id
        and invocation.schema_revision_id != schema.revision.revision_id
    ):
        raise InvocationError(
            "schema_revision_id mismatch: "
            f"{invocation.schema_revision_id!r} != {schema.revision.revision_id!r}"
        )
    spec = _action_spec(schema, invocation.action_id)
    if spec is None:
        raise InvocationError(f"unknown action_id: {invocation.action_id!r}")
    primitives_runner(spec.primitive_plan, dict(invocation.bindings))


def _action_spec(schema: AdaptiveSchema, action_id: str) -> ActionSpec | None:
    for action in schema.actions:
        if action.action_id == action_id:
            return action
    return None


def _field_value(snapshot: StateSnapshot, pred: EffectPredicate) -> Any:
    if pred.entity_id is not None:
        ent = snapshot.entities.get(pred.entity_id)
        if ent is None:
            return _MISSING
        return ent.get(pred.field_id, _MISSING)
    return snapshot.fields.get(pred.field_id, _MISSING)


_MISSING = object()


def _values_equal(a: Any, b: Any) -> bool:
    return a == b


def _snapshot_body_equal(a: StateSnapshot, b: StateSnapshot) -> bool:
    return (
        a.mode_id == b.mode_id
        and a.fields == b.fields
        and a.entities == b.entities
    )


def _pred_with_bindings(pred: EffectPredicate, bindings: dict | None) -> EffectPredicate:
    if pred.binding and bindings is not None:
        eid = bindings.get(pred.binding)
        if eid is None:
            return pred
        return replace(pred, entity_id=str(eid), binding=None)
    return pred


_OPPOSITE_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "increased": lambda aval, bval: aval < bval,
    "decreased": lambda aval, bval: aval > bval,
}


def contradicted(
    before: StateSnapshot,
    after: StateSnapshot,
    spec: ActionSpec,
    bindings: dict | None = None,
) -> bool:
    """Positive contrary evidence for this after-frame.

    Only ordering predicates define an opposite direction, so a strictly
    reversed value is a contradiction.  Missing, unreadable or disappeared
    readings are never a contradiction: failed perception must not be reported
    as a wrong effect.
    """

    for pred in (_pred_with_bindings(p, bindings) for p in spec.expected_effects):
        opposite = _OPPOSITE_OPS.get(pred.op)
        if opposite is None:
            continue
        if pred.entity_id is not None and pred.entity_id not in after.entities:
            continue
        bval = _field_value(before, pred)
        aval = _field_value(after, pred)
        if bval is _MISSING or aval is _MISSING:
            continue
        try:
            if opposite(aval, bval):
                return True
        except TypeError:
            continue
    return False


def effect_evaluable(
    before: StateSnapshot,
    after: StateSnapshot,
    spec: ActionSpec,
    bindings: dict | None = None,
) -> bool:
    """True when every expected predicate can be read from this after-frame.

    Distinguishes "observed and unchanged" (-> no_effect) from unreadable or
    missing perception (-> unobservable).
    """

    if not spec.expected_effects:
        return False
    preds = [_pred_with_bindings(p, bindings) for p in spec.expected_effects]
    disappeared_ids = {
        p.entity_id for p in preds if p.op == "disappeared" and p.entity_id is not None
    }
    if set(before.entities) - set(after.entities) - disappeared_ids:
        return False
    for pred in preds:
        if pred.op in ("appeared", "disappeared"):
            if pred.entity_id is None:
                return False
            continue
        if pred.entity_id is not None and pred.entity_id not in after.entities:
            return False
        if _field_value(before, pred) is _MISSING or _field_value(after, pred) is _MISSING:
            return False
    return True


def verify(
    before: StateSnapshot,
    after: StateSnapshot,
    spec: ActionSpec,
    bindings: dict | None = None,
) -> bool:
    if not spec.expected_effects:
        return False
    preds = [_pred_with_bindings(p, bindings) for p in spec.expected_effects]
    disappeared_ids = {
        p.entity_id for p in preds if p.op == "disappeared" and p.entity_id is not None
    }
    lost_entities = set(before.entities) - set(after.entities) - disappeared_ids
    if lost_entities:
        return False
    has_entity_lifecycle = any(p.op in ("appeared", "disappeared") for p in preds)
    if _snapshot_body_equal(before, after) and not has_entity_lifecycle:
        return False
    for pred in preds:
        op = pred.op
        if op == "appeared":
            if pred.entity_id is None:
                return False
            if pred.entity_id in before.entities or pred.entity_id not in after.entities:
                return False
            continue
        if op == "disappeared":
            if pred.entity_id is None:
                return False
            if pred.entity_id not in before.entities or pred.entity_id in after.entities:
                return False
            continue
        if pred.entity_id is not None and pred.entity_id not in after.entities:
            return False
        bval = _field_value(before, pred)
        aval = _field_value(after, pred)
        if bval is _MISSING or aval is _MISSING:
            return False
        if op == "changed":
            if _values_equal(bval, aval):
                return False
        elif op == "equals":
            if not _values_equal(aval, pred.value):
                return False
        elif op == "increased":
            try:
                if not (aval > bval):
                    return False
            except TypeError:
                return False
        elif op == "decreased":
            try:
                if not (aval < bval):
                    return False
            except TypeError:
                return False
        elif op == "set":
            if not _values_equal(aval, pred.value):
                return False
        else:
            return False
    return True


def step(
    world: Any,
    schema: AdaptiveSchema,
    invocation: ActionInvocation,
    runner: PrimitivesRunner,
    *,
    assembler: Any | None = None,
    ocr: Any | None = None,
) -> tuple[StateSnapshot, bool]:
    if assembler is not None:
        pixels, _, _ = world.observe()
        if pixels is None:
            raise InvocationError("pixel observation requires PIL")
        _, before_scene = assembler.update(pixels, ocr=ocr)
        before = project(before_scene, schema)
        invoke(before, invocation)
        execute(invocation, schema, runner)
        pixels2, _, _ = world.observe()
        _, after_scene = assembler.update(pixels2, ocr=ocr)
        after = project(after_scene, schema)
    else:
        before_scene = world.observe()[1]
        before = project(before_scene, schema)
        invoke(before, invocation)
        execute(invocation, schema, runner)
        after_scene = world.observe()[1]
        after = project(after_scene, schema)
    spec = _action_spec(schema, invocation.action_id)
    if spec is None:
        return after, False
    ok = verify(before, after, spec, bindings=invocation.bindings)
    return after, ok


def normalized_click_point(scene: SceneGraph, node_id: str) -> tuple[float, float]:
    """Center of node bbox in normalized coordinates (for primitive bindings)."""
    for node in scene.nodes:
        if node.node_id == node_id:
            x0, y0, x1, y1 = node.bbox
            return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    raise InvocationError(f"node_id not in scene: {node_id!r}")


def make_native_primitives_runner(
    owner: Any,
    *,
    get_scene: Callable[[], SceneGraph | None],
) -> PrimitivesRunner:
    """Bounded primitive runner backed by the native input owner."""

    def runner(steps: tuple[PrimitiveStep, ...], bindings: dict[str, Any]) -> None:
        owner.run_plan(steps, bindings, scene=get_scene())

    return runner
