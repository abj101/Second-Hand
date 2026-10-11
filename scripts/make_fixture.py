"""Build deterministic Pokémon Red free-roam evaluator states."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_pyboy import PyBoyEnv
from truth_pokered import read
OUT, ADVANCE = Path(".tmp/e2"), ("A",) * 160

def wait(env, test, label):
    for _ in range(600):
        row = read(env)
        if test(row): return row
        env.tick(20)
    raise RuntimeError(f"timed out waiting for {label}")


def choose_name(env, name):
    for key in ADVANCE:
        row = read(env)
        if row["map"] != 0x26: raise RuntimeError(f"intro left 2F: {row['map']:#x}")
        if "NEW NAME" in row["screen_text"] and name in row["screen_text"]:
            env.press("DOWN"); env.press("A"); return
        if row["text_box"]: env.press(key)
        else: env.tick(20)
    raise RuntimeError(f"preset {name} did not appear")

def finish_intro(env):
    stable = 0
    for key in ADVANCE:
        row = read(env)
        if row["map"] != 0x26: raise RuntimeError(f"intro left 2F: {row['map']:#x}")
        if row["text_box"]:
            stable = 0; env.press(key)
        else:
            env.tick(20); stable = stable + 1 if not read(env)["text_box"] else 0
            if stable == 5: return
    raise RuntimeError("intro did not reach free roam")

def move(env, key):
    for _ in range(3):
        before = read(env); env.press(key)
        for _ in range(100):
            row = read(env)
            if row["map"] != before["map"] or (row["x"], row["y"]) != (before["x"], before["y"]): return row
            env.tick(20)
    raise RuntimeError(f"blocked or stuck moving {key}")
def main():
    OUT.mkdir(parents=True, exist_ok=True); env = PyBoyEnv()
    try:
        env.tick(5000); env.press("START")
        wait(env, lambda row: "NEW GAME" in row["screen_text"], "title menu")
        env.press("A"); wait(env, lambda row: row["map"] == 0x26 and row["text_box"], "Oak intro")
        choose_name(env, "RED"); choose_name(env, "BLUE"); finish_intro(env)
        row = read(env)
        assert (row["map"], row["player_name"], row["rival_name"]) == (0x26, "RED", "BLUE"), row
        for key in ("RIGHT", "RIGHT", "UP", "UP"): row = move(env, key)
        assert (row["map"], row["x"], row["y"]) == (0x26, 5, 4), row
        env.tick(60); env.screenshot().save(OUT / "red_2f.png"); env.save(str(OUT / "red_2f.state"))
        for key in ("UP", "UP", "UP", "RIGHT", "RIGHT"): row = move(env, key)
        row = wait(env, lambda current: current["map"] == 0x25, "1F warp")
        for key in ("LEFT", "LEFT", "LEFT", "DOWN", "DOWN"): row = move(env, key)
        assert (row["map"], row["x"], row["y"]) == (0x25, 4, 3), row; env.tick(60)
        env.screenshot().save(OUT / "red_1f.png"); env.save(str(OUT / "red_1f.state"))
        print(f"red_2f map={0x26:#04x}; red_1f map={row['map']:#04x}")
    finally:
        env.close()


if __name__ == "__main__": main()
