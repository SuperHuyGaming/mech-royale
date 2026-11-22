# Strategy Comparison: Existing Automata vs Huy's Survival Plan (v2)

This document presents a side-by-side analysis of:
- **Strategy A**: The existing `automata` architecture (Team 67 base with Team GMUNVIS `origin/tune-m` parameter tuning).
- **Strategy B**: Huy's Survival Plan (v2) as detailed in `KHANG_MECH_PROMPT.md`.

---

## 1. Feature Comparison Matrix

| # | Survival Requirement (Strategy B) | Existing Automata (Strategy A) Implementation | Status | Missing / Required Enhancements |
|---|---|---|---|---|
| **1** | **Upgrades in Peace & Energy Priority**<br>- Move away from enemies to a quiet spot before picking.<br>- Priority: ENERGY (`capacitor`, `reactor`) > Armor (`armor`, `plating`, `nanites`) > Speed (`servos`, `jets`). | `Brain::pick_upgrade` (`mod.rs:280-287`) picks immediately upon receiving `obs.offer`.<br>Default `p.upgrade_priority` in `params.rs`: `["plating", "armor", "nanites", "gun_damage", "gun_rate", "laser_damage", "laser_charge", ...]` (Energy missing from top). | **Partly Done / Missing** | 1. Delay picking until safe (>100m from enemies or `t >= offer.expires - 0.5s`).<br>2. Reorder `upgrade_priority` in `bot.json` with Energy first, then Armor, then Speed. |
| **2** | **Avoid Conflict**<br>- Do not start fights.<br>- Keep distance from all visible/audible enemies.<br>- Prefer occlusion paths with no enemy line-of-sight. | `Ffa` module (`ffa.rs:135-207`) supports `ffa_avoid` and `ffa_early_quiet_range`. Move scoring in `plan.rs` penalizes `w_expo` and `expo_retreat`. | **Partly Done** | `ffa_avoid_range` was disabled (default 0.0) in base `bot.json`. Need to tune `ffa_avoid_range = 160.0`, `ffa_fair_alive = 2.0`, `ffa_min_hp = 0.85` so pacifist disengagement is always active. |
| **3** | **Buildings as Cover**<br>- Wait and loot next to buildings/corners.<br>- Move cover-to-cover. | `Brain::cover_goal` (`strategy.rs:565-635`) raycasts against world prisms/geometry to find occluded waypoints.<br>`Brain::spot_goal` (`survive.rs:61-195`) scores spots by cover and concealment (`sv_spot`, `sv_w_cover`). | **Already Done** | Handled natively by `cover_goal` and `spot_goal`. Verified with `sv_spot = 1.0`, `sv_hold = 1.0`, `sv_w_cover = 30.0`. |
| **4** | **Passive Regeneration**<br>- Break LOS and stay hidden after taking damage until HP regens (45 HP/s after 5.0s lockout). | `Memory::last_damage_t` tracks hits. `sq_regen` (`params.rs:188-190`) and `retreat_exit_hp` (0.85-0.95) provide hysteresis disengagement. | **Partly Done** | When hit at high HP (e.g. 85%), bot previously stayed in combat unless HP < `retreat_hp`. Need `survive2_on` trigger: disengage on *any* damage until lockout expires and HP restores. |
| **5** | **Run When Hurt**<br>- Disengage instantly if HP drops below ~50% (tune 40-60%).<br>- Boost away toward cover; never chase. | `want_retreat` in `strategy.rs:196`: `hpf < p.retreat_hp && threat_near`. Mode switches to `Mode::Retreat`. `gear_boost` triggers boost when retreating. | **Already Done / Tunable** | Tunable via `retreat_hp` (0.50-0.58) and `flee_dist = 120.0`. Add hard disengage cap `survive2_disengage_hp = 0.50`. |
| **6** | **Early Zone Entry**<br>- Move inward early as border closes.<br>- Stay well inside next circle with cover. | `Brain::zone_info` (`strategy.rs:88-139`) evaluates `zone_lead_s`, `zone_edge`, `zone_slack_s`. `plan.rs` weights `zone_cost_mult`, `zone_inner`, `w_next_zone`. | **Already Done / Tunable** | Tuned via `bot.json`: `zone_lead_s = 22.0`, `w_next_zone = 2.0`, `idle_frac = 0.70`, `zone_inner = 8.0`. |
| **7** | **Endgame Dueling**<br>- When $\le 3$ mechs remain or circle is very small, fight back aggressively.<br>- Boost, strafe, target lowest-HP enemy. | Previous endgame trigger in `strategy.rs:224`: `held && z.next_radius <= 0.5 && z.shrink_end - t < 40.0` (triggers only when circle is 0). Did NOT trigger on `obs.alive <= 3`. | **Missing** | Add explicit `endgame` trigger when `obs.alive <= 3 || z.next_radius <= 25.0`. Re-enable duelist targeting prioritized by lowest HP (`frac(tr.hp, tr.max_hp)`). |

---

## 2. Combined Variants Strategy

To achieve the highest placement points and beat the official 6.23 baseline, we test 3 candidate configurations:
1. **Variant 1 (Tuned Baseline `tune-m`)**: Laptop A's tuned parameters with `pref_mortar = 0.0` ban, `sv_hold = 1.0`, `retreat_hp = 0.58`, `flee_dist = 120.0`.
2. **Variant 2 (Survival Plan `survive2_on`)**: Adds Energy-first upgrade priority, `ffa_avoid_range = 160.0`, `ffa_min_hp = 0.85`, hide-until-regenerated hysteresis (`retreat_exit_hp = 0.92`), and quiet upgrade holding.
3. **Variant 3 (Combined Optimal + Endgame Trigger)**: Combines Variant 2 with the explicit $\le 3$ alive endgame trigger, `retreat_hp = 0.50`, and duelist lowest-HP targeting.
