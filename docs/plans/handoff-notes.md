# Handoff notes
Last written: 2026-10-26. R23 code and one LV23 complete; post-LV O1 repair verified offline. Never pushed.

## Code
- Branch feat/lean-agent; r22 base d18d5b5, plan/contracts141cf3e, r22 code HEAD6849410.
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
- In r22 the lab shop label and HP awareness were open; r23 added called-name display and last-seen meters.
- R22 had 80 dropped held segments on before-world steps (LV21=59), despite gap0.
- Three r22 worktrees removed normally; branches and scratch evidence retained.
- Evidence/report: .tmp/r22/report.md, metrics/analysis/extra/walls/supplement/summary JSONs, final-audit.json,
  integrity-final.json, D/diagnosis.md, g/, O2/, M2/ and live.log.

## R23 code and gates
- Plan and contract: 21b9807, ec6355c. T: 08b0889/merge 1632c96. NO: e258e57/merge 83ebcc9.
  S: 1c0bf2d/merge a66ee02. Authorized O1 repair: 3d24e4d/merge ab761ed.
- T weighted route prefers known floor, learns each new wall once on explore only, skips exact failed
  frontier targets, stops tail at obstacles; grid capability absent stays inert.
- S perceives optional labelled meters, remembers latest per name, renders one last-seen line to act/plan;
  no status without meters. N displays game-given called names before picture guesses; matching unchanged.
- O joins original segment log fates. Post-LV O1 repair snapshots held dropped fates on resolution step;
  odometer absent stays inert. No folding/position change, game words, RAM reads or new module.
- Baseline 944 passed/1 skipped; T-T 947/3; T-NO 949/3; T-S 952/3; original G23 954/1.
- H1 frozen player HP frames: 15/15 exact, 0 PP confusions, 16 effective posts within 20 cap.
- T18F/G/H/I PASS 25/33/65/133; O2 byte-identical; O3 boot/phase identical, agreement 1.0.
- Post-O1 focused 3 passed; final merged suite 956/1. Final regression results: .tmp/r23/report.md.

## LV23 and open evidence
- One copy from LV21 with checkpoint 210 byte-identical; steps 211–270 once, 145 live requests.
- Ledger stored 11222 plus H1 16 = effective 11238/11260. Never pushed or reran LV23.
- World steps 19 (LV22=29, LV21=33); Route 1 minimum RAM y24 (LV22=20, LV21=24).
- Explore verdicts: 11 changed/4 progress/3 failed; offered 75 new/0 failed (LV22=73/43).
- Explore bumps: 0 known floor/11 seen unclassified/0 never seen (LV22=0/20/12);
  reconstructed routes used known floor on 119/296 steps, 40.2% (LV22=129/300, 43.0%).
- Status first reached an act prompt at battle step 216, then 15 world steps; picture-only shop in 0/62 act
  prompts (LV22=29/61). A live enemy meter was misread from a move counter despite H1 success.
- Standing: unchosen presses 0, world lock 100%, gap 0, actor/door walls 0, false crossings 0,
  one offset per visit, refuted-exit choices 0, menu reads 0/22 selects. Map 0 world steps 9
  (LV22=16); battle requests 93/38=2.45 per step.
- O1 LV23 trace **failed**: dropped counters 29/28/30 on steps 227/243/262, same-step tags 0.
  User approved offline repair without rerun; old trace remains failure, future live validation needed.
- D4 step 220 +7.5-tile x came from a dropped post-cut segment; reached stored memory: no.
- All six r23 worktrees removed normally; D4 scratch retained byte-identically under main .tmp/r23/D4.
- Report/evidence: .tmp/r23/report.md, g/, LV/post_metrics.json, H1/, D4/diagnosis.md, live.log.
