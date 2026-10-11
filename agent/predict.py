"""Game-neutral effect labels and prediction verdicts."""

from agent.observer import changed, cut

EFFECTS = ("place_changes", "text_appears", "screen_changes", "entity_appears",
           "entity_disappears", "view_scrolled", "no_change")


def effects(before: dict, after: dict, res, place_before: str, place_after: str) -> dict:
    """Compare perceptual lists and frames; each emitted effect carries its evidence basis."""
    found, basis = [], {}
    frame_changed = changed(res.before, res.after) > 0
    scene_cut = cut(res.before, res.after, res.blank_seen)
    if scene_cut:
        found.append("place_changes")
        basis["place_changes"] = "pixel"
    if after.get("text", []) and any(t not in before.get("text", []) for t in after["text"]):
        found.append("text_appears")
        # The perception contract has no text boxes, so it cannot localize pixel evidence.
        basis["text_appears"] = "model"
    if frame_changed:
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
            "to": place_after if scene_cut else None}


def verdict(pred: dict | None, outcome: dict | None) -> str:
    if not outcome or not outcome.get("effects"):
        return "unobservable"
    actual = outcome["effects"]
    if not pred or not pred.get("effect"):
        return "unobservable"
    expected = pred["effect"]
    if expected in actual:
        detail = pred.get("detail")
        if detail is not None and expected == "place_changes" and outcome.get("to") != detail:
            return "partial"
        return "hit"
    if actual == ["no_change"]:
        return "miss"
    return "surprise"
