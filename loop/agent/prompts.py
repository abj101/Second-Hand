from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from loop.memory.notebook import (
    listing_backdrop_close,
    listing_has_dialog,
    listing_has_named,
    overlay_screen,
)
from loop.memory.skills import Skill
from loop.os.frame_model import (
    GENERIC_CONTROL_ROLES,
    control_key_from_row,
    paginated_tutorial_active,
    quantize_coord,
    quantized_bounds_key,
    row_bounds,
    surface_fingerprint,
)

AUTHORING_PRIOR = (
    "Skills use unlabeled device chords. Schema: recognize / avoid_or_do "
    "(chord JSON + pitfall without game verbs) / end. Merge overlapping skills. "
    "Never write dodge, roll, flask, attack, sprint."
)

LEARN_PRIOR = (
    "You are playing this game from pixels. Make progress; do not catalog every control. "
    "Every step you MUST emit a non-empty unlabeled device chord. "
    "Idle, end, or empty JSON is invalid. "
    "Screen layout: top strip is compass/bars (letters there are headings, not sticks); "
    "bottom-left is loadout/skill name (not a button). "
    "Compass letters N/E/S/W are headings, not stick_l_* device names. "
    "stick_l moves the body. stick_r only moves the camera. "
    "You see a 2D screenshot, not 3D. Range tags near/mid/far are box size and "
    "how low the box sits: large or low on screen = near (short walk or stop); "
    "small or high = far (long walk ~1600ms). "
    "A golden trail / loot glow is a pickup on a corpse — walk to it if far, stop if a PROMPT banner is up. "
    "Doorway, arch, and other bright patches are optional hints, not a required walk. "
    "Do not chase architecture into a wall. Do not tap a painted box. "
    "Low y is sky — do not pitch the camera at sky lights. "
    "A motion-mad line (if present) is CV text about the last chord — not a device named burst. "
    "If the same stick_l walk keeps returning no-change, turn with stick_r or back with stick_l_s. "
    "A doorway is not an interact prompt. If the target is far, walk closer. "
    "If the PROMPT banner already shows a bar or verb, stop walking; "
    "a face tap with no copy is a jump. "
    "Do not invent game-specific verbs."
)

SYSTEM_NO_SKILL = f"""{LEARN_PRIOR}
JSON only.
{{"thought": "what I am doing", "stick_l": "n", "stick_r": null, "held": [], "tapped": [], "duration_ms": 800}}
You may also send {{"thought": "string", "action": "device_name", "duration_ms": 400}}
or {{"thought": "string", "invoke_gsm": "explore"}}.
No tools. Journal lines are sensor facts, not a control wiki.
"""

SYSTEM_DUMP = f"""{LEARN_PRIOR}
Pick exactly one action name from the provided list, or call a memory tool.
JSON only.
{{"thought": "what I am doing", "action": "device_name", "duration_ms": 400}}
or {{"thought": "string", "tool": "recall"}}
or {{"thought": "string", "tool": "remember", "note": "what this screen taught you"}}
"""

SYSTEM_RATCHET = f"""{LEARN_PRIOR}
At most one skill may be shown. Follow it if the screen matches recognize.
If no skill matches, still emit a non-empty play chord or invoke_gsm explore.
JSON only.
{AUTHORING_PRIOR}
{{"thought": "what I am doing", "stick_l": "n", "stick_r": null, "held": [], "tapped": [], "duration_ms": 800}}
You may also send {{"thought": "string", "tool": "recall", "query": "doorway", "stick_r": "e", "held": [], "tapped": [], "duration_ms": 400}}.
Recall is tag lookup on the journal (kind, heading, glyph). Still emit a chord.
"""

TURN_LEARN_PRIOR = (
    "You are playing a turn-based game from pixels. Win the run. "
    "Click printed play controls (menus, cards, enemies). "
    "Do not hover every icon or catalog the UI. "
    "You keep a notebook. Every JSON has goal (this-step move) and note "
    "(one binding, or \"\"). note is a fact such as "
    "\"mk_1 is Singleplayer on title\", not a recap of thought. "
    "Thought names the move. "
    "The image has a 0-1000 grid and numbered cyan badges on likely controls. "
    "Click a badge center with {\"thought\": \"...\", \"goal\": \"...\", \"note\": \"...\", \"mark\": 4}. "
    "If Marks: already names the word, click it — omit hover. "
    "Hover unlabeled marks only if you need the name: {\"hover\": true, \"mark\": 6}. "
    "If no badge sits on the control, emit "
    "{\"thought\": \"...\", \"goal\": \"...\", \"note\": \"\", \"gx\": 410, \"gy\": 750} "
    "(integers 0-1000, 0,0 is top-left). "
    "Keys only: {\"action\": \"enter\"} (also escape, space, arrows). "
    "Do not use named layout slots. "
    "JSON goal is this-step move. Follow the Notebook Goal in the user prompt. "
    "choice is an optional short label of the control you clicked. "
    "The user prompt is host See (screen, HUD, Marks) — do not re-emit hp, energy, or screen. "
    "Never reuse example numbers. "
    "JSON only. Idle or empty JSON is invalid. Do not invent game verbs."
)

SYSTEM_TURN_NO_SKILL = f"""{TURN_LEARN_PRIOR}
No tools. Notebook is in the user prompt.
{{"thought": "what I am doing", "goal": "start a run", "note": "mk_1 is Singleplayer on title", "mark": 4, "choice": "Singleplayer"}}
or {{"thought": "string", "goal": "start a run", "note": "", "gx": 410, "gy": 750}}
or {{"thought": "string", "goal": "dismiss overlay", "note": "", "action": "enter"}}
or {{"thought": "string", "goal": "read name", "note": "", "hover": true, "mark": 6}}
"""

SYSTEM_TURN_DUMP = f"""{TURN_LEARN_PRIOR}
Tools: recall, remember. Prefer goal/note in the same click JSON; tools are optional.
{{"thought": "what I am doing", "goal": "start a run", "note": "", "mark": 4}}
or {{"thought": "string", "hover": true, "mark": 6, "choice": "what the tooltip said"}}
or {{"thought": "string", "tool": "recall", "gx": 410, "gy": 750}}
or {{"thought": "string", "tool": "remember", "note": "mk_5 is Continue on title", "gx": 500, "gy": 180}}
"""

SYSTEM_TURN_RATCHET = f"""{TURN_LEARN_PRIOR}
At most one skill may be shown. Follow it if the screen matches recognize.
{AUTHORING_PRIOR}
{{"thought": "what I am doing", "goal": "start a run", "note": "", "mark": 4}}
You may also send {{"thought": "string", "tool": "recall", "query": "kind heading", "gx": 410, "gy": 750}}.
"""

PERCEIVE_SYSTEM = """Read the HUD on this game screenshot. JSON only.
{"health_frac": 0.0, "stamina_frac": 0.0, "runes": null, "fail_screen": false}
Use null when a bar or counter is absent. health_frac and stamina_frac are 0..1.
"""

SCENE_PERCEIVE_V1 = """You are the eyes for Slay the Spire 2. Transcribe the screenshot into structured text. JSON only.
{
  "scene": "2-4 sentences: layout, characters, what screen this is",
  "screen": "title|select|tutorial|map|combat|shop|rest|reward|pause|death|other",
  "controls": [{"label": "exact visible text on a clickable", "role": "menu|dialog|card|enemy|button|other"}],
  "dialog": {"prompt": "modal question if any", "options": ["Yes", "No"]},
  "combat": {"enemies": ["names"], "hand": ["card names"], "hp": "visible fraction or null", "energy": "visible number or null"},
  "tutorial_text": "one key instruction sentence if tutorial, else null"
}
Rules:
- Read every visible button/menu/card/enemy name verbatim in controls or combat/hand.
- No actions, no coordinates, no mark ids, no grid.
- Do not invent stats; leave null when absent.
- Host OCR in the user prompt is a hint for text you cannot read — prefer what you see in the image.
"""

SCENE_PERCEIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {
            "type": "string",
            "enum": [
                "title",
                "select",
                "tutorial",
                "map",
                "combat",
                "shop",
                "rest",
                "reward",
                "pause",
                "death",
                "other",
            ],
        },
        "tutorial": {
            "type": ["object", "null"],
            "properties": {
                "active": {"type": "boolean"},
                "title": {"type": "string"},
                "step": {"type": "integer"},
                "total": {"type": "integer"},
            },
        },
        "turn": {"type": ["string", "null"], "enum": ["ours", "theirs", "unknown", None]},
        "controls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["card", "button", "menu", "dialog", "enemy", "other"],
                    },
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                    "source": {"type": "string", "enum": ["detector", "ocr", "model"]},
                },
                "required": ["label", "role", "confidence"],
            },
        },
        "combat": {
            "type": ["object", "null"],
            "properties": {
                "active": {"type": "boolean"},
                "confidence": {"type": "string", "enum": ["high", "low"]},
            },
        },
        "dialog": {
            "type": ["object", "null"],
            "properties": {
                "prompt": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string", "enum": ["high", "low"]},
            },
        },
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "uncertain": {"type": "array", "items": {"type": "string"}},
        "unresolved": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Mark ids or regions LLM1 could not confidently label. Empty list is valid.",
        },
        "legal_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mark": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["legal", "illegal", "uncertain"]},
                    "confidence": {"type": "number"},
                },
                "required": ["mark", "verdict"],
            },
            "description": "Per-mark legality from CV affordance hints; attach verdict to labeled entity.",
        },
        "hud": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "role": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                },
                "required": ["label"],
            },
        },
        "hand_cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                    "name_source": {"type": "string", "enum": ["ocr", "dino", "game_state", "unresolved"]},
                    "role": {"type": "string", "enum": ["card"]},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                    "source": {"type": "string", "enum": ["detector", "ocr", "model"]},
                    "playable": {"type": "boolean"},
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                },
                "required": [
                    "id", "label", "name", "name_source", "role", "confidence", "playable", "bbox",
                ],
            },
        },
        "enemies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                    "name_source": {"type": "string", "enum": ["ocr", "dino", "game_state", "unresolved"]},
                    "role": {
                        "type": "string",
                        "enum": ["enemy", "character", "unknown_character"],
                    },
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                    "inspectable": {"type": "boolean"},
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                },
                "required": [
                    "role", "confidence", "inspectable", "bbox", "label", "name", "name_source",
                ],
            },
        },
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                    "name_source": {"type": "string", "enum": ["ocr", "dino", "game_state", "unresolved"]},
                    "role": {
                        "type": "string",
                        "enum": ["enemy", "character", "unknown_character"],
                    },
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                    "inspectable": {"type": "boolean"},
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                },
                "required": [
                    "role", "confidence", "inspectable", "bbox", "label", "name", "name_source",
                ],
            },
        },
        "overlays_active": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "screen", "controls", "visible_text", "legal_actions", "unresolved",
        "characters", "enemies", "hand_cards",
    ],
}

