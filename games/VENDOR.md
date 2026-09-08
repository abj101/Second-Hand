# Vendored fixtures

Both clones are MIT-licensed. Nested `.git` directories were removed so they
live as source in this repo, not as submodules.

| Path | Upstream | License |
|---|---|---|
| `games/2048` | https://github.com/gabrielecirulli/2048 | MIT (`LICENSE.txt`) |
| `games/tetris` | https://github.com/fkucukkara/tetris | MIT (`LICENSE`) |

Avatar patches:

- **2048:** `window.__AVATAR_GAME__`, `window.__AVATAR_ORACLE__` (backend scoring only), `loadAvatarFixture(name)` for canned See-stage boards. Never send the oracle to a VLM.
- **Tetris:** `?pause_and_decide=1` auto-pauses after start and after each lock; `?fall_ms=N` overrides gravity; `window.__AVATAR_ORACLE__` and `window.__AVATAR_DISPATCH__`.

Do not vendor Nintendo IP, BIOS, or ROMs. Platinum dumps stay on the operator's disk.
