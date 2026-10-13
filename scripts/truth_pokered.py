"""Evaluator-only Pokémon Red RAM and tile-map reader."""
from __future__ import annotations

_ADDR = {"map": 0xD35E, "y": 0xD361, "x": 0xD362, "party": 0xD163,
         "battle": 0xD057, "player_name": 0xD158, "rival_name": 0xD34A,
         "map_h": 0xD368, "map_w": 0xD369, "screen_text": 0xC3A0, "sprites": 0xC100}
_CHARS = {**dict(zip(range(0x80, 0x9A), "ABCDEFGHIJKLMNOPQRSTUVWXYZ")),
          **dict(zip(range(0xA0, 0xBA), "abcdefghijklmnopqrstuvwxyz")), 0x7F: " "}


def _text(env, start: int, size: int) -> str:
    out = []
    for i in range(size):
        value = env.ram(start + i)
        if value == 0x50:
            break
        out.append(_CHARS.get(value, "?"))
    return "".join(out).rstrip()


def read(env) -> dict:
    ram = env.ram
    row = {key: ram(addr) for key, addr in _ADDR.items() if key not in ("screen_text", "sprites")}
    tilemap = [ram(0xC3A0 + i) for i in range(20 * 18)]
    row["text_box"] = any(tile in range(0x79, 0x7F) for i, tile in enumerate(tilemap)
                          if 12 <= i // 20 < 18)
    row["player_name"] = _text(env, 0xD158, 11)
    row["rival_name"] = _text(env, 0xD34A, 11)
    row["screen_text"] = "\n".join("".join(_CHARS.get(tile, "?") for tile in tilemap[y * 20:(y + 1) * 20]).rstrip()
                                    for y in range(18)).strip()
    sprites = []
    for slot in range(16):
        base = 0xC100 + slot * 16
        picture, image = ram(base), ram(base + 2)
        y, x = ram(base + 4), ram(base + 6)
        if picture not in (0, 0xFF) and image != 0xFF and 0 <= y < 144 and 0 <= x < 160:
            sprites.append({"slot": slot, "picture": picture, "y": y, "x": x})
    row["sprites"] = sprites
    return row