PERCEPTION_SCHEMA_VERSION = "v5"
# Short alias for integrations that refer to the LLM1 payload as "perceive".
PERCEIVE_SCHEMA_VERSION = PERCEPTION_SCHEMA_VERSION
# Alias retained for callers that name the schema after its scene payload.
SCENE_PERCEIVE_SCHEMA_VERSION = PERCEPTION_SCHEMA_VERSION
SCENE_PERCEIVE_PROMPT_VERSION = "v5"
LLM1_OBSERVER_PROMPT_VERSION = SCENE_PERCEIVE_PROMPT_VERSION

SCENE_PERCEIVE_CANONICAL_EXAMPLE = {
    "screen": "combat",
    "tutorial": None,
    "turn": "ours",
    "controls": [
        {"label": "Strike", "role": "card", "confidence": "high", "source": "ocr"},
        {"label": "End Turn", "role": "button", "confidence": "high", "source": "ocr"},
    ],
    "combat": {"active": True, "confidence": "high"},
    "dialog": None,
    "visible_text": [],
    "uncertain": [],
    "unresolved": [],
    "legal_actions": [],
    "hud": [],
    "hand_cards": [
        {
            "id": "card_1",
            "label": "Strike",
            "name": "Strike",
            "name_source": "ocr",
            "role": "card",
            "confidence": "high",
            "playable": True,
            "bbox": [200, 780, 320, 980],
        }
    ],
    "characters": [
        {
            "label": "Ironclad",
            "name": "Ironclad",
            "name_source": "ocr",
            "role": "character",
            "confidence": "high",
            "inspectable": False,
            "bbox": [80, 420, 220, 780],
        }
    ],
    "enemies": [
        {
            "label": None,
            "name": None,
            "name_source": "unresolved",
            "role": "enemy",
            "confidence": "high",
            "inspectable": True,
            "bbox": [680, 500, 820, 720],
        }
    ],
    "overlays_active": [],
}


def _canonical_example_json() -> str:
    import json

    return json.dumps(SCENE_PERCEIVE_CANONICAL_EXAMPLE, ensure_ascii=False, separators=(",", ":"))


SCENE_PERCEIVE_SYSTEM = """You are LLM1, an observer for one Slay the Spire 2 screenshot.
Report what is visible now. Do not recommend, rank, select, click, target, or
otherwise advise an action. JSON only; no prose or markdown fences.

Schema version: """ + PERCEPTION_SCHEMA_VERSION + """. Emit every required field, using
[] or null when it is absent. Verify OCR/detector candidates against pixels:
keep verified candidates, correct visible mistakes, drop false positives, and
add directly visible omissions. Candidate metadata is evidence, not authority.

OUTPUT SHAPE:
{
  "screen": "title|select|tutorial|map|combat|shop|rest|reward|pause|death|other",
  "tutorial": {"active": true, "title": "...", "step": 1, "total": 3} | null,
  "turn": "ours|theirs|unknown" | null,
  "controls": [
    {"label": "...", "role": "card|button|menu|dialog|enemy|other",
     "confidence": "high|low", "source": "detector|ocr|model"}
  ],
  "combat": {"active": true, "confidence": "high|low"} | null,
  "dialog": {"prompt": "...", "options": ["..."], "confidence": "high|low"} | null,
  "visible_text": ["..."],
  "uncertain": ["..."],
  "unresolved": [],
  "legal_actions": [
    {"mark": "mk_7", "verdict": "legal|illegal|uncertain", "confidence": 0.86}
  ],
  "hud": [],
  "hand_cards": [
    {"id": "card_1", "role": "card", "confidence": "high", "playable": true,
     "bbox": [x0,y0,x1,y1], "label": "...", "name": "...", "name_source": "ocr"}
  ],
  "characters": [
    {"role": "character", "confidence": "high", "inspectable": false,
     "bbox": [x0,y0,x1,y1], "label": "...", "name": "...", "name_source": "ocr"}
  ],
  "enemies": [
    {"role": "enemy", "confidence": "high", "inspectable": true,
     "bbox": [x0,y0,x1,y1], "label": null, "name": null, "name_source": "unresolved"}
  ],
  "overlays_active": []
}

Required lists on every response: legal_actions, unresolved, hand_cards, characters, enemies.
Unknown names use label=null, name=null, name_source="unresolved" — never a second convention.
Do not invent names. Compact JSON.

CANONICAL EXAMPLE (structure only; do not copy its labels):
""" + _canonical_example_json() + """

OBSERVATION RULES:
- `controls` records visible clickable/playable UI only; body copy, legends,
  and other non-clickable text go in `visible_text`. Do not include HUD
  numbers in either.
- `source` is detector/ocr/model. Confidence is high only for clear evidence.
- MODALS AND DIMMED BACKGROUNDS: classify the foreground overlay; record only
  foreground controls. Illustrations and dimmed background elements are not controls.
- ICON-ONLY CONTROLS: record a visible icon using its detector label when one
  exists; otherwise use a short descriptive label with low confidence.
- Put ambiguous text/regions in `uncertain` and `unresolved`. `unresolved` is
  required even when empty.
- `legal_actions` is an observation of supplied CV affordance evidence, not a
  recommendation. Add a row only for a mark that can be associated with a
  visible entity; use `uncertain` when evidence is insufficient.
- `hud` is chrome, `hand_cards` is the hand band, and `characters` contains
  every visible creature-like sprite. `enemies` is the hostile subset. Mirror
  observed cards/enemies into `controls` when they are actually active.
- Emit one `hand_cards` row per physical card, including duplicate names.
  Assign left-to-right ids card_1, card_2, ... and include the visible bbox
  [x0,y0,x1,y1] on the 0-1000 grid. Never infer coordinates from a card name.
- Unknown sprites must remain unknown: role `unknown_character` or `enemy`,
  low or high confidence as evidence allows, inspectable=true, label=null,
  name=null, name_source="unresolved", bbox [x0,y0,x1,y1] on the 0-1000 grid.
  Never invent a canonical name. A specific entity name needs explicit OCR,
  detector/DINO, or game-state evidence and its matching `name_source`.
- Examples demonstrate JSON structure only; never copy their semantic labels.
"""

FEW_SHOT_EXAMPLE_TUTORIAL = {
    "input_candidates": {
        "ocr_text": [
            "Defeat enemies by playing cards from your hand!",
            "Strike",
            "Defend",
            "End Turn",
            "Got it",
        ],
        "detector_labels": [
            {"label": "Strike", "role_guess": "card"},
            {"label": "Defend", "role_guess": "card"},
            {"label": "enemy_1", "role_guess": "enemy"},
            {"label": "Confirm", "role_guess": "button"},
        ],
    },
    "expected_output": {
        "screen": "tutorial",
        "tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3},
        "turn": "ours",
        "controls": [
            {"label": "Strike", "role": "card", "confidence": "high", "source": "detector"},
            {"label": "Defend", "role": "card", "confidence": "high", "source": "detector"},
            {"label": "enemy_1", "role": "enemy", "confidence": "low", "source": "detector", "name_source": "unresolved"},
            {"label": "End Turn", "role": "button", "confidence": "high", "source": "ocr"},
        ],
        "combat": {"active": True, "confidence": "high"},
        "dialog": None,
        "visible_text": ["Defeat enemies by playing cards from your hand!"],
        "uncertain": [],
        "unresolved": [],
        "legal_actions": [],
        "hud": [],
        "hand_cards": [],
        "characters": [],
        "enemies": [],
        "overlays_active": ["Combat Basics"],
    },
    "note": (
        "Confirm was a detector candidate but not visible in the image, so it "
        "was dropped rather than kept on the detector's say-so -- this replaces "
        "the old hardcoded 'don't list Confirm on this tutorial' rule; it now "
        "falls out of the verify step for every screen. enemy_1 is a controls "
        "entry with role=enemy, not a separate enemies list."
    ),
}

FEW_SHOT_EXAMPLE_MAP = {
    "input_candidates": {
        "ocr_text": ["Unknown", "Merchant", "Elite", "Treasure", "Map"],
        "detector_labels": [
            {"label": "node_3", "role_guess": "button"},
            {"label": "node_7", "role_guess": "button"},
        ],
    },
    "expected_output": {
        "screen": "map",
        "tutorial": None,
        "turn": None,
        "controls": [
            {"label": "node_3", "role": "button", "confidence": "low", "source": "detector"},
            {"label": "node_7", "role": "button", "confidence": "high", "source": "detector"},
        ],
        "combat": None,
        "dialog": None,
        "visible_text": ["Unknown", "Merchant", "Elite", "Treasure"],
        "uncertain": ["node_3"],
        "unresolved": ["node_3"],
        "legal_actions": [],
        "hud": [],
        "hand_cards": [],
        "characters": [],
        "enemies": [],
        "overlays_active": [],
    },
    "note": (
        "Unknown/Merchant/Elite/Treasure are Legend text describing icon types, "
        "not clickable labels themselves -- they stay in visible_text only, "
        "never controls, per the general rule (no map-specific exception "
        "needed). node_3's detector box was hard to confirm cleanly against "
        "the image, so it's kept but marked low confidence and flagged in "
        "uncertain, rather than silently dropped or silently trusted."
    ),
}

FEW_SHOT_EXAMPLE_DIALOG = {
    "input_candidates": {
        "ocr_text": ["Skip this card?", "Yes", "No"],
        "detector_labels": [
            {"label": "Yes", "role_guess": "dialog"},
            {"label": "No", "role_guess": "dialog"},
        ],
    },
    "expected_output": {
        "screen": "reward",
        "tutorial": None,
        "turn": None,
        "controls": [
            {"label": "Yes", "role": "dialog", "confidence": "high", "source": "detector"},
            {"label": "No", "role": "dialog", "confidence": "high", "source": "detector"},
        ],
        "combat": None,
        "dialog": {"prompt": "Skip this card?", "options": ["Yes", "No"], "confidence": "high"},
        "visible_text": ["Skip this card?"],
        "uncertain": [],
        "unresolved": [],
        "legal_actions": [],
        "hud": [],
        "hand_cards": [],
        "characters": [],
        "enemies": [],
        "overlays_active": [],
    },
    "note": (
        "Yes/No appear in both `controls` (so they're clickable) and "
        "`dialog.options` (so the modal's Q/A structure is legible) -- that's "
        "intentional duplication, not redundant restatement: controls answers "
        "'what can be clicked', dialog answers 'what is being asked', and LLM2 "
        "uses them for different things."
    ),
}

