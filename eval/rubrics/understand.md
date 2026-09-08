# Human scoring — Understand experiment

- `screen_kind_correct`: UI vs gameplay
- `goal_sane`: 1 if the stated goal is something a player would pursue on this screen
- `next_move_legal`: 1 if the named move exists on the action table and is legal *on this screenshot*

Pixels-only: the model was not given a manual. Do not penalize missing jargon.
