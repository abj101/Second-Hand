"""Game-neutral effect labels and action results."""

from agent.observer import changed, cut

EFFECTS = ("place_changes", "text_appears", "screen_changes", "entity_appears",
           "entity_disappears", "view_scrolled", "no_change")
PRED = {"place_changes": "leads_to", "text_appears": "says", "screen_changes": "opens",
        "entity_appears": "reveals", "entity_disappears": "removes",
        "view_scrolled": "moves_view", "no_change": "does_nothing"}


def result(status: str, effects: list[str], novel: bool) -> str:
    if novel: return "progress"
    if status in ("blocked", "failed"): return "failed"
    if effects == ["no_change"]: return "no_change"
    return "changed"


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
    if tuple(res.scroll) != (0, 0):
        found.append("view_scrolled")
        basis["view_scrolled"] = "pixel"
    if not found:
        found.append("no_change")
        basis["no_change"] = "pixel"
    return {"effects": found, "basis": basis,
            "to": place_after if scene_cut and not place_after_candidate else None}
