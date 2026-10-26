from agent.model import parse_json
from agent.perceive import normalize


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure: {"a": 2} hope that helps') == {"a": 2}
    assert parse_json("no json here") is None


def test_screen_labels_follow_the_enum():
    from agent.perceive import SCHEMA
    labels = ("world", "dialogue", "menu", "keyboard", "battle", "title", "other")
    assert SCHEMA["properties"]["screen"]["enum"] == list(labels)
    assert [normalize({"screen": label})["screen"] for label in labels] == list(labels)
    assert normalize({"screen": "top-down room view"})["screen"] == "other"


def test_normalize_splits_string_text_into_lines():
    assert normalize({"text": "MOM: Right.\nAll boys leave"})["text"] == ["MOM: Right.", "All boys leave"]


def test_placement_is_optional_and_normalize_keeps_only_valid_values():
    from agent.perceive import PLACEMENTS, SCHEMA

    data = {"entities": [
        {"kind": "door", "name": "rug", "at": [200, 930], "placement": "bottom wall"},
        {"kind": "person", "name": "MOM", "at": [500, 500], "placement": "near player"},
    ]}
    out = normalize(data)

    assert out["entities"] == [
        {"kind": "door", "name": "rug", "at": [.2, .93], "placement": "bottom wall"},
        {"kind": "person", "name": "MOM", "at": [.5, .5]},
    ]
    assert SCHEMA["properties"]["entities"]["items"]["required"] == ["kind", "name", "at"]
    assert "affordances" not in SCHEMA["properties"] and SCHEMA["required"] == ["screen", "text", "entities"]
    assert SCHEMA["properties"]["entities"]["items"]["properties"]["placement"]["enum"] == list(PLACEMENTS)


def test_crop_box_stays_inside_frame():
    from agent.locate import crop_box
    assert crop_box((1280, 720), [0.99, 0.01]) == (960, 0, 1280, 320)
    x0, y0, x1, y1 = crop_box((1280, 720), [0.5, 0.5])
    assert (x1 - x0, y1 - y0) == (320, 320) and x0 == 480


def test_selectable_is_kept_only_when_a_real_true():
    from agent.perceive import SCHEMA

    base = {"kind": "menu_item", "name": "YES", "at": [500, 500]}
    assert normalize({"entities": [{**base, "selectable": True}]})["entities"] == [{**base, "at": [.5, .5], "selectable": True}]
    for value in ("true", 1, None, False):
        assert normalize({"entities": [{**base, "selectable": value}]})["entities"] == [{"kind": "menu_item", "name": "YES", "at": [.5, .5]}]
    assert normalize({"entities": [base]})["entities"] == [{"kind": "menu_item", "name": "YES", "at": [.5, .5]}]
    assert SCHEMA["properties"]["entities"]["items"]["properties"]["selectable"] == {"type": "boolean"}
    assert "selectable" not in SCHEMA["properties"]["entities"]["items"]["required"]
    assert SCHEMA["properties"]["entities"]["items"]["properties"]["highlighted"] == {"type": "boolean"}
    assert "highlighted" not in SCHEMA["properties"]["entities"]["items"]["required"]

def test_normalize_preserves_highlight_only_on_valid_selectable_entities():
    from agent.perceive import normalize
    rows = normalize({"entities": [
        {"kind": "menu_item", "name": "YES", "at": [500, 500], "selectable": True, "highlighted": True},
        {"kind": "menu_item", "name": "NO", "at": [250, 500], "highlighted": True},
        {"kind": "menu_item", "name": "bad", "at": None, "selectable": True, "highlighted": True},
    ]})["entities"]
    assert rows == [{"kind": "menu_item", "name": "YES", "at": [.5, .5], "selectable": True, "highlighted": True},
                    {"kind": "menu_item", "name": "NO", "at": [.25, .5]}]


def test_dialogue_over_furniture_has_no_selectable_flags():
    perception = {"screen": "dialogue", "text": ["MOM: Right."], "entities": [
        {"kind": "furniture", "name": "television", "at": [300, 200]},
        {"kind": "player", "name": "player", "at": [500, 700]},
        {"kind": "dialogue_box", "name": "dialogue_box", "at": [500, 900]},
    ]}
    out = normalize(perception)
    assert out["screen"] == "dialogue" and len(out["entities"]) == 3
    assert all("selectable" not in e for e in out["entities"])
