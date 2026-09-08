# Human scoring — Act experiment

Tag every row with `harness_level` (L1 vs L2) and `attempt`.

- `success`: micro-task completed (one legal swipe that changed the board; one Tetris shift/rotate)
- `stuck`: 1 if the agent repeated the same failing action or the session was cut for no progress

n=20 per cell (task × level × model) when collecting the write-up table.
Win rate / max tile are descriptive, not gates.