FEW_SHOT_EXAMPLE_TUTORIAL_PAGINATED = {
    "input_candidates": {
        "ocr_text": [
            "Combat Basics",
            "Cards require Energy to play.",
            "Once you are out, end your turn.",
            "Strike",
            "Defend",
            "End Turn",
            "enemy_1",
            "Deal 6 damage.",
        ],
        "detector_labels": [
            {"label": "Prev", "role_guess": "button"},
            {"label": "Next", "role_guess": "button"},
            {"label": "Strike", "role_guess": "card"},
            {"label": "enemy_1", "role_guess": "enemy"},
            {"label": "End Turn", "role_guess": "button"},
        ],
    },
    "expected_output": {
        "screen": "tutorial",
        "tutorial": {"active": True, "title": "Combat Basics", "step": 2, "total": 3},
        "turn": None,
        "controls": [
            {"label": "Prev", "role": "button", "confidence": "high", "source": "detector"},
            {"label": "Next", "role": "button", "confidence": "high", "source": "detector"},
        ],
        "combat": {"active": True, "confidence": "low"},
        "dialog": None,
        "visible_text": [
            "Cards require Energy to play.",
            "Once you are out, end your turn.",
        ],
        "uncertain": [],
        "unresolved": [],
        "legal_actions": [],
        "hud": [],
        "hand_cards": [],
        "characters": [],
        "enemies": [],
        "overlays_active": ["Combat Basics"],
    },
    "note": (
        "Paginated tutorial overlay (step 2 of 3): screen is tutorial, not combat, "
        "even though a real fight is visible dimmed behind the panel. The Strike "
        "card and enemy_1 in the explanatory diagram are illustrations inside the "
        "overlay -- not playable controls. Strike/Defend/End Turn/enemy_1 in the "
        "dimmed background are likewise inert while the overlay is up -- drop them "
        "from controls. Only the Prev/Next nav arrows on the overlay are interactive "
        "(icon-only controls with detector labels)."
    ),
}

SCENE_PERCEIVE_FEW_SHOTS: tuple[dict[str, Any], ...] = (
    FEW_SHOT_EXAMPLE_TUTORIAL,
    FEW_SHOT_EXAMPLE_TUTORIAL_PAGINATED,
    FEW_SHOT_EXAMPLE_MAP,
    FEW_SHOT_EXAMPLE_DIALOG,
)


def _format_few_shot_example(example: dict[str, Any]) -> str:
    import json

    inp = example["input_candidates"]
    out = example["expected_output"]
    lines = [
        "EXAMPLE:",
        f"OCR candidates: {json.dumps(inp['ocr_text'], separators=(',', ':'))}",
        f"Detector candidates: {json.dumps(inp['detector_labels'], separators=(',', ':'))}",
        f"Correct output: {json.dumps(out, separators=(',', ':'))}",
    ]
    note = example.get("note")
    if note:
        lines.append(f"Why: {note}")
    return "\n".join(lines)


def scene_perceive_system() -> str:
    """SCENE_PERCEIVE_SYSTEM plus few-shot verify-don't-generate examples."""
    blocks = "\n\n".join(_format_few_shot_example(ex) for ex in SCENE_PERCEIVE_FEW_SHOTS)
    return (
        SCENE_PERCEIVE_SYSTEM
        + "\n\nFEW-SHOT EXAMPLES (verify-don't-generate pattern):\n\n"
        + blocks
    )


