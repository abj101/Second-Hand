# Human scoring — See experiment

Score pixels-only. Do not use the oracle JSON when judging the *model*.
The oracle is for later automation only.

- `tiles_correct`: how many of 16 cells match (0–16)
- `score_correct`: 1 if the reported score matches the screenshot
- `status_correct`: 1 if start / mid / game-over is right
- `still_readable`: 1 if *you* can still read the board at this capture setting

CSV template is written into each run directory as `human_score_see.csv`.
