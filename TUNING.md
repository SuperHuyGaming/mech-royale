# Tuning log (practice board, deterministic: same bot = same score)

| Commit | Change (cmd/bot/params.json) | Score |
| --- | --- | --- |
| 72ab9ea | baseline (giddy pilot, no overrides) | 6.19 |
| d5dee4b (v9a) | squad ThreatBoost 45 (boost while under threat) | 5.60 (reverted) |
| 769567f (v9b) | salt 1 (re-roll our own RNG) | never built (scorer builds HEAD at build time) |
| 1edecd4 (v10) | Ears: face unseen gunfire, laser charges, footsteps and beams (salt 0) | 5.73 (twice) |
| 610ec0a (v11) | baseline logic (Ears off), salt 2 | not submitted |
| v12 | baseline logic, salt 0, EndGhost 70: once the circle is small, stop fighting the squad and hide for the zone collapse | 6.19 (identical: the bot never survives to a 70 m circle, ~4:25) |
| v13 | EndGhost 140: switch to hiding mode from ~3:15 (circle heading to 140 m) | ? |