_OCR_STAT_RE = re.compile(r"\b\d+\s*/\s*\d+\b")
_OCR_TITLE_PHRASE_RE = re.compile(r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+")
_OCR_MARKER_PHRASES = (
    "got it",
    "end turn",
    "singleplayer",
    "multiplayer",
    "combat basics",
    "map basics",
    "skip this card",
)

_SKIP_CLICKABLE_LABELS = frozenset({"icon", "locked", "character", "button", "buttons", "menu"})
_MAP_OCR_SKIP = frozenset({
    "share",
    "unknown",
    "merchant",
    "treasure",
    "rest",
    "enemy",
    "elite",
    "event",
    "shop",
    "legend",
})

REASON_TURN_PRIOR = (
    "You are the strategist for a turn-based deckbuilder. Win the run. "
    "Each turn you get: State (screen/turn/tutorial step -- authoritative, trust it over "
    "anything else in context including journal or memory), Stats (Host HUD numbers -- "
    "authoritative for hp/energy/etc), Allowed controls (the ONLY legal vocabulary for "
    "choice -- copy exactly), Allowed targets (enemy labels visible this turn -- the ONLY "
    "legal vocabulary for target), Inspectable targets (unknown/unidentified creatures — "
    "hover to learn their name before attacking), Unconfirmed labels/targets (seen but not "
    "verified -- never choose or target these directly), Visible text (context only, not "
    "clickable), and Journal (past strategy notes -- ideas only, no label in it is legal to "
    "click or target just because it's mentioned there). "
    "Decide the best move. Combat actions: ATTACK (play card + named target), INSPECT "
    '(hover an Inspectable target to read tooltip/name — emit {"action":"inspect","target":'
    '"unknown_character_1"} or {"hover":true,"target":"unknown_character_1"}), or IGNORE '
    "(End Turn / Defend when inspecting is not useful). When Allowed targets only lists "
    "unknown/unidentified entries, INSPECT before ATTACK. "
    'Play chord shape: {"thought": "strategy", "goal": "this-step move", "note": "", '
    '"choice_id":"card_1","choice": "Strike", "target_id":"enemy_1", "target": "unidentified_enemy"}. '
    'choice_id is required when Card instances lists the same label more than once. '
    'target_id is required for targeted cards when Target instances exist. '
    'choice/target labels are audit-only; never copy mark_N or mk_N. '
    'When Allowed targets is non-empty and '
    'choice is an attack card (not Defend), target is REQUIRED. Omit target only for '
    'cards that need no enemy (e.g. Defend). '
    'Inspect shape: {"thought": "need name", "goal": "read tooltip", "action": "inspect", '
    '"target": "unknown_character_1"}. '
    "CONFLICT RESOLUTION, when sources disagree: Allowed controls / Allowed targets win for "
    "what's clickable or targetable -- if Journal, Visible text, or memory imply a label "
    "that isn't in this turn's Allowed controls or Allowed targets, it does not exist right "
    "now. Host HUD wins for numbers. The State line wins for screen/turn/tutorial step over "
    "anything older. "
    "If your intended choice or target isn't in its Allowed list, or that list is empty, or "
    "the label you want is only Unconfirmed -- pick a different move from Allowed controls "
    "or omit target. The harness may rescan perception when you emit an illegal choice, but "
    "never guess, and never promote a label from Unconfirmed, Journal, or memory into "
    "choice or target without it appearing in this turn's Allowed list. "
    "Tools: memory_recall, memory_remember, memory_notebook, request_recheck, "
    "targeting_options, targeting_validate. "
    "Tool policy: "
    "(1) Use memory_recall when you want prior strategy for this screen -- any label it "
    "surfaces still has to be in this turn's Allowed controls/targets before you can choose it. "
    "(2) Use memory_remember to store a binding note after you learn a control name. "
    "(3) Use memory_notebook to refresh the standing notebook block. "
    "(4) Use request_recheck when unresolved marks exist, Allowed lists seem incomplete, "
    "or your confidence is low — before committing to a choice/target. "
    "You also receive a marked screenshot (numbered marks) as a verification aid alongside "
    "the structured scene JSON. JSON remains primary; use the image to catch omissions. "
    "Vision/HUD tools beyond recheck are not available — State, Stats, and Allowed "
    "controls/targets in the user prompt are authoritative for this turn. "
    "Never rename or resolve any `unknown_*` entity. Treat perception labels as "
    "authoritative unless a later tool observation explicitly supplies the name. "
    "Every turn, output exactly one tool call or exactly one play chord -- never both, never "
    "neither. Idle JSON is invalid."
)

REASON_ACT_SYSTEM = """You are the ACT strategist for a turn-based deckbuilder.
The deterministic router has established that the action surface is confirmed.
Choose exactly one action that advances the run. JSON only; no markdown.

Trust State and Stats over older context. `Allowed controls` is the complete and
only vocabulary for `choice`; copy one label exactly. `Allowed targets` is the
complete and only vocabulary for `target`; copy exactly. Journal and memory are
strategy context only and cannot create a current control or target. Never use
an icon, unknown object, unconfirmed label, stale mark, or coordinate.

For an attack card, include a target when Allowed targets is non-empty. Omit
target for non-targeted cards such as Defend and for menu/dialog controls.
Do not inspect, hover, call request_recheck, or rename an unknown; Explore owns
all observation work. Emit one confirmed action, never an empty response.

Shapes:
{"thought":"brief strategy","goal":"this-step result","note":"","choice_id":"card_1","choice":"Strike","target_id":"enemy_1","target":"unidentified_enemy"}
{"thought":"brief strategy","goal":"advance","note":"","choice":"Continue"}
choice_id is required when Card instances lists duplicates. target_id is required
for targeted cards when Target instances is non-empty. Labels are audit-only.
Never emit mark_N / mk_N as choice, target, choice_id, or target_id.
"""

REASON_EXPLORE_SYSTEM = """You are the exploration reasoner in an unknown-first
two-agent system. You receive a scene observation, unresolved regions, and
inspectable unknown entities. Do not select a game control or give action
advice. Decide only whether more observation is needed and what evidence would
resolve uncertainty. Never rename an unknown entity from memory or convention.
JSON only. Emit exactly one observation request:
{"tool":"request_recheck","region_or_entity":"exact unresolved mark or unknown entity"}
or, for an inspectable entity:
{"tool":"inspect_object","mark_id":123,"run_id":"current frame run_id","step_id":4}
`mark_id`, `run_id`, and `step_id` must come from the current-frame inspection contract.
Use only current observation evidence. These are the only permitted tools."""

# Existing runtime imports use this name. The action phase remains its behavior.
REASON_TURN_SYSTEM = REASON_ACT_SYSTEM

SYNTH_SYSTEM = f"""Author one skill from a failure cluster. JSON only.
{AUTHORING_PRIOR}
{{"id": "short_id", "recognize": "when this screen cluster repeats", "avoid_or_do": {{"chord": {{"stick_l": "n", "held": [], "tapped": [], "duration_ms": 400}}, "pitfall": "do not repeat the failed device names"}}, "end": "bars recovered or the scene changed"}}
"""


def system_for(arm: str, *, pace: str = "realtime") -> str:
    if pace == "turn":
        if arm == "dump_all":
            return SYSTEM_TURN_DUMP
        if arm == "ratchet":
            return SYSTEM_TURN_RATCHET
        return SYSTEM_TURN_NO_SKILL
    if arm == "dump_all":
        return SYSTEM_DUMP
    if arm == "ratchet":
        return SYSTEM_RATCHET
    return SYSTEM_NO_SKILL


NOTEBOOK_SCHEMA_VERSION = "v1"


def prompt_id(*, arm: str, pace: str = "realtime", architecture: str = "overlay") -> str:
    """Stable hash of frozen system prior + notebook template version."""
    if pace == "turn" and architecture == "two_stage":
        text = scene_perceive_system() + REASON_EXPLORE_SYSTEM + REASON_ACT_SYSTEM
    else:
        text = system_for(arm, pace=pace)
    text = text + "\n# notebook_schema=" + NOTEBOOK_SCHEMA_VERSION
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def two_stage_system_for(*, arm: str) -> tuple[str, str]:
    """Return (perceive_system, reason_system) for two-stage STS2 collect."""
    del arm
    return scene_perceive_system(), REASON_ACT_SYSTEM


def lock_evidence_line(*, glyph: str | None = None, kinds: Sequence[str] | None = None) -> str:
    """Sensor-only lock fact for later ticks. Not a thought memory."""
    face = (glyph or "").strip().lower() or "face"
    where = ", ".join(k for k in (kinds or []) if k) or "this interact"
    return (
        f"CV: glyph '{face}' already failed to clear at {where}. "
        "Treat it as a condition, not Open. Look (stick_r) or a different heading. "
        "A face tap with no prompt bar is a jump, not Open."
    )


def approach_evidence_line() -> str:
    """CV contrast without OCR copy — walk in, do not tap."""
    return (
        "CV contrast in the prompt band has no readable verb. "
        "Target is still far. Short stick_l toward it; do not tap a face "
        "until the PROMPT inset shows copy."
    )


def close_prompt_line() -> str:
    """CV/DINO thinks a bar is up, but OCR is empty — do not walk through it."""
    return (
        "Interact range: the PROMPT banner is the lower strip. OCR may be empty. "
        "Do not walk forward into the object. Read the banner; tap a face only if a verb is visible."
    )


def user_prompt(
    *,
    actions: str,
    memory_snippets: list[str] | None = None,
    allow_memory: bool = False,
    skill: Skill | None = None,
    arm: str = "no_skill",
    recent_chords: list[str] | None = None,
    last_pixels_changed: bool | None = None,
    last_mad: float | None = None,
    last_had_prompt: bool = False,
    prompt_box: bool | None = None,
    prompt_glyph: str | None = None,
    bright_patches: str | None = None,
    burst_caption: str | None = None,
    second_image: str | None = None,
    working_block: str | None = None,
    grounded: str | None = None,
    prompt_text: str | None = None,
    prompt_locked: bool = False,
    lock_note: str | None = None,
    approach_note: str | None = None,
    close_prompt_note: str | None = None,
    journal_block: str | None = None,
    recall_hits: list[str] | None = None,
) -> str:
    lines = [
        f"Device names: {actions}",
        "Bottom-left loadout names are not buttons. Top compass letters are not sticks.",
        "Every step emit a non-empty chord. Empty JSON is invalid.",
        "For stick_l walks, set duration_ms from the range tag "
        "(~400 near, ~900 mid, ~1600 far). Taps and stick_r stay ~400.",
    ]
    if second_image:
        lines.append(second_image)
    if burst_caption:
        lines.append("Last chord CV (text only, not an image strip): " + burst_caption)
    if prompt_text:
        lines.append(
            f'PROMPT TEXT: "{prompt_text}". '
            "If this is a condition (need item / only those who …), "
            "do not mash the face; look or walk to another ranked goal."
        )
    elif prompt_locked:
        lines.append(
            "Prompt bar still up after the same face. Treat as a condition "
            "(need item), not Open. Do not mash; look or walk to another ranked goal."
        )
    if lock_note:
        lines.append(lock_note)
    if approach_note:
        lines.append(approach_note)
    if close_prompt_note:
        lines.append(close_prompt_note)
    if grounded:
        lines.append(grounded)
        if prompt_locked or lock_note:
            lines.append(
                "Interact is a condition, not a tap. Look or walk to another ranked goal. "
                "Painted box is a hint, not a button."
            )
            if "loot glow" in grounded.lower() or "golden trail" in grounded.lower():
                lines.append(
                    "Loot glow is still a pickup. Walk stick_l toward it."
                )
        elif prompt_text or prompt_box or close_prompt_note:
            lines.append(
                "A prompt is up. Read the PROMPT banner. Do not walk through it."
            )
            if "loot glow" in grounded.lower() or "golden trail" in grounded.lower():
                lines.append(
                    "Loot glow may be this pickup. Tap only if a verb is visible."
                )
        elif "loot glow" in grounded.lower() or "golden trail" in grounded.lower():
            lines.append(
                "Loot glow is a pickup. Walk stick_l toward it. "
                "Do not ignore it for a locked or distant door. Painted box is not a button."
            )
        elif approach_note or "interact check" in grounded.lower():
            lines.append(
                "At the target. Short approach only; do not long-walk. "
                "Painted box is a hint, not a button."
            )
        else:
            lines.append("Painted box is a hint, not a required walk and not a button.")
    elif bright_patches:
        lines.append(
            "Bright patches (x,y from top-left; optional walk hint, not a required dir): "
            + bright_patches
        )
    if recent_chords:
        lines.append("Recent device names: " + ", ".join(recent_chords))
    arrival = bool(
        grounded
        and "interact check" in grounded.lower()
        and not lock_note
        and not approach_note
        and not close_prompt_note
    )
    used_prompt = (last_had_prompt or prompt_box is True) and not approach_note and not close_prompt_note
    if last_mad is not None:
        lines.append(f"Last chord motion={last_mad:.3f} (0=still).")
        if approach_note:
            lines.append("Short step toward the object; do not tap until the inset shows copy.")
        elif close_prompt_note:
            lines.append("Do not walk into the object. Read the PROMPT banner.")
        elif used_prompt:
            lines.append(
                "A prompt was just used. Do not mash the same face. Look with stick_r or walk a new heading."
            )
        elif last_mad < 0.02 and arrival:
            lines.append("At the target with small motion. Short step or turn; do not long-walk.")
        elif last_mad < 0.02:
            lines.append(
                "Almost still / blocked. Do not repeat the same stick_l. "
                "Turn with stick_r (e/w) or back with stick_l_s, then walk a different dir."
            )
    elif last_pixels_changed is False:
        if approach_note:
            lines.append("Short step toward the object; do not tap until the inset shows copy.")
        elif close_prompt_note:
            lines.append("Do not walk into the object. Read the PROMPT banner.")
        elif used_prompt:
            lines.append(
                "A prompt was just used. Do not mash the same face. Look with stick_r or walk a new heading."
            )
        elif arrival:
            lines.append("At the target with small motion. Short step or turn; do not long-walk.")
        else:
            lines.append(
                "Almost still / blocked. Do not repeat the same stick_l. "
                "Turn with stick_r (e/w) or back with stick_l_s, then walk a different dir."
            )
    if arm == "dump_all" or allow_memory:
        lines.append("Tools: recall, remember. Use recall if the screen looks familiar.")
    elif arm == "ratchet":
        lines.append(
            'Tools: recall. Emit {"tool": "recall", "query": "kind heading glyph"} plus a chord.'
        )
    else:
        lines.append("Memory tools are off this step.")
    retrieved = list(recall_hits or []) + list(memory_snippets or [])
    if retrieved:
        lines.append("Retrieved:")
        lines.extend(f"- {s}" for s in retrieved[:8])
    if journal_block:
        lines.append(journal_block)
    if skill is not None:
        lines.append("Active skill (0 or 1):")
        lines.append(f"id: {skill.id}")
        lines.append(f"recognize: {skill.recognize}")
        lines.append(f"avoid_or_do: {skill.avoid_or_do}")
        lines.append(f"end: {skill.end}")
    elif arm == "ratchet":
        lines.append("Active skill: none this episode. Still play.")
    if working_block:
        lines.append(working_block)
    return "\n".join(lines)


def _hud_see_line(screen: str, hud: dict[str, Any] | None) -> str:
    bits = [f"screen={screen or 'other'}"]
    keys: tuple[str, ...]
    if screen in {"combat", "tutorial"}:
        keys = ("energy", "energy_max", "hp", "hp_max", "block")
    elif screen in {"map", "shop", "rest", "event", "reward"}:
        keys = ("gold", "floor")
    else:
        keys = ()
    for key in keys:
        value = (hud or {}).get(key)
        if value is not None and value != "":
            bits.append(f"{key}={value}")
    return "See: " + " ".join(bits)


def clickable_labels_from_list(labels: Sequence[str] | None) -> list[str]:
    """Sorted unique allowed controls for LLM2 (labels only, no mark ids)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in labels or []:
        lab = str(raw or "").strip()
        key = lab.lower()
        if not lab or key in _SKIP_CLICKABLE_LABELS or key in seen:
            continue
        seen.add(key)
        out.append(lab)
    return sorted(out, key=str.lower)


def _compact_control_label(text: str) -> str:
    return " ".join(
        "".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split()
    )


def _label_in_allowed(label: str, allowed: set[str]) -> bool:
    compact = _compact_control_label(label)
    return compact in {_compact_control_label(row) for row in allowed if str(row or "").strip()}


def choice_in_allowed_controls(
    choice: str,
    allowed: Sequence[str] | None,
    *,
    target: str = "",
    allowed_targets: Sequence[str] | None = None,
) -> bool:
    """True when choice (and optional target) match Allowed controls/targets verbatim."""
    if not (choice or "").strip():
        return True
    allowed_compact = {_compact_control_label(row) for row in (allowed or []) if str(row or "").strip()}
    if _compact_control_label(choice) not in allowed_compact:
        return False
    tgt = (target or "").strip()
    if not tgt:
        return True
    target_pool = allowed_targets if allowed_targets is not None else allowed
    return _compact_control_label(tgt) in {
        _compact_control_label(row) for row in (target_pool or []) if str(row or "").strip()
    }


def _admit_control_row(row: dict[str, Any], *, require_geometry: bool = False) -> bool:
    role = str(row.get("role") or "").strip().lower()
    if role not in GENERIC_CONTROL_ROLES:
        return False
    label = str(row.get("label") or "").strip()
    if not label:
        return False
    if require_geometry and row_bounds(row) is None:
        return False
    return True


def admit_control_rows(
    rows: Sequence[dict[str, Any]] | None,
    *,
    require_geometry: bool = False,
) -> list[dict[str, Any]]:
    """Admit only current-frame typed controls with generic UI roles."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or ():
        if not isinstance(row, dict) or not _admit_control_row(row, require_geometry=require_geometry):
            continue
        key = control_key_from_row(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return sorted(out, key=lambda item: str(item.get("label") or "").lower())


def labels_from_admitted_controls(rows: Sequence[dict[str, Any]] | None) -> list[str]:
    return clickable_labels_from_list([str(row.get("label") or "") for row in (rows or ())])


def allowed_controls_for_turn(
    *,
    inventory: Sequence[dict[str, Any]] | None = None,
    detector_rows: Sequence[dict[str, Any]] | None = None,
    tutorial_info: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Admit typed current-frame controls; paginated tutorial may prefer detector rows."""
    if paginated_tutorial_active(tutorial_info, host_stats=host_stats):
        admitted = admit_control_rows(detector_rows, require_geometry=True)
        if admitted:
            return admitted, "detector"
    return admit_control_rows(inventory), "inventory"


def format_host_hud_block(
    *,
    hud: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
) -> str:
    """Authoritative pre-action stats for the reasoner (replaces redundant hud_read)."""
    bits: list[str] = []
    stats = host_stats or {}
    flat = hud or {}
    screen = str(stats.get("screen") or flat.get("screen") or "other")
    bits.append(f"screen={screen}")
    turn = stats.get("turn")
    if turn and str(turn) not in {"", "unknown", "null"}:
        bits.append(f"turn={turn}")
    player = stats.get("player") if isinstance(stats.get("player"), dict) else {}
    for key in ("hp", "hp_max", "energy", "energy_max", "block"):
        val = player.get(key) if player else flat.get(key)
        if val is not None and val != "":
            bits.append(f"{key}={val}")
    for key in ("gold", "floor"):
        val = flat.get(key)
        if val is None and isinstance(stats.get("meta"), dict):
            val = stats["meta"].get(key)
        if val is not None and val != "":
            bits.append(f"{key}={val}")
    tutorial = stats.get("tutorial")
    if isinstance(tutorial, dict) and tutorial.get("active"):
        title = str(tutorial.get("title") or "Tutorial").strip()
        step, total = tutorial.get("step"), tutorial.get("total")
        if isinstance(step, int) and isinstance(total, int):
            bits.append(f"tutorial={title} ({step} of {total})")
        else:
            bits.append(f"tutorial={title}")
    enemies = stats.get("enemies")
    if isinstance(enemies, list) and enemies:
        parts = []
        for row in enemies[:4]:
            if not isinstance(row, dict):
                continue
            hp, hp_max = row.get("hp"), row.get("hp_max")
            label = row.get("label")
            if hp is not None and hp_max is not None:
                chunk = f"{hp}/{hp_max}"
                if label:
                    chunk = f"{label} {chunk}"
                parts.append(chunk)
        if parts:
            bits.append("enemies=" + ", ".join(parts))
    markers = stats.get("markers")
    if isinstance(markers, list) and markers:
        bits.append("markers=" + ", ".join(str(m) for m in markers[:8]))
    if not bits:
        return ""
    return "Host HUD: " + " ".join(bits)


def see_pack(
    *,
    screen: str | None = None,
    hud: dict[str, Any] | None = None,
    selected: str | None = None,
    aim_listing: str | None = None,
) -> list[str]:
    """2–3 host lines for this SCREEN_KIND. Overlay inventory is a later increment."""
    resolved = overlay_screen(screen or str((hud or {}).get("screen") or "") or None, aim_listing)
    lines = [_hud_see_line(resolved, hud)]
    if resolved == "combat":
        if selected:
            lines.append(f"SELECTED: {selected}")
    elif resolved == "tutorial" or listing_has_dialog(aim_listing):
        printed: list[str] = []
        blob = (aim_listing or "").lower()
        for pretty, token in (
            ("Yes", ":yes"),
            ("No", ":no"),
            ("Got it", ":got_it"),
            ("Close", ":close"),
            ("Back", ":back"),
        ):
            if token in blob:
                printed.append(f":{pretty}")
        if printed:
            lines.append("Tutorial: " + ", ".join(printed) + " printed.")
        else:
            lines.append("Tutorial: printed dialog labels in Marks.")
    elif resolved == "pause":
        lines.append("Pause: click :Resume. Never Give Up or Save and Quit.")
    elif resolved == "map":
        lines.append("Map: click an opaque or pulsing node, not the Legend panel.")
    elif resolved == "select" or (aim_listing and ":locked" in aim_listing.lower()):
        lines.append("Select: click Confirm. Locked portraits are not cards.")
    elif resolved == "title":
        lines.append("Title: click a printed menu word (Singleplayer, Continue).")
    elif resolved in {"shop", "rest", "reward"}:
        lines.append(f"{resolved.title()}: click a printed Marks: label.")
    elif resolved == "death":
        lines.append("Run ended.")
    if listing_backdrop_close(aim_listing):
        lines.append("Magnified card — :Close is the darkened backdrop (leave).")
    elif aim_listing and ":locked" in aim_listing.lower() and resolved != "select":
        lines.append("Locked portrait — not playable. Click Confirm, not Play/unlock.")
    return lines[:4]


def turn_user_prompt(
    *,
    actions: str,
    recent_chords: list[str] | None = None,
    last_pixels_changed: bool | None = None,
    last_mad: float | None = None,
    burst_caption: str | None = None,
    working_block: str | None = None,
    journal_block: str | None = None,
    notebook_block: str | None = None,
    skill: Skill | None = None,
    arm: str = "no_skill",
    memory_snippets: list[str] | None = None,
    recall_hits: list[str] | None = None,
    allow_memory: bool = False,
    aim_listing: str | None = None,
    last_click: str | None = None,
    selected: str | None = None,
    screen: str | None = None,
    hud: dict[str, Any] | None = None,
) -> str:
    del recent_chords, working_block
    lines = [
        'Click a numbered badge with {"mark": N} from Marks: — never copy schema example ids.',
        "JSON goal is this-step move. Notebook Goal: is the run.",
    ]
    if aim_listing:
        lines.insert(
            0,
            "Control scheme is Marks: only. Keys and gx,gy are off-scheme while Marks: is listed.",
        )
    else:
        lines.insert(0, "Keys (only if needed): " + actions)
    lines.extend(
        see_pack(screen=screen, hud=hud, selected=selected, aim_listing=aim_listing)
    )
    if aim_listing:
        lines.append("Marks: " + aim_listing)
        if listing_has_named(aim_listing):
            lines.append(
                "choice must copy a printed Marks: label — not a paraphrase. "
                'note: "mk_N is Label" with that same word. '
                "Empty JSON is invalid; emit thought, goal, note, and a mark."
            )
        if listing_has_dialog(aim_listing):
            lines.append(
                "This popup is not Got it unless that label is printed. "
                "Do not emit action enter when :Yes/:No are printed."
            )
        if ":icon" in aim_listing.lower():
            lines.append(
                "Unlabeled Marks (:icon) — skip them if a printed play word is listed. "
                "hover once "
                '{"hover": true, "mark": N} only if you need the name, then click. '
                "Printed words click immediately."
            )
    if last_click:
        lines.append("Last click (grid): " + last_click + ". Do not repeat it if the screen did not change.")
    if burst_caption:
        lines.append("Last name CV (text only): " + burst_caption)
    if last_mad is not None:
        lines.append(f"Last motion={last_mad:.3f} (0=still).")
        if last_mad < 0.02:
            if selected:
                lines.append(
                    f"Almost still with {selected} selected. Click a creature, not End Turn."
                )
            elif listing_has_dialog(aim_listing):
                lines.append("Almost still on a dialog. Click a printed :Yes, :No, or :Got it.")
            elif listing_backdrop_close(aim_listing):
                lines.append("Almost still. Click :Close on the darkened backdrop.")
            elif aim_listing:
                lines.append("Almost still. Click a different listed mark.")
            else:
                lines.append("Almost still. Click a different mark or gx,gy.")
    elif last_pixels_changed is False:
        if selected:
            lines.append(
                f"Last click did not change pixels. {selected} is selected — click a creature."
            )
        elif listing_has_dialog(aim_listing):
            lines.append("Last click did not change pixels. Click a printed :Yes, :No, or :Got it.")
        elif listing_backdrop_close(aim_listing):
            lines.append("Last click did not change pixels. Click :Close on the darkened backdrop.")
        elif aim_listing:
            lines.append("Last click did not change pixels. Click a different listed mark.")
        else:
            lines.append("Last click did not change pixels. Click a different mark or gx,gy.")
    if arm == "dump_all" or allow_memory:
        if aim_listing:
            lines.append("Tools: recall, remember. Still emit a listed mark with goal and note.")
        else:
            lines.append("Tools: recall, remember. Still emit mark, hover, or gx,gy with goal and note.")
    elif arm == "ratchet":
        lines.append('Tools: recall. Emit {"tool": "recall", "query": "...", "mark": 4}.')
    retrieved = list(recall_hits or []) + list(memory_snippets or [])
    if retrieved:
        lines.append("Retrieved:")
        lines.extend(f"- {s}" for s in retrieved[:8])
    if notebook_block:
        lines.append(notebook_block)
    elif journal_block:
        lines.append(journal_block)
    if skill is not None:
        lines.append("Active skill (0 or 1):")
        lines.append(f"id: {skill.id}")
        lines.append(f"recognize: {skill.recognize}")
        lines.append(f"avoid_or_do: {skill.avoid_or_do}")
        lines.append(f"end: {skill.end}")
    elif arm == "ratchet":
        lines.append("Active skill: none this episode. Still play.")
    return "\n".join(lines)


def _ocr_blob_looks_like_stat(text: str) -> bool:
    blob = (text or "").strip().lower()
    if not blob:
        return True
    if _OCR_STAT_RE.search(blob):
        return True
    return blob in {"hp", "energy", "/"}


def _host_anchor_for_prompt(
    *,
    hud: dict[str, Any] | None,
    host_stats: dict[str, Any] | None,
) -> dict[str, Any]:
    stats = host_stats or {}
    anchor: dict[str, Any] = {}
    screen = str(stats.get("screen") or (hud or {}).get("screen") or "").strip()
    if screen:
        anchor["screen"] = screen
    turn = stats.get("turn")
    if turn is not None and str(turn) not in {"", "null"}:
        anchor["turn"] = turn
    tutorial = stats.get("tutorial")
    if isinstance(tutorial, dict) and tutorial:
        anchor["tutorial"] = tutorial
    return anchor


def _host_stats_for_prompt(
    *,
    hud: dict[str, Any] | None,
    host_stats: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    stats = host_stats or {}
    player = stats.get("player")
    if isinstance(player, dict):
        hp, hp_max = player.get("hp"), player.get("hp_max")
        if hp is not None and hp_max is not None:
            out["hp"] = f"{hp}/{hp_max}"
        energy, energy_max = player.get("energy"), player.get("energy_max")
        if energy is not None and energy_max is not None:
            out["energy"] = f"{energy}/{energy_max}"
    if hud:
        for key in ("hp", "energy", "block", "gold", "floor"):
            val = hud.get(key)
            if val is not None and val != "" and key not in out:
                out[key] = val
    meta = stats.get("meta")
    if isinstance(meta, dict):
        for key in ("gold", "floor"):
            if key in meta and key not in out:
                out[key] = meta[key]
    return out


def _ocr_candidates_from_hud(
    *,
    hud: dict[str, Any] | None,
    host_stats: dict[str, Any] | None,
    ocr_suppress: Any | None = None,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    stats = host_stats or {}
    screen = str(stats.get("screen") or (hud or {}).get("screen") or "").strip().lower()

    def add(raw: str) -> None:
        text = " ".join(str(raw or "").split())
        if not text or _ocr_blob_looks_like_stat(text):
            return
        if callable(ocr_suppress) and ocr_suppress(text):
            return
        key = text.lower()
        if screen == "map":
            compact = " ".join(key.split())
            if compact in _MAP_OCR_SKIP:
                return
            if any(tok in _MAP_OCR_SKIP for tok in compact.split()):
                return
            if re.search(r"\bv0\.\d", key):
                return
            if re.search(r"\bcs[0-9a-z]{6,}\b", key):
                return
        if key in seen:
            return
        seen.add(key)
        candidates.append(text)

    blob = str((hud or {}).get("ocr") or "").strip()
    for line in blob.splitlines():
        add(line.strip())
    low = blob.lower()
    for phrase in sorted(_OCR_MARKER_PHRASES, key=len, reverse=True):
        if phrase in low:
            if phrase == "got it":
                add("Got it")
            elif phrase == "end turn":
                add("End Turn")
            elif phrase == "singleplayer":
                add("Singleplayer")
            elif phrase == "multiplayer":
                add("Multiplayer")
            elif phrase == "combat basics":
                add("Combat Basics")
            elif phrase == "map basics":
                add("Map Basics")
            else:
                add(phrase[:1].upper() + phrase[1:])
    for match in _OCR_TITLE_PHRASE_RE.finditer(blob):
        add(match.group(0))
    for marker in (host_stats or {}).get("markers") or []:
        add(str(marker))
    return candidates


def _role_guess_for_detector_label(label: str) -> str:
    low = " ".join(str(label or "").lower().split())
    if not low:
        return "other"
    if low in {"yes", "no"}:
        return "dialog"
    if low in {
        "got it",
        "confirm",
        "back",
        "close",
        "continue",
        "resume",
        "end turn",
        "next",
        "arrow",
        "singleplayer",
        "multiplayer",
        "settings",
        "quit",
    }:
        return "button"
    if low.endswith("+") or low in {"strike", "defend", "bash"}:
        return "card"
    if low.startswith("node_"):
        return "button"
    return "other"


def _detector_candidates_from_labels(
    labels: Sequence[str] | Sequence[dict[str, str]] | None,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in labels or []:
        if isinstance(raw, dict):
            label = str(raw.get("label") or "").strip()
            role_guess = str(raw.get("role_guess") or raw.get("role") or "other").strip() or "other"
        else:
            label = str(raw or "").strip()
            role_guess = _role_guess_for_detector_label(label)
        key = label.lower()
        if not label or key in _SKIP_CLICKABLE_LABELS or key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "role_guess": role_guess})
    return out


def detector_candidates_from_marks(
    marks: Sequence[Any] | None,
) -> list[dict[str, str]]:
    """Build VLM1 detector candidates from aim marks (includes numbered map nodes)."""
    return [
        {"label": row["label"], "role_guess": row.get("role") or row.get("role_guess") or "other"}
        for row in detector_typed_rows_from_marks(marks)
    ]


def _detector_role_for_label(label: str, explicit_role: str = "") -> str:
    role = str(explicit_role or "").strip().lower()
    if role in GENERIC_CONTROL_ROLES:
        return role
    guess = _role_guess_for_detector_label(label)
    compact = _compact_control_label(label)
    if guess == "dialog":
        return "dialog"
    if guess == "menu":
        return "menu"
    if guess == "button":
        if compact == "next":
            return "nav_next"
        if compact in {"prev", "previous", "arrow"}:
            return "nav_prev"
        return "button"
    return "other"


def detector_typed_rows_from_marks(
    marks: Sequence[Any] | None,
) -> list[dict[str, Any]]:
    """Typed detector rows with geometry for tutorial admission."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mark in marks or ():
        label = str(getattr(mark, "label", "") or "").strip()
        key = label.lower()
        if not label or key in _SKIP_CLICKABLE_LABELS or key in seen:
            continue
        seen.add(key)
        role = _detector_role_for_label(label, str(getattr(mark, "role", "") or ""))
        row: dict[str, Any] = {
            "label": label,
            "role": role,
            "confidence": "high",
            "x": getattr(mark, "x", None),
            "y": getattr(mark, "y", None),
            "x0": getattr(mark, "x0", None),
            "y0": getattr(mark, "y0", None),
            "x1": getattr(mark, "x1", None),
            "y1": getattr(mark, "y1", None),
        }
        out.append(row)
    return out


def scene_perceive_user(
    *,
    hud: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
    clickable_labels: Sequence[str] | None = None,
    detector_labels: Sequence[str] | Sequence[dict[str, str]] | None = None,
    focus: str = "",
    host_anchor: dict[str, Any] | None = None,
    ocr_candidates: Sequence[str] | None = None,
    detector_candidates: Sequence[dict[str, str]] | None = None,
    affordance_hints: dict[str, Any] | None = None,
    ocr_suppress: Any | None = None,
) -> str:
    anchor = host_anchor if host_anchor is not None else _host_anchor_for_prompt(
        hud=hud,
        host_stats=host_stats,
    )
    stats = _host_stats_for_prompt(hud=hud, host_stats=host_stats)
    ocr = list(ocr_candidates) if ocr_candidates is not None else _ocr_candidates_from_hud(
        hud=hud,
        host_stats=host_stats,
        ocr_suppress=ocr_suppress,
    )
    if detector_candidates is not None:
        detectors = [dict(row) for row in detector_candidates]
    else:
        raw_labels = detector_labels if detector_labels is not None else clickable_labels
        detectors = _detector_candidates_from_labels(raw_labels)

    lines = [
        "Verify and structure this exact frame. JSON only, per schema.",
        f"Host anchor (authoritative screen/turn state -- do not override): {anchor}",
        f"Host OCR stats (authoritative numbers -- do not restate as controls/text): {stats}",
        "OCR text candidates (confirm each is visible as written, correct if misread, drop if not present):",
    ]
    if ocr:
        lines.extend(f'  - "{text}"' for text in ocr)
    else:
        lines.append("  (none)")
    lines.append(
        "Detector element candidates (confirm role, correct label if wrong, "
        "drop false positives, add anything missed):"
    )
    if detectors:
        lines.extend(
            f"  - {row['label']} (role guess: {row['role_guess']})" for row in detectors
        )
    else:
        lines.append("  (none)")
    if affordance_hints:
        lines.append(
            "CV affordance hints (frame-diff; prefer these when filling legal_actions verdicts):"
        )
        for mark_id, sig in affordance_hints.items():
            if hasattr(sig, "as_dict"):
                payload = sig.as_dict()
            elif isinstance(sig, dict):
                payload = sig
            else:
                continue
            lines.append(
                f"  - {mark_id}: delta={payload.get('delta')} "
                f"conf={payload.get('confidence')} verdict={payload.get('verdict')}"
            )
    focus_text = (focus or "").strip()
    if focus_text:
        lines.append(
            "Targeted rescan requested by the reasoning agent -- pay special attention to: "
            + focus_text
        )
    return "\n".join(lines)


def strip_mark_leaks(text: str) -> str:
    """Remove mk_N / id@grid leaks from notebook lines shown to the reasoner."""
    if not text:
        return ""
    import re

    out: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"\bmk_\d+\b", "", line, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b\d+@\d+,\d+(?::\S+)?\b", "", cleaned)
        cleaned = " ".join(cleaned.split())
        if cleaned:
            out.append(cleaned)
    return "\n".join(out)


PERCEIVE_PROMPTS: dict[str, str] = {
    "v1": SCENE_PERCEIVE_V1,
    "v2": scene_perceive_system(),
}


def strategy_see_pack(
    *,
    screen: str | None = None,
    hud: dict[str, Any] | None = None,
    selected: str | None = None,
    scene_controls: list[str] | None = None,
    scene_dialog: list[str] | None = None,
    tutorial_info: dict[str, Any] | None = None,
) -> list[str]:
    """Host context for the reasoner — no mark ids."""
    resolved = (screen or str((hud or {}).get("screen") or "") or "other").strip().lower() or "other"
    lines = [_hud_see_line(resolved, hud)]
    tut = tutorial_info or {}
    tut_active = bool(tut.get("active"))
    tut_step, tut_total = tut.get("step"), tut.get("total")
    if tut_active and isinstance(tut_step, int) and isinstance(tut_total, int) and tut_step < tut_total:
        title = str(tut.get("title") or "Tutorial").strip()
        lines.append(
            f"Tutorial in progress: {title} ({tut_step} of {tut_total}). "
            "Follow on-screen instructions. Do not Back or Singleplayer."
        )
    elif resolved == "combat":
        if selected:
            lines.append(f"SELECTED: {selected} — pick a target enemy label for attacks.")
        else:
            lines.append("Combat: play a hand card (choice) then target an enemy (target).")
    elif resolved == "tutorial":
        opts = [o for o in (scene_dialog or scene_controls or []) if o]
        if opts:
            lines.append("Tutorial controls (pick one from Allowed controls): " + ", ".join(opts[:6]))
        else:
            lines.append("Tutorial: pick exactly one label from Allowed controls to advance.")
        if not tut_active:
            blob = " ".join(scene_controls or []).lower()
            if "(1 of" in blob or "(2 of" in blob or "(3 of" in blob or "combat basics" in blob:
                lines.append(
                    "Tutorial in progress — follow on-screen instructions. Do not Back or Singleplayer."
                )
    elif resolved == "pause":
        lines.append("Pause: Resume only. Never Give Up.")
    elif resolved == "map":
        lines.append("Map: pick the next node name you want to visit.")
    elif resolved == "select":
        lines.append("Character select: click Confirm after picking a character.")
    elif resolved == "title":
        lines.append("Title: Singleplayer or Continue to progress.")
    return lines[:5]


def reason_state_from_host(
    *,
    screen: str | None = None,
    turn: str | None = None,
    tutorial_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Authoritative screen/turn/tutorial state for the reasoner."""
    state: dict[str, Any] = {
        "screen": str(screen or "other").strip() or "other",
        "turn": str(turn or "unknown").strip() or "unknown",
    }
    tut = tutorial_info or {}
    if tut.get("active"):
        title = str(tut.get("title") or "").strip()
        if title:
            state["tutorial_title"] = title
        step, total = tut.get("step"), tut.get("total")
        if isinstance(step, int) and isinstance(total, int):
            state["tutorial_step"] = step
            state["tutorial_total"] = total
    return state


def reason_hud_stats(
    *,
    hud: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Compact Host HUD numbers for the reasoner Stats line."""
    stats: dict[str, str] = {}
    flat = hud or {}
    player = host_stats.get("player") if isinstance((host_stats or {}).get("player"), dict) else {}
    hp = (player or {}).get("hp") if player else flat.get("hp")
    hp_max = (player or {}).get("hp_max") if player else flat.get("hp_max")
    if hp is not None and hp != "":
        stats["hp"] = f"{hp}/{hp_max}" if hp_max not in (None, "") else str(hp)
    energy = (player or {}).get("energy") if player else flat.get("energy")
    energy_max = (player or {}).get("energy_max") if player else flat.get("energy_max")
    if energy is not None and energy != "":
        stats["energy"] = (
            f"{energy}/{energy_max}" if energy_max not in (None, "") else str(energy)
        )
    block = (player or {}).get("block") if player else flat.get("block")
    if block is not None and block != "":
        stats["block"] = str(block)
    return stats


def _is_inspectable_row(row: dict[str, Any]) -> bool:
    from loop.agent.entities import (
        is_player_row,
        is_unanchored_character_row,
        is_unknown_entity_label,
        _row_center,
    )

    if is_player_row(row) or is_unanchored_character_row(row):
        return False
    role = str(row.get("role") or "").strip().lower()
    side = str(row.get("side") or "").strip().lower()
    if role in {"unknown_character", "character"} and _row_center(row) is None and side not in {
        "enemy", "hostile", "foe",
    }:
        return False
    label = str(row.get("label") or "")
    if str(row.get("inspectable") or "").strip().lower() in {"true", "1", "yes"}:
        return True
    if role in {"unknown_character", "character"} and str(row.get("confidence") or "").lower() == "low":
        return True
    return is_unknown_entity_label(label)


def reason_controls_for_turn(
    *,
    controls: Sequence[dict[str, Any]] | None = None,
    uncertain: Sequence[str] | None = None,
    enemies: Sequence[str] | None = None,
    detector_rows: Sequence[dict[str, Any]] | None = None,
    tutorial_info: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """LLM1 controls with role/confidence; paginated tutorial may swap in detector rows."""
    uncertain_compact = {_compact_control_label(str(u)) for u in (uncertain or []) if str(u or "").strip()}
    out: list[dict[str, Any]] = []

    if paginated_tutorial_active(tutorial_info, host_stats=host_stats):
        admitted, _source = allowed_controls_for_turn(
            detector_rows=detector_rows,
            tutorial_info=tutorial_info,
            host_stats=host_stats,
        )
        if admitted:
            return [dict(row, confidence=str(row.get("confidence") or "high")) for row in admitted]

    seen: set[str] = set()
    for row in controls or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        from loop.agent.entities import (
            _row_center,
            is_frame_mark_token,
            is_player_row,
            is_unanchored_character_row,
            player_display_label,
        )

        if is_frame_mark_token(label) or is_unanchored_character_row(row):
            continue
        if is_player_row(row):
            if _row_center(row) is None:
                continue
            row = dict(row)
            row["label"] = player_display_label(label)
            row["role"] = "player"
            row["side"] = row.get("side") or "player"
            label = str(row["label"])
        role_early = str(row.get("role") or "").strip().lower()
        side_early = str(row.get("side") or "").strip().lower()
        if (
            role_early in {"unknown_character", "character"}
            and side_early not in {"enemy", "hostile", "foe"}
        ):
            from loop.agent.entities import _row_center

            if _row_center(row) is None:
                continue
        compact = _compact_control_label(label)
        instance_key = str(row.get("entity_id") or row.get("id") or compact).strip()
        if is_frame_mark_token(instance_key):
            instance_key = compact
        if instance_key in seen:
            continue
        seen.add(instance_key)
        role = str(row.get("role") or "other").strip()
        confidence = str(row.get("confidence") or "").strip().lower()
        if confidence not in {"high", "low"}:
            confidence = "low" if compact in uncertain_compact else "high"
        entry: dict[str, Any] = {"label": label, "role": role, "confidence": confidence}
        for key in ("bbox", "x", "y", "x0", "y0", "x1", "y1", "side"):
            if row.get(key) is not None:
                entry[key] = row.get(key)
        if row.get("entity_id"):
            from loop.agent.entities import is_frame_mark_token as _mark_tok

            eid = str(row.get("entity_id") or "")
            if not _mark_tok(eid):
                entry["entity_id"] = eid
        elif row.get("id"):
            from loop.agent.entities import is_frame_mark_token as _mark_tok

            eid = str(row.get("id") or "")
            if not _mark_tok(eid):
                entry["entity_id"] = eid
        if row.get("inspectable") is not None:
            entry["inspectable"] = row.get("inspectable")
        out.append(entry)

    enemy_in_controls = {
        _compact_control_label(c["label"]) for c in out if c.get("role") == "enemy"
    }
    for raw in enemies or []:
        label = str(raw or "").strip()
        if not label:
            continue
        from loop.agent.entities import is_player_row as _is_player_name

        if _is_player_name({"label": label, "role": "character"}):
            continue
        compact = _compact_control_label(label)
        if compact in enemy_in_controls:
            continue
        seen.add(compact)
        enemy_in_controls.add(compact)
        confidence = "low" if compact in uncertain_compact else "high"
        out.append({"label": label, "role": "enemy", "confidence": confidence})

    existing = {_compact_control_label(c["label"]) for c in out}
    for raw in uncertain or []:
        label = str(raw or "").strip()
        if not label:
            continue
        compact = _compact_control_label(label)
        if compact in existing:
            continue
        existing.add(compact)
        out.append({"label": label, "role": "other", "confidence": "low"})
    return out


def allowed_sets_from_controls(
    controls: Sequence[dict[str, Any]] | None,
) -> tuple[set[str], set[str]]:
    """Confirmed (high-confidence) controls vs targets for validate_choice."""
    from loop.agent.entities import is_frame_mark_token, is_player_row, is_unanchored_character_row

    allowed_controls: set[str] = set()
    allowed_targets: set[str] = set()
    for row in controls or []:
        if str(row.get("confidence") or "").strip().lower() != "high":
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        if is_unanchored_character_row(row):
            continue
        if is_player_row(row):
            if not is_frame_mark_token(label):
                from loop.agent.entities import player_display_label

                allowed_targets.add(player_display_label(label))
            continue
        role = str(row.get("role") or "").strip().lower()
        if role in GENERIC_CONTROL_ROLES:
            allowed_controls.add(label)
        elif role in {"unknown_character", "character", "target", "enemy", "player"}:
            if not is_frame_mark_token(label):
                allowed_targets.add(label)
    return allowed_controls, allowed_targets


def inspectable_targets_from_controls(
    controls: Sequence[dict[str, Any]] | None,
) -> set[str]:
    """Unknown / inspectable sprite labels legal for action=inspect."""
    out: set[str] = set()
    for row in controls or []:
        if not _is_inspectable_row(row):
            continue
        label = str(row.get("label") or row.get("entity_id") or "").strip()
        if label:
            out.add(label)
    return out


_NO_TARGET_CARDS = frozenset({"defend", "block"})


def attack_cards_from_controls(controls: Sequence[dict[str, Any]] | None) -> set[str]:
    """Hand cards that require an enemy target (excludes Defend etc.)."""
    out: set[str] = set()
    for row in controls or []:
        if str(row.get("role") or "").strip() != "card":
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        if _compact_control_label(label) in _NO_TARGET_CARDS:
            continue
        if str(row.get("confidence") or "").strip().lower() == "high":
            out.add(label)
    return out


def instance_ids_from_controls(
    controls: Sequence[dict[str, Any]] | None,
) -> tuple[set[str], set[str], set[str]]:
    """Return (choice_ids, target_ids, duplicate_choice_labels)."""
    choice_ids: set[str] = set()
    target_ids: set[str] = set()
    label_counts: dict[str, int] = {}
    for row in controls or []:
        role = str(row.get("role") or "").strip()
        eid = str(row.get("entity_id") or row.get("id") or "").strip()
        label = str(row.get("label") or "").strip()
        if role == "card":
            if eid:
                choice_ids.add(eid)
            if label:
                key = _compact_control_label(label)
                label_counts[key] = label_counts.get(key, 0) + 1
        elif role in {"enemy", "player"} and eid:
            target_ids.add(eid)
    duplicates = {label for label, n in label_counts.items() if n > 1}
    return choice_ids, target_ids, duplicates


def validate_choice(
    response_json: dict[str, Any],
    allowed_controls: set[str],
    allowed_targets: set[str],
    retry_state: dict[str, Any],
    *,
    selected: str | None = None,
    attack_cards: set[str] | None = None,
    inspectable_targets: set[str] | None = None,
    choice_ids: set[str] | None = None,
    target_ids: set[str] | None = None,
    duplicate_choice_labels: set[str] | None = None,
) -> dict[str, Any]:
    """Harness guardrail — reject illegal choice/target before executing anything."""
    from loop.agent.entities import is_frame_mark_token

    if "tool" in response_json:
        return {"type": "tool_call", **response_json}

    choice = response_json.get("choice")
    if choice in (None, ""):
        choice = None
    else:
        choice = str(choice).strip() or None
    target = response_json.get("target")
    choice_id = str(response_json.get("choice_id") or "").strip()
    target_id = str(response_json.get("target_id") or "").strip()
    mark_token = next(
        (
            token
            for token in (choice or "", str(target or ""), choice_id, target_id)
            if token and is_frame_mark_token(token)
        ),
        "",
    )
    if mark_token:
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated frame-mark token: '{mark_token}'"}
        return {
            "type": "force_rescan",
            "reason": (
                f"'{mark_token}' is a frame-local mark, not a legal choice/target id"
            ),
            "focus": mark_token,
        }
    if choice and not choice_id and choice_ids is not None and choice in choice_ids:
        choice_id = choice
    if target is not None and not target_id and target_ids is not None and str(target).strip() in target_ids:
        target_id = str(target).strip()
        target = None

    action_kind = str(response_json.get("action") or "").strip().lower()
    hover_flag = response_json.get("hover") in (True, "true", 1, "1", "yes")
    inspect_target = ""
    if action_kind in {"inspect", "hover"}:
        inspect_target = str(target or response_json.get("inspect") or "").strip()
    elif hover_flag:
        inspect_target = str(target or "").strip()
    if inspect_target and not choice and inspectable_targets is not None:
        if inspect_target in inspectable_targets:
            retry_state["invalid_choice_count"] = 0
            return {"type": "inspect", "target": inspect_target}
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated invalid inspect target: '{inspect_target}'"}
        return {
            "type": "force_rescan",
            "reason": f"inspect target '{inspect_target}' is not in Inspectable targets",
            "focus": inspect_target,
        }

    if choice is None and not inspect_target and not selected:
        has_other_action = any(
            response_json.get(k) not in (None, "", [], {})
            for k in ("mark", "action", "gx", "gy", "stick_l", "invoke_gsm")
        )
        if not has_other_action:
            retry_state["empty_response_count"] = retry_state.get("empty_response_count", 0) + 1
            if retry_state["empty_response_count"] > 2:
                return {"type": "abort", "reason": "repeated empty response from reasoner"}
            return {
                "type": "retry",
                "reason": "empty response — emit JSON with choice (and target when required)",
            }

    if selected:
        pick = str(target or choice or "").strip()
        if not _label_in_allowed(pick, allowed_targets):
            retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
            if retry_state["invalid_choice_count"] > 3:
                return {"type": "abort", "reason": f"repeated invalid target while selected: '{pick}'"}
            return {
                "type": "force_rescan",
                "reason": f"with {selected!r} selected, pick a target from Allowed targets",
                "focus": pick or selected,
            }
        retry_state["invalid_choice_count"] = 0
        return {"type": "click", "choice": choice, "target": target or pick}

    if not _label_in_allowed(choice or "", allowed_controls):
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated invalid choice: '{choice}'"}
        return {
            "type": "force_rescan",
            "reason": f"'{choice}' is not in this turn's Allowed controls",
            "focus": choice,
        }

    if (
        attack_cards
        and _label_in_allowed(choice or "", attack_cards)
        and allowed_targets
        and target is None
        and not target_id
    ):
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated missing target for '{choice}'"}
        return {
            "type": "force_rescan",
            "reason": f"attack card '{choice}' requires target from Allowed targets",
            "focus": choice,
        }

    if target is not None and not _label_in_allowed(str(target), allowed_targets):
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated invalid target: '{target}'"}
        return {
            "type": "force_rescan",
            "reason": f"target '{target}' is not in this turn's Allowed targets",
            "focus": target,
        }

    if (
        duplicate_choice_labels
        and choice
        and _compact_control_label(choice) in duplicate_choice_labels
        and not choice_id
    ):
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated missing choice_id for '{choice}'"}
        return {
            "type": "force_rescan",
            "reason": f"duplicate card '{choice}' requires choice_id",
            "focus": choice,
        }

    if choice_id and choice_ids is not None and choice_id not in choice_ids:
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated invalid choice_id: '{choice_id}'"}
        return {
            "type": "force_rescan",
            "reason": f"choice_id '{choice_id}' is not a current-frame card instance",
            "focus": choice_id,
        }

    if (
        attack_cards
        and _label_in_allowed(choice or "", attack_cards)
        and target_ids
        and not target_id
    ):
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated missing target_id for '{choice}'"}
        return {
            "type": "force_rescan",
            "reason": f"targeted card '{choice}' requires target_id",
            "focus": choice,
        }

    if target_id and target_ids is not None and target_id not in target_ids:
        retry_state["invalid_choice_count"] = retry_state.get("invalid_choice_count", 0) + 1
        if retry_state["invalid_choice_count"] > 3:
            return {"type": "abort", "reason": f"repeated invalid target_id: '{target_id}'"}
        return {
            "type": "force_rescan",
            "reason": f"target_id '{target_id}' is not a current-frame combat target",
            "focus": target_id,
        }

    retry_state["invalid_choice_count"] = 0
    payload = {"type": "click", "choice": choice, "target": target}
    if choice_id:
        payload["choice_id"] = choice_id
    if target_id:
        payload["target_id"] = target_id
    return payload


def reason_turn_user_prompt(
    *,
    state: dict[str, Any],
    hud_stats: dict[str, Any],
    controls: Sequence[dict[str, Any]],
    visible_text: Sequence[str] | None = None,
    last_outcome: str | None = None,
    tool_results: dict[str, Any] | None = None,
    journal: str | None = None,
) -> str:
    """Consolidated per-turn user prompt: one State line + one Stats line."""
    state_line = f"State: screen={state['screen']} turn={state['turn']}"
    if state.get("tutorial_title"):
        state_line += f" tutorial={state['tutorial_title']}"
        step, total = state.get("tutorial_step"), state.get("tutorial_total")
        if isinstance(step, int) and isinstance(total, int):
            state_line += f" ({step} of {total})"

    def split(role_filter):
        from loop.agent.entities import is_frame_mark_token

        confirmed = [
            c["label"]
            for c in controls
            if c.get("confidence") == "high"
            and role_filter(c)
            and not is_frame_mark_token(str(c.get("label") or ""))
        ]
        unconfirmed = [
            c["label"]
            for c in controls
            if c.get("confidence") == "low"
            and role_filter(c)
            and not is_frame_mark_token(str(c.get("label") or ""))
        ]
        return confirmed, unconfirmed

    confirmed_controls, unconfirmed_controls = split(
        lambda c: c.get("role") not in {"enemy", "player"}
    )
    confirmed_targets, unconfirmed_targets = split(
        lambda c: c.get("role") in {"enemy", "player"}
    )
    inspectable = [
        str(c.get("label") or "").strip()
        for c in controls
        if _is_inspectable_row(c) and str(c.get("label") or "").strip()
    ]

    lines = [
        state_line,
        f"Stats: {hud_stats}",
        "Allowed controls (only legal 'choice' values -- copy exactly): "
        + (", ".join(confirmed_controls) if confirmed_controls else "(none)"),
    ]
    card_instances = [
        f"{c.get('entity_id')}={c.get('label')}"
        for c in controls
        if c.get("role") == "card" and c.get("entity_id") and c.get("confidence") == "high"
    ]
    if card_instances:
        lines.append(
            "Card instances (choice_id required for duplicates; copy exactly): " + ", ".join(card_instances)
        )
    target_instances = [
        f"{c.get('entity_id')}={c.get('label')}"
        for c in controls
        if c.get("role") in {"enemy", "player"} and c.get("entity_id") and c.get("confidence") == "high"
    ]
    if target_instances:
        lines.append(
            "Target instances (target_id required for targeted cards; copy exactly): " + ", ".join(target_instances)
        )
    if unconfirmed_controls:
        lines.append(
            "Unconfirmed labels (not legal this turn -- pick from Allowed controls instead): "
            + ", ".join(unconfirmed_controls)
        )
    if confirmed_targets or unconfirmed_targets:
        lines.append(
            "Allowed targets (only legal 'target' values -- copy exactly): "
            + (", ".join(confirmed_targets) if confirmed_targets else "(none)")
        )
        if unconfirmed_targets:
            lines.append(
                "Unconfirmed targets (not legal this turn -- pick from Allowed targets instead): "
                + ", ".join(unconfirmed_targets)
            )
    if inspectable:
        lines.append(
            "Inspectable targets (hover/inspect to learn name — use action=inspect): "
            + ", ".join(inspectable)
        )
    info = [str(t).strip() for t in (visible_text or []) if str(t or "").strip()]
    if info:
        lines.append("Visible text (context only, not clickable): " + " | ".join(info))
    if last_outcome:
        lines.append(f"Last outcome: {last_outcome}")
    if tool_results:
        lines.append("Tool results this turn:")
        lines.extend(f"  - {k}: {v}" for k, v in tool_results.items())
    if journal:
        lines.append(
            "Journal (strategy notes only -- no label here is legal to click or target): "
            + strip_mark_leaks(journal)
        )
    return "\n".join(lines)


def perceive_user() -> str:
    return "Estimate the HUD fields. Use null when absent."


def synth_user(*, signature: str, metrics: dict, flags: list[str]) -> str:
    return (
        f"Failure signature: {signature}\n"
        f"Terminal/metrics: {metrics}\n"
        f"Audit flags: {flags}\n"
        "Author one mergeable skill. Unlabeled chords only."
    )
