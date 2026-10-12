"""Game-neutral effect labels and prediction verdicts."""

from agent.observer import changed, cut
from agent.memory import conf

EFFECTS = ("place_changes", "text_appears", "screen_changes", "entity_appears",
           "entity_disappears", "view_scrolled", "no_change")
PRED = {"place_changes": "leads_to", "text_appears": "says", "screen_changes": "opens",
        "entity_appears": "reveals", "entity_disappears": "removes",
        "view_scrolled": "moves_view", "no_change": "does_nothing"}


def predict(mem, aff: dict, target_id: str | None, place: str) -> dict | None:
    """Use the strongest live, observed relation attached to this target."""
    if not target_id:
        return None
    effects_by_pred = {predicate: effect for effect, predicate in PRED.items()}
    matches = [row for row in mem.rows("relations")
               if row.get("s") == target_id and row.get("p") in effects_by_pred
               and row.get("src") == "observed" and row.get("state") == "live"]
    if matches:
        row = max(matches, key=conf)
        return {"basis": "instance", "id": row["id"], "effect": effects_by_pred[row["p"]],
                "detail": row["o"], "conf": conf(row)}
    # E4: patterns
    return None


def effects(before: dict, after: dict, res, place_before: str, place_after: str,
            *, place_after_candidate: bool = False, scene_cut: bool | None = None) -> dict:
    """Compare perceptual lists and frames; each emitted effect carries its evidence basis."""
    found, basis = [], {}
    frame_changed = changed(res.before, res.after) > 0
    scene_cut = cut(res.before, res.after, res.blank_seen) if scene_cut is None else scene_cut
    if scene_cut and not place_after_candidate:
        found.append("place_changes")
        basis["place_changes"] = "pixel"
    if after.get("text", []) and any(t not in before.get("text", []) for t in after["text"]):
        found.append("text_appears")
        # The perception contract has no text boxes, so it cannot localize pixel evidence.
        basis["text_appears"] = "model"
    if frame_changed or (scene_cut and place_after_candidate):
        found.append("screen_changes")
        basis["screen_changes"] = "pixel"
    old = {(e.get("kind"), e.get("name")) for e in before.get("entities", [])}
    new = {(e.get("kind"), e.get("name")) for e in after.get("entities", [])}
    if new - old:
        found.append("entity_appears")
        basis["entity_appears"] = "model"
    if old - new:
        found.append("entity_disappears")
        basis["entity_disappears"] = "model"
    if tuple(res.scroll) != (0, 0):
        found.append("view_scrolled")
        basis["view_scrolled"] = "pixel"
    if not found:
        found.append("no_change")
        basis["no_change"] = "pixel"
    return {"effects": found, "basis": basis,
            "to": place_after if scene_cut and not place_after_candidate else None}


def verdict(pred: dict | None, outcome: dict | None, *, blocked: bool = False, inputs: int | None = None) -> str:
    if not outcome or not outcome.get("effects"):
        return "unobservable"
    actual = outcome["effects"]
    if not pred or not pred.get("effect"):
        return "unobservable"
    expected = pred["effect"]
    if blocked:
        unexpected = set(actual) - {expected, "no_change", "screen_changes", "view_scrolled"}
        return "surprise" if unexpected else "miss"
    if expected in actual:
        if inputs == 0 and expected != "no_change": return "miss"
        detail = pred.get("detail")
        if detail is not None and expected == "place_changes" and outcome.get("to") != detail:
            return "partial"
        return "hit"
    if actual == ["no_change"]:
        return "miss"
    return "surprise"
