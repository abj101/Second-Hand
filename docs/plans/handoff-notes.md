# Handoff notes
Last written: 2026-10-26. R22 complete; G22 passed and single LV22 completed. Never pushed.

## Code
- Branch feat/lean-agent; base d18d5b5; plan/contracts141cf3e; live/code HEAD6849410.
- O2 ed571d3, mergeab73b27: explore refs/failure keys/trace intents use frontier cells.
- Same-name groups carry place-wide history and failure-first notes, including raw/disambiguated names.
- Refuted exits expose existing reasons and failed state. Unrefuted verified wording is preserved.
- Actual recorded crossings take precedence over stale failure state; no exit-refutation rules changed.
- M2 56dd9c7, merge6849410: perception carries optional highlighted for a selectable cursor item.
- Select uses rounded tile-grid row/column ordinals, then confirms; uncertain geometry falls back to menu read.
- Crop/signature helpers and menu-cache handoff removed; observed legacy cursor signatures dropped.
- Learned menu item order/replies remain. No new production module/class/filter, game words or RAM reads.

## Verification
- Baseline934 passed/1 skipped; G22 final944 passed/1 skipped,29000 warnings.
- O2 worktree938/3; M2 worktree942/3 (extra isolated-worktree ROM/data skips).
- F/G/H/I25/33/65/133 PASS; O2 output identical r21; O3 boot/phase JSONs identical r20.
- User approved O2 extra repair, one M2 obsolete cache assertion update, and T18G mock frontier metadata.
- T18G only gained its missing to field; all assertions and production code stayed unchanged in that update.
- D3 highlight14/14 correct,14/14 sequences (14 posts); T-M2 merged replay14/14 with0 menu reads.
- Golden9 files, protected user5 files, historical source3752 files unchanged; reviewed code hashes match.
- Six O2 tests added; M2 adds6 cases and deletes2 B1 signature tests. Existing approved assertions inventoried.

## LV22
- Single new invocation7; runs/m1_r22_odo copied from r21, checkpoint210 byte-identical; steps211-270 complete.
- Cap160, actual150 requests. No rerun, extension or early stop. Ledger10913+14+150=11077; ceiling11103.
- Route1 minimum RAM y20 (LV21=24; LV20=25); smaller is farther north.
- World choices: check1, exit2, explore/probe24, talk2, travel0. True frontier choices23; exit probe1.
- LV21's2 explore/probe choices were exit probes, with0 true frontier choices.
- Already-refuted exit choices0 (LV21=7; retrospective LV20=4).
- Menu reads0/18 selects (LV21=13/14);18 verified outcomes,0 different-item/ambiguous results.
- Battle requests/step2.55 (LV21=2.96; LV20=2.50). Total requests150 vs145/139; activity mix differs.
- New places0; unexplored quests closed5 (LV21=1; LV20=2), through the contract's new-ground progress.
- Unchosen presses0, world lock100%, gap0, actor/door walls0, false crossings0; one offset in each of3 visits.

## Diagnosis and open issues
- Corrected D3 ROM decoder uses16 bytes per4x4 tile block, not4; initial table is preserved/withdrawn.
- Corrected classification matches every recorded LV21/LV22 world position; no false scenery blocker proven.
- RAM4,23 bush and frontier3,20 are solid; copied270 LEFT probe from4,20 stays blocked.
- Real terrain/available frontier access explains the detour; recorded23 vs reconstructed24/25 gap remains.
- Future terrain/frontier improvements must use runtime evidence, never production RAM/ROM classification.
- Lab shop label and HP awareness remain open; health is not automatically assessed for continuation.
- Held-segment fate scope unchanged:80 dropped on before-world steps (LV21=59), despite gap0.
- Three r22 worktrees removed normally; branches and scratch evidence retained.
- Evidence/report: .tmp/r22/report.md, metrics/analysis/extra/walls/supplement/summary JSONs, final-audit.json,
  integrity-final.json, D/diagnosis.md, g/, O2/, M2/ and live.log.
