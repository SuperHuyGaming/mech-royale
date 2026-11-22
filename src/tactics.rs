use crate::combat::{best_los_point, compute_dodge, decide_combat, find_weapon_slot};
use crate::map::{ArenaMap, RoofEdgePoint, RoofHop};
use crate::protocol::*;
use crate::world_state::{ClimbState, RoofHopState, WorldState};

#[inline]
fn count_upgrade(me: &SelfObs, id: &str) -> usize {
    me.upgrades.iter().filter(|u| u.as_str() == id).count()
}

#[inline]
fn max_climb_height(me: &SelfObs) -> f64 {
    let rise = me.stat("max_rise_speed", 9.0).max(1.0);
    ((me.max_energy - 12.0).max(0.0) / me.jet_drain().max(5.0)) * (rise * 0.94)
}

fn is_roof_climbable(
    map: &ArenaMap,
    obs: &Observation,
    state: &WorldState,
    roof_idx: usize,
    cur_roof: Option<usize>,
    for_laser_pickup: bool,
) -> bool {
    if cur_roof == Some(roof_idx) {
        return true;
    }
    if !for_laser_pickup || obs.t < state.climb_cooldown_until {
        return false; // Ground discipline for combat; allow climbing ONLY to grab a seen rooftop Laser!
    }
    let me = &obs.me;
    let max_h = max_climb_height(me);
    let rise = me.stat("max_rise_speed", 9.0).max(1.0);
    let roof = &map.rooftops[roof_idx];
    roof.edge_pts.iter().any(|ep| {
        if !ep.launch_clear {
            return false;
        }
        let launch_ground = map.terrain_z(ep.launch_xy[0], ep.launch_xy[1]);
        if ep.drop_z > launch_ground.max(me.pos[2]) + me.step_up() + 2.0 {
            return false;
        }
        let climb_dh = (ep.pos[2] - ep.drop_z).max(0.0);
        if climb_dh > max_h || climb_dh > 28.0 {
            return false;
        }
        let e_need = (climb_dh / (rise * 0.94)).max(0.5) * me.jet_drain() + 10.0;
        e_need <= me.max_energy - 2.0
            && dist2d(ep.launch_xy, obs.zone.center) <= obs.zone.radius - 4.0
    })
}

fn target_pos_reachable(
    map: &ArenaMap,
    obs: &Observation,
    state: &WorldState,
    pos: [f64; 3],
    cur_roof: Option<usize>,
    allow_roof_climb: bool,
) -> bool {
    let tz = map.terrain_z(pos[0], pos[1]);
    if pos[2] <= tz + 2.5 {
        return true;
    }
    if let Some(r_idx) = map.rooftop_at(pos[0], pos[1], pos[2]) {
        if cur_roof == Some(r_idx) {
            return true;
        }
        return allow_roof_climb && is_roof_climbable(map, obs, state, r_idx, cur_roof, allow_roof_climb);
    }
    (pos[2] - obs.me.pos[2]).abs() < 4.0
}

/// Cube farming cap: 15 cubes in Practice (5th upgrade + late supply drops!), 10 in FFA (avoid hitbox bloat!)
#[inline]
fn cube_farm_cap(state: &WorldState) -> i64 {
    if state.is_ffa() { 10 } else { 15 }
}

pub fn pick_upgrade(offer: &Offer, obs: &Observation, state: &WorldState) -> usize {
    let me = &obs.me;
    let have_laser = find_weapon_slot(me, "laser").is_some();
    let have_gun = find_weapon_slot(me, "gun").is_some();
    let have_mortar = find_weapon_slot(me, "mortar").is_some();
    let have_light = find_weapon_slot(me, "lightning").is_some();
    let is_ffa = state.is_ffa();
    let hp_frac = me.hp / me.max_hp.max(1.0);

    let mut best_idx = 0usize;
    let mut best_score = f64::NEG_INFINITY;

    for (i, ch) in offer.choices.iter().enumerate() {
        let stacks = count_upgrade(me, &ch.id);
        let s = match ch.id.as_str() {
            "nanites" => {
                let base = if is_ffa {
                    if stacks == 0 { 132.0 } else { 124.0 }
                } else {
                    if stacks == 0 { 138.0 } else { 132.0 }
                };
                if hp_frac < 0.75 { base + 6.0 } else { base }
            }
            // Zone damage ignores armor -> Plating (+15% max HP + heal) beats Armor (-8% dmg = +8.7% EHP)!
            "plating" => if is_ffa { 138.0 } else { 128.0 },
            "armor" => if is_ffa { 114.0 } else { 116.0 },
            "laser_damage" => if have_laser { if is_ffa { 124.0 } else { 130.0 } } else { 32.0 },
            "laser_charge" => if have_laser { if is_ffa { 118.0 } else { 125.0 } } else { 32.0 },
            "lightning_range" => if have_light { if is_ffa { 106.0 } else { 110.0 } } else { 28.0 },
            "servos" => if is_ffa { 109.0 } else { 104.0 },
            "lightning_efficiency" => if have_light { if is_ffa { 98.0 } else { 102.0 } } else { 25.0 },
            "jets" => if stacks == 0 { if is_ffa { 92.0 } else { 96.0 } } else { 80.0 },
            "lightning_chain" => if have_light { if is_ffa { 62.0 } else { 96.0 } } else { 20.0 },
            "reactor" => if is_ffa { 88.0 } else { 91.0 },
            "capacitor" => if is_ffa { 82.0 } else { 86.0 },
            "gun_mag" => if have_gun { if have_laser { 100.0 } else { 106.0 } } else { 18.0 },
            "gun_rate" => if have_gun { if have_laser { 96.0 } else { 102.0 } } else { 18.0 },
            "gun_damage" => if have_gun { if have_laser { 94.0 } else { 100.0 } } else { 18.0 },
            "gun_velocity" => if have_gun { if have_laser { 76.0 } else { 82.0 } } else { 15.0 },
            "sensors" => if is_ffa { 96.0 } else { 70.0 },
            "gyros" => 76.0,
            "laser_pierce" => if have_laser { if is_ffa { 54.0 } else { 80.0 } } else { 18.0 },
            "mortar_reload" => if have_mortar { 76.0 } else { 15.0 },
            "mortar_splash" => if have_mortar { 72.0 } else { 15.0 },
            "mortar_velocity" => if have_mortar { 68.0 } else { 15.0 },
            _ => 48.0,
        };
        if s > best_score {
            best_score = s;
            best_idx = i;
        }
    }
    best_idx
}

/// Aggressive Laser acquisition (up to 155m!), FORBIDS Mortar in Practice/Unknown (+60% cookoff!),
/// upgrades Laser+Gun -> Laser+Lightning once zone tightens or energy upgrades online.
#[allow(clippy::too_many_arguments)]
pub fn decide_weapon_pickup(
    obs: &Observation,
    state: &WorldState,
    map: &ArenaMap,
    cur_roof: Option<usize>,
    ready_for_rooftops: bool,
    enemy_laser_los_close: bool,
    enemies_with_los: usize,
    nearest_enemy_d: f64,
) -> (Option<i64>, Option<usize>, Option<[f64; 3]>, bool) {
    let me = &obs.me;
    let reach = state.config.loot.pickup_radius;

    let s0 = me.weapons.first().and_then(|w| w.as_ref());
    let s1 = me.weapons.get(1).and_then(|w| w.as_ref());
    let already_has = |k: &str| {
        s0.map(|w| w.kind == k).unwrap_or(false) || s1.map(|w| w.kind == k).unwrap_or(false)
    };
    let laser_charging_slot = |slot: usize| -> bool {
        me.weapons
            .get(slot)
            .and_then(|w| w.as_ref())
            .map(|w| w.kind == "laser" && w.charge.unwrap_or(0.0) > 0.0)
            .unwrap_or(false)
    };

    let gun_ups = count_upgrade(me, "gun_rate")
        + count_upgrade(me, "gun_damage")
        + count_upgrade(me, "gun_velocity")
        + count_upgrade(me, "gun_mag");
    let energy_ups = count_upgrade(me, "jets") + count_upgrade(me, "reactor") + count_upgrade(me, "capacitor");

    let evaluate_kind = |kind: &str| -> Option<(f64, Option<usize>)> {
        if already_has(kind) {
            return None;
        }
        // NEVER pick up Mortar in Practice OR FFA (+60% enemy laser cookoff = 504..1474 dmg instant death + 350m sound beacon!)
        if kind == "mortar" {
            return None;
        }

        if s0.is_none() || s1.is_none() {
            let base = match kind {
                "laser" => 185.0,
                "lightning" => 140.0,
                "gun" => 110.0,
                _ => 90.0,
            };
            return Some((base, None));
        }

        let w0 = s0.unwrap();
        let w1 = s1.unwrap();

        // Always upgrade to Laser if missing! (Keep Gun or Lightning alongside Laser)
        if kind == "laser" {
            let drop_slot = if w1.kind == "mortar" { 1 }
                else if w0.kind == "mortar" { 0 }
                else if w1.kind != "gun" { 1 }
                else { 0 };
            return Some((180.0, Some(drop_slot)));
        }

        // Replace Mortar with Lightning immediately
        if kind == "lightning" && already_has("mortar") {
            let m_slot = if w0.kind == "mortar" { 0 } else { 1 };
            return Some((135.0, Some(m_slot)));
        }

        // Late-game close quarters (next_radius <= 70 or radius <= 85): swap Gun -> Lightning! (Opus #7 — drops broken energy_ups gate!)
        if kind == "lightning" && already_has("laser") && already_has("gun") {
            let mid_gunfight = enemies_with_los >= 1 && nearest_enemy_d > 48.0;
            let close_endgame = obs.zone.next_radius <= 70.0 || obs.zone.radius <= 85.0;
            if !mid_gunfight && close_endgame && gun_ups <= 2 {
                let gun_slot = if w0.kind == "gun" { 0 } else { 1 };
                return Some((130.0, Some(gun_slot)));
            }
        }
        None
    };

    let _ = energy_ups;
    let mut best_take: Option<(f64, i64, Option<usize>)> = None;
    for p in &obs.pickups {
        let dh = dist2d([me.pos[0], me.pos[1]], [p.pos[0], p.pos[1]]);
        let dz = (me.pos[2] - p.pos[2]).abs();
        if dh < reach - 0.15 && dz < 3.85 {
            if let Some((val, drop_slot)) = evaluate_kind(&p.kind) {
                if let Some(ds) = drop_slot {
                    if laser_charging_slot(ds) { continue; }
                }
                if best_take.map(|(bv, _, _)| val > bv).unwrap_or(true) {
                    best_take = Some((val, p.id, drop_slot));
                }
            }
        }
    }
    if let Some((_, pid, ds)) = best_take {
        return (Some(pid), ds, None, false);
    }

    if enemy_laser_los_close {
        if let Some(ms) = find_weapon_slot(me, "mortar") {
            if s0.is_some() && s1.is_some() {
                return (None, Some(ms), None, false);
            }
        }
    }

    // Separate Laser search (NO 155m cap — reachable Laser ALWAYS outranks nearby Lightning! Fable #1 + GPT-6.2 P1)
    let mut best_laser_goal: Option<(f64, [f64; 3])> = None;
    let mut best_other_goal: Option<(f64, [f64; 3])> = None;
    for p in state.pickups.values() {
        if let Some((val, _)) = evaluate_kind(&p.kind) {
            let d = dist2d([me.pos[0], me.pos[1]], [p.pos[0], p.pos[1]]);
            let is_laser = p.kind == "laser";
            let max_seek = if is_laser { 450.0 } else { 115.0 };
            if d > max_seek { continue; }
            if dist2d([p.pos[0], p.pos[1]], obs.zone.center) > obs.zone.radius - 5.0 { continue; }
            let allow_roof = ready_for_rooftops || is_laser;
            if !target_pos_reachable(map, obs, state, p.pos, cur_roof, allow_roof) {
                continue;
            }
            let dz = (p.pos[2] - me.pos[2]).max(0.0);
            let on_other_roof = p.pos[2] > map.terrain_z(p.pos[0], p.pos[1]) + 2.5
                && cur_roof != map.rooftop_at(p.pos[0], p.pos[1], p.pos[2]);
            if on_other_roof && !is_laser && me.energy < 45.0 {
                continue;
            }
            let sc = val - d * 1.05 - dz * 1.1;
            if is_laser {
                if best_laser_goal.map(|(bs, _)| sc > bs).unwrap_or(true) {
                    best_laser_goal = Some((sc, p.pos));
                }
            } else if best_other_goal.map(|(bs, _)| sc > bs).unwrap_or(true) {
                best_other_goal = Some((sc, p.pos));
            }
        }
    }

    if let Some((_, pos)) = best_laser_goal {
        return (None, None, Some(pos), true);
    }
    match best_other_goal {
        Some((_, pos)) => (None, None, Some(pos), false),
        None => (None, None, None, false),
    }
}

fn zone_at(z: &ZoneObs, now: f64, t_eval: f64) -> ([f64; 2], f64) {
    if t_eval <= z.shrink_start || z.shrink_end <= z.shrink_start {
        (z.center, z.radius)
    } else if t_eval >= z.shrink_end {
        (z.next_center, z.next_radius)
    } else {
        let base_t = now.max(z.shrink_start);
        let f = ((t_eval - base_t) / (z.shrink_end - base_t).max(0.05)).clamp(0.0, 1.0);
        (
            [
                z.center[0] + (z.next_center[0] - z.center[0]) * f,
                z.center[1] + (z.next_center[1] - z.center[1]) * f,
            ],
            z.radius + (z.next_radius - z.radius) * f,
        )
    }
}

struct EnemySummary {
    id: i64,
    pos: [f64; 3],
    muzzle: [f64; 3],
    center: [f64; 3],
    dist: f64,
    has_los: bool,
    hp: f64,
    max_hp: f64,
    charging: bool,
    has_lightning: bool,
    has_laser: bool,
    #[allow(dead_code)]
    laser_ready_t: f64,
}

#[derive(Clone, Copy)]
enum MoveCandidate {
    Pos([f64; 3]),
    Hop(RoofHop),
}

impl MoveCandidate {
    fn dest(&self) -> [f64; 3] {
        match self {
            MoveCandidate::Pos(p) => *p,
            MoveCandidate::Hop(h) => h.to_pos,
        }
    }
}

fn select_climb_edge(
    map: &ArenaMap,
    roof_idx: usize,
    obs: &Observation,
    enemies: &[EnemySummary],
) -> Option<RoofEdgePoint> {
    let me = &obs.me;
    let roof = &map.rooftops[roof_idx];
    let max_h = max_climb_height(me);
    let rise = me.stat("max_rise_speed", 9.0).max(1.0);

    let mut best: Option<(f64, RoofEdgePoint)> = None;
    for &ep in &roof.edge_pts {
        if !ep.launch_clear {
            continue;
        }
        let launch_ground = map.terrain_z(ep.launch_xy[0], ep.launch_xy[1]);
        if ep.drop_z > launch_ground.max(me.pos[2]) + me.step_up() + 2.0 {
            continue;
        }
        let d_me = dist2d([me.pos[0], me.pos[1]], ep.launch_xy);
        let climb_dh = (ep.pos[2] - ep.drop_z).max(0.0);
        let eff_h = if d_me < 12.0 {
            climb_dh.max((ep.pos[2] - me.pos[2]).max(0.0))
        } else {
            climb_dh
        };
        if eff_h > max_h {
            continue;
        }
        // Require enough max energy to clear this roof edge!
        let e_need = (eff_h / (rise * 0.94)).max(0.5) * me.jet_drain() + 10.0;
        if e_need > me.max_energy - 2.0 {
            continue;
        }
        if dist2d(ep.launch_xy, obs.zone.center) > obs.zone.radius - 4.0 {
            continue;
        }
        let mut sc = -d_me * 1.15 - climb_dh * 0.6;
        let l_chk = [ep.launch_xy[0], ep.launch_xy[1], ep.drop_z + me.height() * 0.78];
        for e in enemies.iter().take(5) {
            if dist3d(e.muzzle, l_chk) < 155.0 && map.has_shot_los(e.muzzle, l_chk) {
                sc -= 65.0;
            }
        }
        if best.map(|(bs, _)| sc > bs).unwrap_or(true) {
            best = Some((sc, ep));
        }
    }
    best.map(|(_, ep)| ep)
}

pub fn decide_action(obs: &Observation, state: &mut WorldState, map: &ArenaMap) -> Action {
    let me = &obs.me;
    let t = obs.t;
    let is_ffa = state.is_ffa();
    let base_h = state.config.mech_f("height", 9.0);
    let base_muz = state.config.mech_f("muzzle_height", 7.0);
    let spc = state.config.cubes.size_per_cube;
    let hp_frac = me.hp / me.max_hp.max(1.0);
    let cur_roof = map.rooftop_at(me.pos[0], me.pos[1], me.pos[2]);

    let mut act = Action {
        tick: obs.tick,
        ..Default::default()
    };

    // 1. Upgrade offer
    if let Some(offer) = &obs.offer {
        if !offer.choices.is_empty() {
            act.pick = Some(pick_upgrade(offer, obs, state));
        }
    }

    // 2. Analyze immediate threats & exposure
    let my_muzzle = me.muzzle_pos();
    let my_upper = [me.pos[0], me.pos[1], me.pos[2] + me.height() * 0.78];
    let mut visible_enemies: Vec<EnemySummary> = Vec::new();
    let mut enemies_with_los = 0usize;
    let mut enemies_within_115 = 0usize;
    let mut nearest_enemy_d = f64::INFINITY;
    let mut any_enemy_charging = false;
    let mut enemy_lightning_within_52 = false;
    let mut enemy_laser_los_close = false;
    let mut max_enemy_hp = 0.0f64;

    for e in state.enemies.values() {
        if t - e.last_seen_t > 5.0 { continue; }
        let eh = e.height(base_h, spc);
        let ec = e.center(base_h, spc);
        let em = e.muzzle(base_muz, spc);
        let d = dist3d(my_muzzle, ec);
        if d < nearest_enemy_d { nearest_enemy_d = d; }
        if d < 115.0 { enemies_within_115 += 1; }
        if e.hp > max_enemy_hp { max_enemy_hp = e.hp; }
        let los = (e.visible_now || (e.source_is_beam && t - e.last_seen_t < 0.40))
            && (best_los_point(map, my_muzzle, e.pos, eh).is_some() || map.has_shot_los(em, my_upper));
        if los { enemies_with_los += 1; }
        if e.visible_now && e.charging { any_enemy_charging = true; }
        let has_lt = e.has_weapon("lightning");
        let has_ls = e.has_weapon("laser");
        if has_lt && d < 52.0 && (e.visible_now || t - e.last_seen_t < 1.5) { enemy_lightning_within_52 = true; }
        if has_ls && los && d < 220.0 { enemy_laser_los_close = true; }
        visible_enemies.push(EnemySummary {
            id: e.id,
            pos: e.pos,
            muzzle: em,
            center: ec,
            dist: d,
            has_los: los,
            hp: e.hp,
            max_hp: e.max_hp,
            charging: e.charging,
            has_lightning: has_lt,
            has_laser: has_ls,
            laser_ready_t: e.laser_ready_t,
        });
    }

    if (t - state.last_damaged_t) < 2.0 {
        if let (Some(by), Some(bear)) = (state.last_damaged_by, state.last_damaged_bearing) {
            if (0..100).contains(&by) && !visible_enemies.iter().any(|e| e.id == by) {
                let gx = me.pos[0] + bear.cos() * 75.0;
                let gy = me.pos[1] + bear.sin() * 75.0;
                let gz = map.terrain_z(gx, gy);
                visible_enemies.push(EnemySummary {
                    id: by,
                    pos: [gx, gy, gz],
                    muzzle: [gx, gy, gz + 7.0],
                    center: [gx, gy, gz + 4.5],
                    dist: 75.0,
                    has_los: true,
                    hp: 1200.0,
                    max_hp: 1400.0,
                    charging: false,
                    has_lightning: false,
                    has_laser: false,
                    laser_ready_t: -10.0,
                });
                enemies_with_los += 1;
                enemies_within_115 += 1;
                nearest_enemy_d = nearest_enemy_d.min(75.0);
            }
        }
    }

    // Pure ground building-cover discipline: stay on ground streets for 0.5s hard-cover slicing & Laser sweeping!
    let ready_for_rooftops = false;

    // 3. Weapon pickup / swap / emergency mortar drop
    let (take_id, drop_slot, desired_pickup_pos, desired_pickup_is_laser) = decide_weapon_pickup(
        obs,
        state,
        map,
        cur_roof,
        ready_for_rooftops,
        enemy_laser_los_close,
        enemies_with_los,
        nearest_enemy_d,
    );
    act.take = take_id;
    act.drop = drop_slot;
    let dropped_only_slot = if take_id.is_none() { drop_slot } else { None };

    // 4. Movement-Only Heal Hysteresis Latch (breaks LOS to heal 56%->88% Prac / 72%->90% FFA,
    //    while NEVER blocking Laser/Gun/Lightning if an enemy actually pushes into LOS!)
    let nanites = count_upgrade(me, "nanites");
    let heal_enter = if is_ffa {
        if nanites >= 1 { 0.74 } else { 0.68 }
    } else {
        if nanites >= 1 { 0.60 } else { 0.54 }
    };
    let heal_exit = if is_ffa {
        if nanites >= 1 { 0.92 } else { 0.86 }
    } else {
        if nanites >= 1 { 0.88 } else { 0.82 }
    };

    // Dynamic endgame timing derived from live zone phases (Opus #8 + GPT-6.2 P0-4)
    let final_circle = obs.zone.next_radius <= 1.0;
    let time_to_close = (obs.zone.shrink_end - t).max(0.0);
    let phase6_imminent = final_circle && time_to_close <= 18.0;
    let phase6_hp_race = (final_circle && time_to_close <= 9.0)
        || (is_ffa && obs.alive <= 2 && obs.zone.radius <= 35.0);
    let est_max_enemy_hp = if max_enemy_hp > 0.0 { max_enemy_hp } else { 1850.0 };
    let winning_hp_race = phase6_hp_race && me.hp > est_max_enemy_hp + 240.0;
    let losing_hp_race = phase6_hp_race && me.hp <= est_max_enemy_hp + 240.0;

    // TTK vs TTD Kill-Commit in Practice (Opus #3): finish wounded / winning 1v1 duels instead of backing off to let them regen!
    let dmg_mult = crate::combat::cube_damage_mult(me.cubes, &state.config);
    let have_laser = find_weapon_slot(me, "laser").is_some();
    let have_light = find_weapon_slot(me, "lightning").is_some();
    let have_gun = find_weapon_slot(me, "gun").is_some();
    let our_dps = (if have_laser { 315.0 / 2.4 * 0.85 } else { 0.0 }
        + if have_light && nearest_enemy_d < 46.0 {
            220.0
        } else if have_gun {
            45.0 / 0.24 * 0.55 * 0.55
        } else {
            0.0
        })
        * dmg_mult;
    let incoming_dps: f64 = visible_enemies
        .iter()
        .filter(|e| e.has_los)
        .map(|e| if e.has_laser { 131.0 } else { 65.0 } + 55.0)
        .sum::<f64>()
        .max(1.0);
    let best_duel_target = visible_enemies
        .iter()
        .filter(|e| e.has_los || e.dist < 65.0)
        .min_by(|a, b| a.hp.partial_cmp(&b.hp).unwrap_or(std::cmp::Ordering::Equal));
    let kill_commit = !is_ffa
        && me.in_zone
        && enemies_with_los <= 1
        && me.hp > 380.0
        && best_duel_target
            .map(|e| {
                let ttk = if have_laser && e.hp <= 315.0 * dmg_mult { 0.6 } else { e.hp / our_dps.max(40.0) };
                let ttd = me.hp / incoming_dps;
                ttk < 0.60 * ttd || (e.hp < e.max_hp * 0.35 && e.dist < 85.0)
            })
            .unwrap_or(false);

    if losing_hp_race || !me.in_zone || kill_commit {
        state.healing_mode = false;
    } else if hp_frac < heal_enter || (phase6_imminent && hp_frac < 0.94 && enemies_with_los == 0) {
        state.healing_mode = true;
    } else if hp_frac >= heal_exit {
        state.healing_mode = false;
    }

    let hold_fire_for_regen = me.in_zone && state.healing_mode && enemies_with_los == 0;

    // 5. Cube farming up to cap
    let want_more_cubes = me.cubes < cube_farm_cap(state);
    let missing_second_weapon = me.weapons.iter().flatten().count() < 2;

    let mut farm_crate_id = None;
    let mut farm_crate_pos = None;
    let mut farm_npc_id = None;
    let mut farm_npc_pos = None;

    let npc_hit_us = state.last_damaged_by.map(|id| (100..1000).contains(&id)).unwrap_or(false)
        && (t - state.last_damaged_t) < 2.5;
    {
        let mut best_aggro_d = if npc_hit_us { 95.0 } else { 52.0 };
        for n in state.npcs.values() {
            let d = dist2d([me.pos[0], me.pos[1]], [n.pos[0], n.pos[1]]);
            if d < best_aggro_d && map.has_shot_los(my_muzzle, [n.pos[0], n.pos[1], n.pos[2] + 1.75]) {
                best_aggro_d = d;
                farm_npc_id = Some(n.id);
                farm_npc_pos = Some(n.pos);
            }
        }
    }

    // Regen hygiene (Opus #4): don't shoot crates while hurt (<90% HP) unless we still lack Laser/2nd weapon!
    let crate_regen_ok = hp_frac >= 0.90 || missing_second_weapon || !have_laser;
    if me.in_zone && enemies_with_los == 0 && nearest_enemy_d > 55.0 && (want_more_cubes || missing_second_weapon || !have_laser) {
        if crate_regen_ok {
            let crate_max_d = if me.cubes < 6 || missing_second_weapon || !have_laser { 135.0 } else { 80.0 };
            let mut best_c_score = f64::NEG_INFINITY;
            for c in state.crates.values() {
                let d = dist2d([me.pos[0], me.pos[1]], [c.pos[0], c.pos[1]]);
                if d >= crate_max_d || !target_pos_reachable(map, obs, state, c.pos, cur_roof, ready_for_rooftops) { continue; }
                if dist2d([c.pos[0], c.pos[1]], obs.zone.center) >= obs.zone.radius - 10.0 { continue; }
                let los_bonus = if map.has_shot_los(my_muzzle, [c.pos[0], c.pos[1], c.pos[2] + 1.2]) { 40.0 } else { 0.0 };
                let sc = los_bonus - d;
                if sc > best_c_score {
                    best_c_score = sc;
                    farm_crate_id = Some(c.id);
                    farm_crate_pos = Some(c.pos);
                }
            }
        }
        if farm_npc_id.is_none() && want_more_cubes && hp_frac > 0.72 && !state.healing_mode {
            let mut best_nd = if me.cubes < 10 { 115.0 } else { 85.0 };
            for n in state.npcs.values() {
                let d = dist2d([me.pos[0], me.pos[1]], [n.pos[0], n.pos[1]]);
                if d < best_nd && dist2d([n.pos[0], n.pos[1]], obs.zone.center) < obs.zone.radius - 10.0 {
                    best_nd = d;
                    farm_npc_id = Some(n.id);
                    farm_npc_pos = Some(n.pos);
                }
            }
        }
    }

    // 6. Active Dodge + Combat Decision
    let dodge = compute_dodge(obs, state, map);
    let combat = decide_combat(
        obs,
        state,
        map,
        hold_fire_for_regen,
        farm_crate_id,
        farm_npc_id,
        &dodge,
        dropped_only_slot,
    );
    act.fire = Some(combat.fire);
    if combat.reload {
        act.reload = Some(true);
    }

    // 7. Zone urgency (including Phase 6 final 10m -> 0m shrink!)
    let dist_to_cur_center = dist2d([me.pos[0], me.pos[1]], obs.zone.center);
    let dist_to_next_center = dist2d([me.pos[0], me.pos[1]], obs.zone.next_center);
    let margin_cur = obs.zone.radius - dist_to_cur_center;
    let margin_next = obs.zone.next_radius - dist_to_next_center;
    let time_to_shrink_end = (obs.zone.shrink_end - t).max(0.0);
    let travel_time_next = (-margin_next).max(0.0) / me.walk_speed().max(5.0);
    let safe_margin = (obs.zone.radius * 0.42).min(if obs.zone.dps >= 100.0 { 18.0 } else { 14.0 });
    let zone_urgent = !me.in_zone
        || (obs.zone.radius > 10.0 && margin_cur < safe_margin)
        || (obs.zone.next_radius > 5.0
            && margin_next < -4.0
            && (t >= obs.zone.shrink_start - 10.0 || travel_time_next + 11.0 >= time_to_shrink_end))
        || (obs.zone.next_radius <= 30.0
            && (t >= obs.zone.shrink_start - 12.0 || obs.zone.radius <= 75.0)
            && dist_to_next_center > (obs.zone.next_radius * 0.45).max(1.2))
        || (obs.zone.next_radius <= 5.0
            && obs.zone.radius <= 30.0
            && dist_to_next_center > (obs.zone.radius * 0.30).max(0.35));

    if me.energy > 22.0 {
        state.zone_boost_active = true;
    } else if me.energy < 8.0 {
        state.zone_boost_active = false;
    }

    // Best nearby cube (up to CUBE_FARM_CAP!)
    let mut best_cube_pos: Option<[f64; 3]> = None;
    if want_more_cubes {
        let mut best_d = if enemies_with_los == 0 { 115.0 } else if enemies_with_los == 1 && hp_frac > 0.65 { 26.0 } else { 12.0 };
        for cb in state.cubes.values() {
            let d = dist2d([me.pos[0], me.pos[1]], [cb.pos[0], cb.pos[1]]);
            if d < best_d
                && target_pos_reachable(map, obs, state, cb.pos, cur_roof, ready_for_rooftops)
                && dist2d([cb.pos[0], cb.pos[1]], obs.zone.center) < obs.zone.radius - 4.0
            {
                best_d = d;
                best_cube_pos = Some(cb.pos);
            }
        }
    }

    // Supply drops: pick CLOSEST reachable drop (full HP + full energy + 3 cubes = ~31s regen! Opus #4 + Fable #4)
    let mut best_drop_pos: Option<[f64; 3]> = None;
    let mut best_drop_eta = f64::INFINITY;
    let mut finishing_drop_pos: Option<[f64; 3]> = None;
    for dr in &obs.drops {
        let d = dist2d([me.pos[0], me.pos[1]], [dr.pos[0], dr.pos[1]]);
        if dist2d([dr.pos[0], dr.pos[1]], obs.zone.center) > obs.zone.radius - 6.0 {
            continue;
        }
        if dr.holder == Some(me.id) && dr.progress > 1.0 && enemies_with_los <= 1 && hp_frac > 0.25 {
            finishing_drop_pos = Some(dr.pos);
        }
        let eta = d / me.walk_speed().max(8.0);
        let ready_to_cap = dr.lands_in <= eta + 12.0;
        if ready_to_cap && d < 210.0 && enemies_with_los <= 1 && (want_more_cubes || hp_frac < 0.78) {
            if !is_ffa || hp_frac < 0.78 || dr.holder.is_none() || dr.holder == Some(me.id) {
                let score_eta = eta.max(dr.lands_in);
                if score_eta < best_drop_eta {
                    best_drop_eta = score_eta;
                    best_drop_pos = Some(dr.pos);
                }
            }
        }
    }

    // Update active RoofHopState
    if let Some(mut hop) = state.roof_hop {
        let landed = me.grounded && cur_roof == Some(hop.to_roof_idx);
        let timed_out = t - hop.started_t > 3.5;
        let fell_ground = me.grounded && cur_roof.is_none();
        let to_out_zone = dist2d([hop.to_pos[0], hop.to_pos[1]], obs.zone.center) > obs.zone.radius - 3.0;
        let p0_abort = hop.phase == 0 && (me.energy < hop.min_energy + 4.0 || dodge.urgency >= 2.2);
        if landed || timed_out || fell_ground || zone_urgent || to_out_zone || p0_abort {
            state.roof_hop = None;
            if !landed { state.climb_cooldown_until = t + 3.0; }
        } else {
            let d_from = dist2d([me.pos[0], me.pos[1]], [hop.from_pos[0], hop.from_pos[1]]);
            if hop.phase == 0 && d_from < 2.2 && me.grounded && me.energy >= hop.min_energy + 4.0 {
                hop.phase = 1;
                hop.started_t = t;
            } else if hop.phase == 1 && dist2d([me.pos[0], me.pos[1]], [hop.to_pos[0], hop.to_pos[1]]) < 4.0 && me.pos[2] >= hop.to_pos[2] - 0.5 {
                hop.phase = 2;
            }
            state.roof_hop = Some(hop);
        }
    }

    // NEVER-FREEZE ClimbState update (height-scaled ascent timeout for 40..78m roofs!)
    let rise_speed = me.stat("max_rise_speed", 9.0).max(1.0);
    if let Some(mut cs) = state.climb_state {
        let reached = cur_roof == Some(cs.roof_idx) && me.pos[2] >= cs.edge_pos[2] - 0.5;
        let climb_h = (cs.edge_pos[2] - map.terrain_z(cs.launch_xy[0], cs.launch_xy[1])).max(8.0);
        let asc_timeout = (climb_h / rise_speed.max(5.0) + 2.2).clamp(4.5, 9.5);
        let timed_out = if cs.ascending { t - cs.started_t > asc_timeout } else { t - cs.started_t > 13.0 };
        let out_of_fuel = cs.ascending && me.energy < 5.0;
        let overhang = cs.ascending && map.is_covered_above(me.pos[0], me.pos[1], me.pos[2] + me.height() - 0.5, 3.5);
        let too_hot = enemies_with_los >= 1 && (nearest_enemy_d < 55.0 || enemies_with_los >= 2 || hp_frac < 0.60);
        let edge_out_zone = dist2d([cs.edge_pos[0], cs.edge_pos[1]], obs.zone.center) > obs.zone.radius - 2.0
            || dist2d(cs.launch_xy, obs.zone.center) > obs.zone.radius - 2.0;

        if reached || timed_out || out_of_fuel || overhang || too_hot || edge_out_zone || zone_urgent || finishing_drop_pos.is_some() {
            state.climb_state = None;
            if !reached { state.climb_cooldown_until = t + 4.0; }
        } else {
            if !cs.ascending {
                let d_launch = dist2d([me.pos[0], me.pos[1]], cs.launch_xy);
                if d_launch < 6.0 {
                    let dz = (cs.edge_pos[2] - me.pos[2]).max(0.0);
                    let e_need = ((dz / (rise_speed * 0.94)).max(0.5) * me.jet_drain() + 10.0).min(me.max_energy - 2.0);
                    let blocked_up = map.is_covered_above(me.pos[0], me.pos[1], me.pos[2] + me.height() - 0.5, 3.5);
                    if !blocked_up && me.energy >= e_need {
                        cs.ascending = true;
                        cs.started_t = t;
                    } else if blocked_up || me.energy < e_need - 18.0 {
                        // NEVER freeze motionless at launch_xy! Abort cleanly!
                        state.climb_state = None;
                        state.climb_cooldown_until = t + 3.5;
                    }
                }
            }
            if state.climb_state.is_some() {
                state.climb_state = Some(cs);
            }
        }
    }

    // 8. Macro & Tactical Goal Selection
    let mut goal_3d: [f64; 3];
    let mut want_boost = dodge.want_boost;
    let mut want_jump = dodge.want_jump;
    let mut stealth_walk = false;
    let mut chosen_hop: Option<RoofHop> = None;

    let safe_for_quick_cube = want_more_cubes && hp_frac > 0.65 && !any_enemy_charging && !enemy_lightning_within_52;
    let laser_loot_priority = !have_laser
        && hp_frac > 0.38
        && enemies_with_los <= 1
        && !enemy_lightning_within_52
        && desired_pickup_is_laser
        && desired_pickup_pos.is_some();
    let kill_drop_laser_search = !have_laser
        && desired_pickup_pos.is_none()
        && hp_frac > 0.42
        && state
            .last_kill_drop_pos
            .map(|(p, kt, hl)| {
                let d = dist2d([me.pos[0], me.pos[1]], [p[0], p[1]]);
                let ok = if hl { enemies_with_los <= 1 && !enemy_lightning_within_52 && t - kt < 55.0 && d < 220.0 }
                         else { enemies_with_los == 0 && t - kt < 25.0 && d < 135.0 };
                ok && d > 5.0 && dist2d([p[0], p[1]], obs.zone.center) < obs.zone.radius - 6.0
            })
            .unwrap_or(false);
    let confirmed_kill_laser = kill_drop_laser_search && state.last_kill_drop_pos.map(|(_, _, hl)| hl).unwrap_or(false);
    let urgent_heal_drop = hp_frac < 0.78 && best_drop_pos.is_some() && enemies_with_los == 0;
    let mut goal_is_laser_pickup = false;

    if zone_urgent {
        goal_3d = find_safe_zone_target(obs, map, margin_cur);
        // Disable boost close to exact final-circle pin so we never overshoot at 260 HP/s! (Fixes Red-Team P1-1)
        let near_final_pin = final_circle && me.in_zone && dist_to_next_center < 5.0;
        if (!me.in_zone || margin_cur < 10.0 || obs.zone.dps >= 100.0) && state.zone_boost_active && !near_final_pin {
            want_boost = true;
        }
    } else if let Some(hop) = state.roof_hop {
        goal_3d = if hop.phase == 0 { hop.from_pos } else { hop.to_pos };
    } else if let Some(dp) = finishing_drop_pos {
        goal_3d = dp;
    } else if let Some(cs) = state.climb_state {
        goal_3d = if cs.ascending { cs.edge_pos } else { [cs.launch_xy[0], cs.launch_xy[1], map.terrain_z(cs.launch_xy[0], cs.launch_xy[1])] };
    } else if laser_loot_priority {
        goal_3d = desired_pickup_pos.unwrap();
        goal_is_laser_pickup = true;
        if me.energy > 22.0 { want_boost = true; }
    } else if confirmed_kill_laser {
        goal_3d = state.last_kill_drop_pos.unwrap().0;
        if me.energy > 24.0 { want_boost = true; }
    } else if missing_second_weapon
        && hp_frac > 0.45
        && enemies_with_los <= 1
        && !enemy_lightning_within_52
        && desired_pickup_pos.map(|p| dist2d([me.pos[0], me.pos[1]], [p[0], p[1]]) < 95.0 && (p[2] - me.pos[2]).abs() < 6.0).unwrap_or(false)
    {
        goal_3d = desired_pickup_pos.unwrap();
        goal_is_laser_pickup = desired_pickup_is_laser;
        if me.energy > 24.0 { want_boost = true; }
    } else if safe_for_quick_cube
        && best_cube_pos.map(|p| {
            let max_d = if me.cubes == 0 { 42.0 } else { 24.0 };
            dist2d([me.pos[0], me.pos[1]], [p[0], p[1]]) < max_d && (p[2] - me.pos[2]).abs() < 6.0 && enemies_with_los <= 1
        }).unwrap_or(false)
    {
        goal_3d = best_cube_pos.unwrap();
        if me.energy > 30.0 { want_boost = true; }
    } else if !visible_enemies.is_empty()
        && (enemies_with_los >= 1
            || nearest_enemy_d < (if have_laser { 95.0 } else { 42.0 })
            || (have_laser && cur_roof.is_some() && nearest_enemy_d < 185.0))
    {
        let reload_retreat = combat.gun_reloading
            && !have_laser
            && !(have_light && nearest_enemy_d < 46.0)
            && (!is_ffa || hp_frac < 0.65 || enemies_with_los >= 2);
        let gun_only_outnumbered = !is_ffa && !have_laser && !have_light && hp_frac < 0.85 && enemies_within_115 >= 2;
        let need_heal = state.healing_mode || enemies_with_los >= 2;
        // Peek-Fire-Hide against ACTUAL Laser holders (denies real Expert bots' 315-dmg Laser during our 1.65s CD!)
        let enemy_laser_ready_soon = visible_enemies
            .iter()
            .any(|e| e.has_los && e.has_laser && e.laser_ready_t <= t + 0.85);
        let laser_cd_cover = have_laser
            && !kill_commit
            && !combat.charging_laser
            && combat.laser_cd > 0.60
            && enemies_with_los >= 1
            && enemy_laser_ready_soon
            && (hp_frac < 0.90 || enemies_with_los >= 2);
        let want_cover_only = (winning_hp_race
            || need_heal
            || reload_retreat
            || gun_only_outnumbered
            || laser_cd_cover
            || (is_ffa && obs.alive > 4 && hp_frac < 0.85 && visible_enemies.iter().all(|e| e.hp > 700.0)))
            && !losing_hp_race
            && !kill_commit
            && !combat.want_peek;

        let laser_sweep_target = None;

        let chosen = pick_tactical_position(
            obs,
            state,
            map,
            &visible_enemies,
            combat.target_enemy_id,
            want_cover_only,
            combat.want_peek || losing_hp_race || kill_commit,
            cur_roof,
            !want_more_cubes,
            ready_for_rooftops,
            laser_sweep_target,
        );
        match chosen {
            MoveCandidate::Pos(p) => {
                goal_3d = p;
                state.last_tactical_goal = Some(p);
            }
            MoveCandidate::Hop(h) => {
                chosen_hop = Some(h);
                goal_3d = h.from_pos;
                state.roof_hop = Some(RoofHopState {
                    from_pos: h.from_pos,
                    to_pos: h.to_pos,
                    to_roof_idx: h.to_roof_idx,
                    min_energy: h.min_energy,
                    phase: 0,
                    started_t: t,
                });
            }
        }

        let escaping_lightning = enemy_lightning_within_52 && !have_light;
        if (enemies_with_los >= 2 || reload_retreat || gun_only_outnumbered || laser_cd_cover || (need_heal && enemies_with_los >= 1) || escaping_lightning)
            && me.energy > 16.0
        {
            want_boost = true;
        }
    } else if let Some(pp) = desired_pickup_pos.filter(|_| !have_laser) {
        goal_3d = pp;
        goal_is_laser_pickup = desired_pickup_is_laser;
        if me.energy > 28.0 { want_boost = true; }
    } else if kill_drop_laser_search {
        goal_3d = state.last_kill_drop_pos.unwrap().0;
        if me.energy > 35.0 { want_boost = true; }
    } else if !have_laser && farm_crate_pos.is_some() {
        // Crack nearby crates aggressively while missing Laser (50% weapon drop + cube #1 instant upgrade!)
        let cp = farm_crate_pos.unwrap();
        let d = dist2d([me.pos[0], me.pos[1]], [cp[0], cp[1]]);
        goal_3d = if d > 12.0 || (cp[2] - me.pos[2]).abs() >= 4.0 { cp } else { me.pos };
        if d > 18.0 && me.energy > 35.0 { want_boost = true; }
    } else if urgent_heal_drop {
        goal_3d = best_drop_pos.unwrap();
        if dist2d([me.pos[0], me.pos[1]], [goal_3d[0], goal_3d[1]]) > 18.0 && me.energy > 35.0 {
            want_boost = true;
        }
    } else if let Some(dp) = best_drop_pos {
        goal_3d = dp;
        if dist2d([me.pos[0], me.pos[1]], [dp[0], dp[1]]) > 22.0 && me.energy > 42.0 {
            want_boost = true;
        }
    } else if let Some(cp) = best_cube_pos {
        goal_3d = cp;
        if dist2d([me.pos[0], me.pos[1]], [cp[0], cp[1]]) > 12.0 && me.energy > 40.0 {
            want_boost = true;
        }
    } else if let Some(pp) = desired_pickup_pos {
        goal_3d = pp;
        goal_is_laser_pickup = desired_pickup_is_laser;
        if me.energy > 40.0 { want_boost = true; }
    } else if let Some(cp) = farm_crate_pos {
        let d = dist2d([me.pos[0], me.pos[1]], [cp[0], cp[1]]);
        goal_3d = if d > 12.0 || (cp[2] - me.pos[2]).abs() >= 4.0 { cp } else { me.pos };
    } else if let Some(np) = farm_npc_pos {
        let d = dist2d([me.pos[0], me.pos[1]], [np[0], np[1]]);
        if d > 58.0 {
            goal_3d = np;
        } else if d < 38.0 {
            let ang = (me.pos[1] - np[1]).atan2(me.pos[0] - np[0]);
            let tx = me.pos[0] + ang.cos() * 20.0;
            let ty = me.pos[1] + ang.sin() * 20.0;
            goal_3d = [tx, ty, map.terrain_z(tx, ty)];
        } else {
            let ang = (me.pos[1] - np[1]).atan2(me.pos[0] - np[0]) + state.strafe_sign * 1.4;
            let tx = me.pos[0] + ang.cos() * 14.0;
            let ty = me.pos[1] + ang.sin() * 14.0;
            goal_3d = [tx, ty, map.terrain_z(tx, ty)];
        }
    } else {
        goal_3d = pick_macro_hold_spot(obs, state, map, cur_roof, margin_cur, ready_for_rooftops);
        if !have_laser && me.energy > 20.0 {
            want_boost = true;
        }
        if have_laser && dist2d([me.pos[0], me.pos[1]], [goal_3d[0], goal_3d[1]]) < 12.0 {
            stealth_walk = true;
            want_boost = false;
        }
    }

    // 9. Initiate Edge-Launch Climb when safe & ready_for_rooftops (or chasing a climbable rooftop Laser)!
    let min_climb_enemy_d = if !is_ffa && have_laser && hp_frac > 0.52 && me.energy > 50.0 {
        26.0
    } else {
        48.0
    };
    if !zone_urgent
        && (ready_for_rooftops || goal_is_laser_pickup)
        && enemies_with_los == 0
        && nearest_enemy_d >= min_climb_enemy_d
        && state.climb_state.is_none()
        && state.roof_hop.is_none()
        && chosen_hop.is_none()
        && t >= state.climb_cooldown_until
    {
        if let Some(target_roof_idx) = map.rooftop_at(goal_3d[0], goal_3d[1], goal_3d[2]) {
            if cur_roof != Some(target_roof_idx) && goal_3d[2] > map.terrain_z(goal_3d[0], goal_3d[1]) + 2.5 {
                if let Some(ep) = select_climb_edge(map, target_roof_idx, obs, &visible_enemies) {
                    let d_launch = dist2d([me.pos[0], me.pos[1]], ep.launch_xy);
                    let dz = if d_launch < 12.0 {
                        (ep.pos[2] - me.pos[2]).max(0.0)
                    } else {
                        (ep.pos[2] - ep.drop_z).max(0.0)
                    };
                    let e_need = ((dz / (rise_speed * 0.94)).max(0.5) * me.jet_drain() + 10.0).min(me.max_energy - 2.0);
                    let blocked_up = map.is_covered_above(me.pos[0], me.pos[1], me.pos[2] + me.height() - 0.5, 3.5);
                    // Preserve jet energy while approaching a rooftop climb (prevents boost-drain deadlock on 28-49m roofs!)
                    if me.energy < e_need + 10.0 && dodge.urgency < 1.5 {
                        want_boost = false;
                    }
                    if me.energy >= e_need - 14.0 {
                        let start_asc = d_launch < 6.0 && !blocked_up && me.energy >= e_need;
                        state.climb_state = Some(ClimbState {
                            roof_idx: target_roof_idx,
                            launch_xy: ep.launch_xy,
                            edge_pos: ep.pos,
                            goal_pos: goal_3d,
                            ascending: start_asc,
                            started_t: t,
                        });
                        goal_3d = if start_asc {
                            ep.pos
                        } else {
                            [ep.launch_xy[0], ep.launch_xy[1], ep.drop_z]
                        };
                    } else {
                        // Walk to the ground launch pad while regenerating energy instead of walking into the building wall!
                        goal_3d = [ep.launch_xy[0], ep.launch_xy[1], ep.drop_z];
                    }
                }
            }
        }
    }

    // 10. Movement, Cube-Sized A*, Roof-Hop Execution, Edge-Launch Climb & Dodge Blending
    let mut move_vec = [0.0, 0.0];
    let dist_goal_2d = dist2d([me.pos[0], me.pos[1]], [goal_3d[0], goal_3d[1]]);
    let dz_goal = goal_3d[2] - me.pos[2];
    let in_air_hop = state.roof_hop.map(|h| h.phase == 1).unwrap_or(false);

    let stuck_overhang = map.is_covered_above(me.pos[0], me.pos[1], me.pos[2] + me.height() - 0.5, 4.0);
    let on_elevated_non_roof = me.pos[2] > map.terrain_z(me.pos[0], me.pos[1]) + 4.0 && cur_roof.is_none();
    let stuck_air_ok = t < state.stuck_escape_until
        && !stuck_overhang
        && (state.stuck_streak < 4 || on_elevated_non_roof)
        && (enemies_with_los == 0 || !me.in_zone || on_elevated_non_roof);
    if t < state.stuck_escape_until {
        move_vec = state.stuck_escape_dir;
        if stuck_air_ok && me.energy > 14.0 {
            if me.grounded {
                want_jump = true;
                want_boost = false;
            } else {
                want_boost = true;
            }
        } else if !me.grounded {
            want_boost = false;
        }
    } else if let Some(hop) = state.roof_hop {
        let tgt = if hop.phase == 0 { hop.from_pos } else { hop.to_pos };
        let dx = tgt[0] - me.pos[0];
        let dy = tgt[1] - me.pos[1];
        let d = dx.hypot(dy).max(1e-6);
        move_vec = [dx / d, dy / d];
        if hop.phase == 1 {
            if me.grounded {
                want_jump = true;
                want_boost = false;
            } else {
                let need_lift = me.pos[2] < hop.to_pos[2] + 1.2 || me.vel[2] < -1.5;
                want_boost = need_lift && me.energy > 6.0;
            }
        } else if hop.phase == 0 && me.energy > hop.min_energy + 18.0 {
            want_boost = true;
        }
    } else if let Some(cs) = state.climb_state {
        if cs.ascending {
            let dx = cs.edge_pos[0] - me.pos[0];
            let dy = cs.edge_pos[1] - me.pos[1];
            let d = dx.hypot(dy).max(1e-6);
            let h_scale = if me.pos[2] < cs.edge_pos[2] - 1.5 {
                0.22
            } else if me.pos[2] < cs.edge_pos[2] - 0.1 {
                0.85
            } else {
                1.0
            };
            move_vec = [(dx / d) * h_scale, (dy / d) * h_scale];
            if me.grounded && me.pos[2] < cs.edge_pos[2] - me.step_up() {
                want_jump = true;
                want_boost = false;
            } else if !me.grounded && me.pos[2] < cs.edge_pos[2] + 0.9 && me.energy > 5.0 {
                let pulse_off = dodge.urgency < 0.5
                    && enemies_with_los == 0
                    && me.vel[2] >= rise_speed - 0.3
                    && (obs.tick % 6 == 0)
                    && me.energy < 70.0;
                want_boost = !pulse_off;
            } else {
                want_boost = false;
            }
        } else {
            let d_launch = dist2d([me.pos[0], me.pos[1]], cs.launch_xy);
            if d_launch > 2.5 {
                let wp = if d_launch > 6.5 {
                    map.next_waypoint_sized(
                        [me.pos[0], me.pos[1]],
                        cs.launch_xy,
                        me.pos[2],
                        me.radius(),
                        me.height(),
                        me.step_up(),
                    )
                } else {
                    cs.launch_xy
                };
                let dx = wp[0] - me.pos[0];
                let dy = wp[1] - me.pos[1];
                let d = dx.hypot(dy).max(1e-6);
                move_vec = [dx / d, dy / d];
                let rep = map.wall_repulsion(me.pos[0], me.pos[1], me.pos[2], me.radius() + 0.9, me.height(), me.step_up());
                move_vec[0] += rep[0] * 0.55;
                move_vec[1] += rep[1] * 0.55;
            } else {
                // Gentle micro-strafe while topping off last ~1s of jet energy (never stand dead still!)
                let ang = (cs.edge_pos[1] - me.pos[1]).atan2(cs.edge_pos[0] - me.pos[0]) + state.strafe_sign * std::f64::consts::FRAC_PI_2;
                move_vec = [ang.cos() * 0.4, ang.sin() * 0.4];
                want_boost = false;
            }
        }
    } else if dist_goal_2d > (if obs.zone.next_radius <= 5.0 && obs.zone.radius <= 30.0 { 0.25 } else { 1.8 }) {
        let same_roof = cur_roof.is_some() && map.rooftop_at(goal_3d[0], goal_3d[1], goal_3d[2]) == cur_roof;
        let bail_off_roof = cur_roof.is_some()
            && !same_roof
            && goal_3d[2] < me.pos[2] - 2.0
            && dist_goal_2d <= 28.0
            && map.has_shot_los([me.pos[0], me.pos[1], me.pos[2] + 3.0], [goal_3d[0], goal_3d[1], me.pos[2] + 3.0]);
        let direct_roof_move = same_roof || bail_off_roof;
        let wp = if dist_goal_2d > 6.0 && !direct_roof_move {
            let from2 = [me.pos[0], me.pos[1]];
            let to2 = [goal_3d[0], goal_3d[1]];
            if (t - state.astar_cache_t) < 0.35
                && dist2d(from2, state.astar_cache_from) < 5.0
                && dist2d(to2, state.astar_cache_to) < 8.0
                && dist2d(from2, state.astar_cache_wp) > 2.5
            {
                state.astar_cache_wp
            } else {
                let computed = map.next_waypoint_sized(
                    from2,
                    to2,
                    me.pos[2],
                    me.radius(),
                    me.height(),
                    me.step_up(),
                );
                state.astar_cache_from = from2;
                state.astar_cache_to = to2;
                state.astar_cache_wp = computed;
                state.astar_cache_t = t;
                computed
            }
        } else {
            [goal_3d[0], goal_3d[1]]
        };
        let dx = wp[0] - me.pos[0];
        let dy = wp[1] - me.pos[1];
        let d = dx.hypot(dy).max(1e-6);
        move_vec = [dx / d, dy / d];
        if !direct_roof_move {
            let rep = map.wall_repulsion(me.pos[0], me.pos[1], me.pos[2], me.radius() + 1.1, me.height(), me.step_up());
            move_vec[0] += rep[0] * 0.7;
            move_vec[1] += rep[1] * 0.7;
        }
    } else if cur_roof.is_some() && !zone_urgent && !final_circle && dodge.urgency < 0.2 {
        // Anti-mortar micro-pacing on rooftops: never stand dead still for blind mortars!
        if let Some(r_idx) = cur_roof {
            let pid = map.rooftops[r_idx].prism_id;
            let top = map.prisms[pid].top;
            let ang = t * 1.3;
            let px = me.pos[0] + ang.cos() * 4.0;
            let py = me.pos[1] + ang.sin() * 4.0;
            if map.point_in_prism_2d(pid, px, py)
                && !map.is_cylinder_blocked(px, py, top + 0.05, me.radius() + 0.25, me.height(), 0.5)
            {
                move_vec = [ang.cos() * 0.55, ang.sin() * 0.55];
            }
        }
    }

    if dodge.urgency >= 3.0 && me.in_zone && !zone_urgent && !in_air_hop {
        // Pure full-speed corner duck / mortar blast evasion — no diagonal drag from tactical goal!
        move_vec = dodge.vec;
    } else if dodge.urgency > 0.1 && !in_air_hop {
        let dw = dodge.urgency.clamp(0.5, 3.2);
        move_vec[0] += dodge.vec[0] * dw;
        move_vec[1] += dodge.vec[1] * dw;
    }

    let mlen = move_vec[0].hypot(move_vec[1]);
    // Boost while charging laser when moving (Opus #1 — matches real Expert 9.75 m/s charge-boost!)
    if combat.charging_laser && mlen > 0.1 && me.energy > 22.0 && !stealth_walk {
        want_boost = true;
    }
    if mlen > 1e-6 {
        let climbing_asc = state.climb_state.map(|c| c.ascending).unwrap_or(false);
        let scale = if climbing_asc {
            mlen.min(1.0)
        } else if stealth_walk && dodge.urgency < 0.2 {
            (4.3 / me.walk_speed().max(5.0)).min(0.4)
        } else if final_circle && dist_goal_2d < 3.5 && dodge.urgency < 0.2 && t >= state.stuck_escape_until {
            // Smooth proportional braking onto final-circle pin (prevents bang-bang overshoot at 260 HP/s! Fixes Red-Team P1-1)
            (dist_goal_2d / 3.5).clamp(0.12, 1.0)
        } else if dist_goal_2d < 4.0 && dodge.urgency < 0.2 && state.roof_hop.is_none() && t >= state.stuck_escape_until && !final_circle {
            (dist_goal_2d / 4.0).clamp(0.2, 1.0)
        } else {
            1.0
        };
        move_vec = [(move_vec[0] / mlen) * scale, (move_vec[1] / mlen) * scale];
    }

    if !me.grounded
        && state.climb_state.is_none()
        && state.roof_hop.is_none()
        && !stuck_air_ok
        && dz_goal <= 0.3
    {
        want_boost = false;
    }

    // 11. Smooth 360-deg scan fallback (fast full-360 sweep when hunting Laser!)
    let final_aim = if let Some(a) = combat.aim {
        a
    } else if !have_laser {
        state.scan_yaw = wrap_angle(me.aim[0] + 1.15);
        [state.scan_yaw, -0.05]
    } else if mlen > 0.1 {
        let move_yaw = move_vec[1].atan2(move_vec[0]);
        let cycle = t % 3.2;
        let offset = if cycle > 2.65 {
            std::f64::consts::PI
        } else {
            (t * 1.6).sin() * 1.15
        };
        state.scan_yaw = wrap_angle(move_yaw + offset);
        [state.scan_yaw, -0.04]
    } else {
        state.scan_yaw = wrap_angle(me.aim[0] + 0.65);
        [state.scan_yaw, -0.04]
    };

    state.last_cmd_move = move_vec;
    act.move_dir = Some(move_vec);
    act.boost = Some(want_boost);
    act.jump = Some(want_jump);
    act.aim = Some(final_aim);
    act
}

/// Full Ohio Stadium footprint (bowl + jagged steep bleacher rim!)
#[inline]
fn in_stadium_zone(x: f64, y: f64) -> bool {
    x > -382.0 && x < -174.0 && y > 4.0 && y < 262.0
}

fn find_safe_zone_target(obs: &Observation, map: &ArenaMap, margin_cur: f64) -> [f64; 3] {
    let me = &obs.me;
    let nc = obs.zone.next_center;
    let nr = obs.zone.next_radius;
    let cur_roof = map.rooftop_at(me.pos[0], me.pos[1], me.pos[2]);

    // Only stay on rooftop covering next_center if we are ALREADY standing on it (never wall-ram during zone_urgent!)
    if nr <= 65.0 && me.in_zone && margin_cur > 10.0 {
        if let Some(r_idx) = map.rooftop_covering(nc[0], nc[1]) {
            if cur_roof == Some(r_idx) {
                let roof = &map.rooftops[r_idx];
                let top = map.prisms[roof.prism_id].top;
                if !map.is_cylinder_blocked(nc[0], nc[1], top + 0.05, me.radius() + 0.25, me.height(), 0.5) {
                    return [nc[0], nc[1], top];
                }
                if dist2d([roof.pos[0], roof.pos[1]], obs.zone.center) <= obs.zone.radius - 4.0 {
                    return roof.pos;
                }
            }
        }
    }

    // Ohio Stadium horseshoe escape: when zone is outside the Stadium, exit via the open south mouth (-275, 12)
    // or walk south around west wall p[235] via (-395, 48) — eliminates ALL Stadium zone deaths (m24/m25/m29)!
    if !in_stadium_zone(nc[0], nc[1]) {
        // Inside the Stadium bowl / east bleacher rim
        if me.pos[0] > -358.0 && me.pos[0] < -174.0 && me.pos[1] > 35.0 && me.pos[1] < 258.0 {
            let ex = -275.0;
            let ey = 12.0;
            return [ex, ey, map.terrain_z(ex, ey)];
        }
        // Directly behind west Stadium wall p[235] (x=[-382.2,-352.2], y=[74.3,221.8]) trying to go east
        if me.pos[0] >= -430.0 && me.pos[0] <= -355.0 && me.pos[1] > 65.0 && me.pos[1] < 228.0 && nc[0] > -310.0 {
            let ex = -394.0;
            let ey = if me.pos[1] > 175.0 && nc[1] > 140.0 { 232.0 } else { 46.0 };
            return [ex, ey, map.terrain_z(ex, ey)];
        }
    }

    let dx = nc[0] - me.pos[0];
    let dy = nc[1] - me.pos[1];
    let d = dx.hypot(dy).max(1e-6);
    let target_r = (nr * 0.50).min((nr - 8.0).max(0.0)).max(0.0);
    let desired = if d > target_r {
        [nc[0] - (dx / d) * target_r, nc[1] - (dy / d) * target_r]
    } else {
        nc
    };

    let check_ground = |x: f64, y: f64| -> Option<[f64; 3]> {
        if x < map.min_x + 8.0 || x > map.max_x - 8.0 || y < map.min_y + 8.0 || y > map.max_y - 8.0 {
            return None;
        }
        let tz = map.terrain_z(x, y);
        if map.terrain_slope_deg(x, y) < 44.0
            && !map.is_cylinder_blocked(x, y, tz, me.radius() + 0.4, me.height(), me.step_up())
        {
            return Some([x, y, tz]);
        }
        None
    };

    if let Some(p) = check_ground(desired[0], desired[1]) {
        return p;
    }
    if let Some(p) = check_ground(nc[0], nc[1]) {
        return p;
    }

    let max_r = if nr <= 10.0 { obs.zone.radius.clamp(6.0, 24.0) } else { nr.max(48.0) };
    for ring in 1..=8 {
        let r = (ring as f64 * 6.0).min(max_r);
        for k in 0..12 {
            let ang = (k as f64 / 12.0) * std::f64::consts::TAU;
            let x = nc[0] + ang.cos() * r;
            let y = nc[1] + ang.sin() * r;
            if let Some(p) = check_ground(x, y) {
                return p;
            }
        }
    }

    [nc[0], nc[1], map.terrain_z(nc[0], nc[1])]
}

#[allow(clippy::too_many_arguments)]
fn pick_tactical_position(
    obs: &Observation,
    state: &WorldState,
    map: &ArenaMap,
    enemies: &[EnemySummary],
    primary_id: Option<i64>,
    want_cover_only: bool,
    want_peek: bool,
    cur_roof: Option<usize>,
    avoid_cubes: bool,
    ready_for_rooftops: bool,
    laser_sweep_target: Option<[f64; 3]>,
) -> MoveCandidate {
    let me = &obs.me;
    let my_h = me.height();
    let my_muz_h = me.muzzle_height();
    let my_r = me.radius();
    let on_rooftop = cur_roof.is_some();
    let (z_future_c, z_future_r) = zone_at(&obs.zone, obs.t, obs.t + 14.0);
    let have_light = find_weapon_slot(me, "lightning").is_some();
    let have_laser = find_weapon_slot(me, "laser").is_some();
    let zone_in_stadium = in_stadium_zone(obs.zone.next_center[0], obs.zone.next_center[1]);
    let primary_enemy = primary_id.and_then(|pid| enemies.iter().find(|e| e.id == pid));

    let preferred_dist = if have_laser {
        118.0
    } else {
        76.0
    };

    let mut candidates: Vec<MoveCandidate> = Vec::with_capacity(48);
    candidates.push(MoveCandidate::Pos(me.pos));

    if let Some(r_idx) = cur_roof {
        let roof = &map.rooftops[r_idx];
        let pid = roof.prism_id;
        let p_top = map.prisms[pid].top;
        let need_size_chk = me.cubes >= 5;

        for &ip in &roof.interior_pts {
            if !need_size_chk || !map.is_cylinder_blocked(ip[0], ip[1], p_top + 0.05, my_r + 0.2, my_h, 0.5) {
                candidates.push(MoveCandidate::Pos(ip));
            }
        }
        for ep in &roof.edge_pts {
            if !need_size_chk || !map.is_cylinder_blocked(ep.pos[0], ep.pos[1], p_top + 0.05, my_r + 0.2, my_h, 0.5) {
                candidates.push(MoveCandidate::Pos(ep.pos));
            }
        }

        if let Some(pe) = primary_enemy {
            if let Some(best_ep) = roof.edge_pts.iter().min_by(|a, b| {
                dist2d([a.pos[0], a.pos[1]], [pe.pos[0], pe.pos[1]])
                    .partial_cmp(&dist2d([b.pos[0], b.pos[1]], [pe.pos[0], pe.pos[1]]))
                    .unwrap_or(std::cmp::Ordering::Equal)
            }) {
                let in_x = -best_ep.outward_yaw.cos();
                let in_y = -best_ep.outward_yaw.sin();
                for &in_step in &[-1.0, 2.2, 6.2, 11.5, 17.0] {
                    let rx = best_ep.pos[0] + in_x * in_step;
                    let ry = best_ep.pos[1] + in_y * in_step;
                    if map.point_in_prism_2d(pid, rx, ry)
                        && !map.is_cylinder_blocked(rx, ry, p_top + 0.05, my_r + 0.2, my_h, 0.5)
                    {
                        candidates.push(MoveCandidate::Pos([rx, ry, p_top]));
                    }
                }
            }
        }

        for k in 0..8 {
            let ang = (k as f64 / 8.0) * std::f64::consts::TAU;
            let cx = me.pos[0] + ang.cos() * 8.0;
            let cy = me.pos[1] + ang.sin() * 8.0;
            if map.point_in_prism_2d(pid, cx, cy)
                && !map.is_cylinder_blocked(cx, cy, p_top + 0.05, my_r + 0.2, my_h, 0.5)
            {
                candidates.push(MoveCandidate::Pos([cx, cy, p_top]));
            }
        }

        if obs.t >= state.climb_cooldown_until {
            for &hop in &roof.hops {
                if me.energy >= hop.min_energy + 12.0
                    && dist2d([hop.to_pos[0], hop.to_pos[1]], obs.zone.center) < obs.zone.radius - 6.0
                {
                    candidates.push(MoveCandidate::Hop(hop));
                }
            }
        }

        // Bail-off ground cover candidates whenever cross-fired or seeking cover on an exposed pancake roof!
        let cur_roof_exposed = enemies.iter().filter(|e| e.has_los).count();
        if want_cover_only || cur_roof_exposed >= 2 || me.hp < me.max_hp * 0.58 {
            for ep in &roof.edge_pts {
                let lx = ep.launch_xy[0];
                let ly = ep.launch_xy[1];
                let lz = ep.drop_z;
                if dist2d([lx, ly], obs.zone.center) <= obs.zone.radius - 4.0
                    && !map.is_cylinder_blocked(lx, ly, lz + 0.05, my_r + 0.3, my_h, me.step_up())
                {
                    candidates.push(MoveCandidate::Pos([lx, ly, lz]));
                }
            }
        }
    } else {
        for &(rad, count) in &[(10.0, 10usize), (22.0, 12usize), (40.0, 10usize)] {
            for k in 0..count {
                let ang = (k as f64 / count as f64) * std::f64::consts::TAU;
                let cx = me.pos[0] + ang.cos() * rad;
                let cy = me.pos[1] + ang.sin() * rad;
                if cx < map.min_x + 8.0 || cx > map.max_x - 8.0 || cy < map.min_y + 8.0 || cy > map.max_y - 8.0 {
                    continue;
                }
                let sz = map.support_z(cx, cy, me.pos[2], me.step_up(), my_h);
                let is_ground = (sz - map.terrain_z(cx, cy)).abs() < 0.5;
                if is_ground && map.terrain_slope_deg(cx, cy) > 44.0 {
                    continue;
                }
                if !map.is_cylinder_blocked(cx, cy, sz, my_r + 0.3, my_h, me.step_up()) {
                    candidates.push(MoveCandidate::Pos([cx, cy, sz]));
                }
            }
        }

        // Building-shadow cover candidates up to 115m away whenever multiple enemies or seeking cover!
        if want_cover_only || enemies.len() >= 2 {
            if let Some(nearest_e) = enemies.iter().min_by(|a, b| a.dist.partial_cmp(&b.dist).unwrap_or(std::cmp::Ordering::Equal)) {
                let mut nearby_prisms: Vec<(f64, usize)> = Vec::new();
                for (pid, p) in map.prisms.iter().enumerate() {
                    if p.area < 40.0 || p.top - p.base < 9.0 || p.kind == crate::map::PrismKind::Stadium { continue; }
                    let d = dist2d([me.pos[0], me.pos[1]], p.centroid);
                    if d < 115.0 { nearby_prisms.push((d, pid)); }
                }
                nearby_prisms.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
                for &(_, pid) in nearby_prisms.iter().take(6) {
                    let p = &map.prisms[pid];
                    let dx = p.centroid[0] - nearest_e.center[0];
                    let dy = p.centroid[1] - nearest_e.center[1];
                    let len = dx.hypot(dy).max(1e-6);
                    let half_ext = ((p.max_x - p.min_x).max(p.max_y - p.min_y) * 0.52 + 5.0).min(38.0);
                    let cx = (p.centroid[0] + (dx / len) * half_ext).clamp(map.min_x + 8.0, map.max_x - 8.0);
                    let cy = (p.centroid[1] + (dy / len) * half_ext).clamp(map.min_y + 8.0, map.max_y - 8.0);
                    let tz = map.terrain_z(cx, cy);
                    if map.terrain_slope_deg(cx, cy) < 44.0
                        && !map.is_cylinder_blocked(cx, cy, tz, my_r + 0.4, my_h, me.step_up())
                    {
                        candidates.push(MoveCandidate::Pos([cx, cy, tz]));
                    }
                }
            }
        }

        // Top 4 nearest climbable rooftops when Phase B ready & safe!
        let cur_los_count = enemies.iter().filter(|e| e.has_los).count();
        let min_e_d = enemies.iter().map(|e| e.dist).fold(f64::INFINITY, f64::min);
        let roof_e_min = if !state.is_ffa() && have_laser && me.hp > me.max_hp * 0.52 && me.energy > 50.0 {
            26.0
        } else {
            48.0
        };
        if ready_for_rooftops && me.energy > 42.0 && obs.t >= state.climb_cooldown_until && cur_los_count == 0 && min_e_d >= roof_e_min {
            let max_climb = max_climb_height(me);
            let mut roof_cands: Vec<(f64, [f64; 3])> = Vec::new();
            for (r_idx, roof) in map.rooftops.iter().enumerate() {
                if !is_roof_climbable(map, obs, state, r_idx, cur_roof, false) {
                    continue;
                }
                let mut best_d = f64::INFINITY;
                let mut best_ep_pos = roof.pos;
                for ep in &roof.edge_pts {
                    let launch_ground = map.terrain_z(ep.launch_xy[0], ep.launch_xy[1]);
                    let climb_dh = (ep.pos[2] - ep.drop_z).max(0.0);
                    if !ep.launch_clear
                        || ep.drop_z > launch_ground.max(me.pos[2]) + me.step_up() + 2.0
                        || climb_dh < 3.0
                        || climb_dh > max_climb
                        || dist2d(ep.launch_xy, obs.zone.center) > obs.zone.radius - 4.0
                    {
                        continue;
                    }
                    let d = dist2d([me.pos[0], me.pos[1]], ep.launch_xy);
                    if d < best_d {
                        best_d = d;
                        best_ep_pos = ep.pos;
                    }
                }
                if best_d < 52.0 {
                    roof_cands.push((best_d, best_ep_pos));
                }
            }
            roof_cands.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
            for &(_, rp) in roof_cands.iter().take(4) {
                candidates.push(MoveCandidate::Pos(rp));
            }
        }
    }

    let mut active_enemies: Vec<&EnemySummary> = enemies
        .iter()
        .filter(|e| e.dist <= 165.0 || (e.has_los && e.dist <= 275.0) || Some(e.id) == primary_id)
        .collect();
    active_enemies.sort_by(|a, b| a.dist.partial_cmp(&b.dist).unwrap_or(std::cmp::Ordering::Equal));
    if active_enemies.len() > 5 {
        let primary_ref = primary_id.and_then(|pid| active_enemies.iter().copied().find(|e| e.id == pid));
        active_enemies.truncate(5);
        if let Some(pr) = primary_ref {
            if !active_enemies.iter().any(|e| e.id == pr.id) {
                active_enemies.push(pr);
            }
        }
    }

    let z_pad = (obs.zone.radius * 0.18).min(6.0);
    let any_in_zone = candidates
        .iter()
        .any(|mc| dist2d([mc.dest()[0], mc.dest()[1]], obs.zone.center) <= obs.zone.radius - z_pad);

    let mut best_cand = MoveCandidate::Pos(me.pos);
    let mut best_score = f64::NEG_INFINITY;

    for &mc in &candidates {
        let cand = mc.dest();
        let dc_cur = dist2d([cand[0], cand[1]], obs.zone.center);
        if any_in_zone && dc_cur > obs.zone.radius - z_pad {
            continue;
        }

        let cand_muz = [cand[0], cand[1], cand[2] + my_muz_h];
        let cand_upper = [cand[0], cand[1], cand[2] + my_h * 0.78];
        let mut sc = 0.0;

        let dc_fut = dist2d([cand[0], cand[1]], z_future_c);
        if dc_cur > obs.zone.radius - z_pad {
            sc -= 2000.0 + (dc_cur - obs.zone.radius) * 25.0;
        }
        if z_future_r > 6.0 && dc_fut > z_future_r - 8.0 {
            sc -= (dc_fut - (z_future_r - 8.0)) * (2.5 + obs.zone.dps * 0.04);
        }
        if !zone_in_stadium && in_stadium_zone(cand[0], cand[1]) {
            sc -= 320.0;
        }

        if avoid_cubes {
            for cb in state.cubes.values() {
                if dist2d([cand[0], cand[1]], [cb.pos[0], cb.pos[1]]) < 3.8 {
                    sc -= 40.0;
                    break;
                }
            }
        }

        // ALWAYS penalize standing inside an NPC turret's LOS (fixes 4/48 NPC deaths!)
        {
            let npc_pen = if me.hp < me.max_hp * 0.50 { 165.0 } else { 105.0 };
            for n in state.npcs.values() {
                if dist2d([cand[0], cand[1]], [n.pos[0], n.pos[1]]) < 52.0
                    && map.has_shot_los([n.pos[0], n.pos[1], n.pos[2] + 2.5], cand_upper)
                {
                    sc -= npc_pen;
                    break;
                }
            }
        }

        let mut exposed = 0usize;
        let mut primary_los = false;
        let mut primary_dist = 100.0;
        let mut min_enemy_d = f64::INFINITY;
        let mut charging_los = 0usize;
        let mut enemies_on_my_height = 0usize;

        for e in &active_enemies {
            let d = dist3d(cand_muz, e.center);
            if d < min_enemy_d { min_enemy_d = d; }
            if on_rooftop && (e.pos[2] - me.pos[2]).abs() < 4.5 && d < 38.0 {
                enemies_on_my_height += 1;
            }
            if e.has_lightning && !have_light && d < 52.0 {
                sc -= (52.0 - d) * 11.5;
            }
            if map.has_shot_los(e.muzzle, cand_upper) {
                exposed += 1;
                if e.charging { charging_los += 1; }
            }
            if Some(e.id) == primary_id {
                primary_los = map.has_shot_los(cand_muz, e.center);
                primary_dist = d;
            }
        }

        if want_cover_only {
            if exposed == 0 {
                sc += 265.0;
            } else {
                sc -= (exposed as f64) * 150.0;
            }
            sc -= (charging_los as f64) * 130.0;
            sc += min_enemy_d.min(95.0) * 0.7;
        } else {
            // Strict 1v1 sightline slicing + hard-cover adjacency bonus (0.5s step behind cover! Fable #2)
            if exposed == 1 && primary_los {
                sc += if want_peek { 205.0 } else { 160.0 };
                if let Some(pe) = primary_enemy {
                    // Check if 7m perpendicular step reaches hard cover from primary enemy!
                    let to_e = (pe.pos[1] - cand[1]).atan2(pe.pos[0] - cand[0]);
                    let mut near_hard_cover = false;
                    for &sgn in &[1.0f64, -1.0] {
                        let pa = to_e + sgn * std::f64::consts::FRAC_PI_2;
                        let hx = cand[0] + pa.cos() * 7.0;
                        let hy = cand[1] + pa.sin() * 7.0;
                        if !map.has_shot_los(pe.muzzle, [hx, hy, cand[2] + my_h * 0.78]) {
                            near_hard_cover = true;
                            break;
                        }
                    }
                    if near_hard_cover {
                        sc += 48.0;
                    }
                }
            } else if exposed == 0 {
                sc += if want_peek { 35.0 } else { 110.0 };
            } else {
                sc -= ((exposed - 1) as f64) * 215.0;
            }
            sc -= (charging_los as f64) * 85.0;

            let range_err = (primary_dist - preferred_dist).abs();
            sc -= range_err * 0.45;
            if !have_light && min_enemy_d < 26.0 {
                sc -= (26.0 - min_enemy_d) * 6.5;
            }
        }

        // After grabbing a rooftop Laser, never cling to exposed roofs — drop cleanly back down to ground cover!
        let _ = enemies_on_my_height;
        if on_rooftop && cand[2] < me.pos[2] - 4.0 && exposed <= 1 {
            sc += 55.0;
        }
        if matches!(mc, MoveCandidate::Hop(_)) {
            if enemies_on_my_height >= 1 || want_cover_only {
                sc += 45.0;
            } else {
                sc -= 20.0;
            }
        }

        sc += map.cover_at([cand[0], cand[1]]) as f64 * 42.0;

        let move_d = dist2d([cand[0], cand[1]], [me.pos[0], me.pos[1]]);
        let dz = (cand[2] - me.pos[2]).max(0.0);
        sc -= move_d * 0.28 + dz * 0.70;

        if let Some(prev_g) = state.last_tactical_goal {
            if dist2d([cand[0], cand[1]], [prev_g[0], prev_g[1]]) < 9.0 {
                sc += 18.0;
            }
        }

        if let Some(st) = laser_sweep_target {
            if move_d > 5.0 {
                let sw_yaw = (st[1] - me.pos[1]).atan2(st[0] - me.pos[0]);
                let move_yaw = (cand[1] - me.pos[1]).atan2(cand[0] - me.pos[0]);
                sc += angle_diff(move_yaw, sw_yaw).cos() * 58.0;
            }
        }

        if !want_cover_only && move_d > 5.0 && move_d < 26.0 {
            if let Some(pe) = primary_enemy {
                let to_e_yaw = (pe.pos[1] - me.pos[1]).atan2(pe.pos[0] - me.pos[0]);
                let tan_yaw = to_e_yaw + state.strafe_sign * std::f64::consts::FRAC_PI_2;
                let move_yaw = (cand[1] - me.pos[1]).atan2(cand[0] - me.pos[0]);
                sc += angle_diff(move_yaw, tan_yaw).cos() * 18.0;
            }
        }

        if sc > best_score {
            best_score = sc;
            best_cand = mc;
        }
    }

    best_cand
}

fn pick_macro_hold_spot(
    obs: &Observation,
    state: &WorldState,
    map: &ArenaMap,
    cur_roof: Option<usize>,
    margin_cur: f64,
    ready_for_rooftops: bool,
) -> [f64; 3] {
    let me = &obs.me;
    let nc = obs.zone.next_center;
    let nr = obs.zone.next_radius;
    let zone_in_stadium = in_stadium_zone(nc[0], nc[1]);

    // Phase A (!have_laser): sweep UNVISITED ground sectors inside next_zone to find a Laser!
    // Phase B (have_laser): HOLD a dense building-cover corner inside next_zone instead of patrolling open streets! (Fable #4)
    if !ready_for_rooftops {
        let have_laser = find_weapon_slot(me, "laser").is_some();
        if have_laser {
            let mut best_hold: Option<(f64, [f64; 3])> = None;
            let hold_max_r = (nr * 0.68).clamp(18.0, 155.0);
            let mut eval_spot = |x: f64, y: f64| {
                if !zone_in_stadium && in_stadium_zone(x, y) { return; }
                if dist2d([x, y], obs.zone.center) > obs.zone.radius - 10.0 { return; }
                if nr > 15.0 && dist2d([x, y], nc) > (nr - 8.0).max(12.0) { return; }
                let tz = map.terrain_z(x, y);
                if map.terrain_slope_deg(x, y) >= 44.0
                    || map.is_cylinder_blocked(x, y, tz, me.radius() + 0.5, me.height(), me.step_up())
                {
                    return;
                }
                let d_me = dist2d([me.pos[0], me.pos[1]], [x, y]);
                let cov = map.cover_at([x, y]) as f64;
                let mut sc = cov * 115.0 - d_me * 0.35;
                if d_me < 8.0 && cov >= 0.25 {
                    sc += 24.0; // Hysteresis: hold our dense cover corner instead of jittering!
                }
                if state.is_ffa() || me.hp < me.max_hp * 0.78 {
                    for s in &state.sounds {
                        if obs.t - s.t < 2.2 && dist2d([x, y], s.est_pos) < 85.0 {
                            sc -= 65.0;
                        }
                    }
                }
                if best_hold.map(|(bs, _)| sc > bs).unwrap_or(true) {
                    best_hold = Some((sc, [x, y, tz]));
                }
            };
            // Always evaluate current position + local dense building corners + inner next_zone rings
            eval_spot(me.pos[0], me.pos[1]);
            for &(loc_r, cnt) in &[(16.0, 8usize), (34.0, 8usize)] {
                for k in 0..cnt {
                    let ang = (k as f64 / cnt as f64) * std::f64::consts::TAU;
                    let x = (me.pos[0] + ang.cos() * loc_r).clamp(map.min_x + 12.0, map.max_x - 12.0);
                    let y = (me.pos[1] + ang.sin() * loc_r).clamp(map.min_y + 12.0, map.max_y - 12.0);
                    eval_spot(x, y);
                }
            }
            for ring in 0..2 {
                let r_f = if ring == 0 { 0.38 } else { 0.78 };
                let hr = (hold_max_r * r_f).max(16.0);
                for step in 0..8 {
                    let ang = (step as f64 / 8.0) * std::f64::consts::TAU + (ring as f64) * 0.39;
                    let x = (nc[0] + ang.cos() * hr).clamp(map.min_x + 12.0, map.max_x - 12.0);
                    let y = (nc[1] + ang.sin() * hr).clamp(map.min_y + 12.0, map.max_y - 12.0);
                    eval_spot(x, y);
                }
            }
            if let Some((_, pos)) = best_hold {
                return pos;
            }
            return find_safe_zone_target(obs, map, margin_cur);
        }

        let mut best_sw: Option<(f64, [f64; 3])> = None;
        let max_r = (nr * 0.72).clamp(30.0, 210.0);
        for ring in 0..2 {
            let r_f = if ring == 0 { 0.45 } else { 0.85 };
            let sweep_r = (max_r * r_f).max(24.0);
            for step in 0..8 {
                let ang = (step as f64 / 8.0) * std::f64::consts::TAU + (ring as f64) * 0.39;
                let x = (nc[0] + ang.cos() * sweep_r).clamp(map.min_x + 12.0, map.max_x - 12.0);
                let y = (nc[1] + ang.sin() * sweep_r).clamp(map.min_y + 12.0, map.max_y - 12.0);
                if !zone_in_stadium && in_stadium_zone(x, y) { continue; }
                if dist2d([x, y], obs.zone.center) > obs.zone.radius - 10.0 { continue; }
                let tz = map.terrain_z(x, y);
                if map.terrain_slope_deg(x, y) >= 44.0
                    || map.is_cylinder_blocked(x, y, tz, me.radius() + 0.5, me.height(), me.step_up())
                {
                    continue;
                }
                let d_me = dist2d([me.pos[0], me.pos[1]], [x, y]);
                if d_me < 25.0 { continue; }
                let stale = (obs.t - state.sector_visited_t(x, y)).min(120.0);
                let cov = map.cover_at([x, y]) as f64;
                let mut sc = stale * 2.2 + cov * 26.0 - (d_me - 95.0).abs() * 0.45;
                if state.is_ffa() || !state.is_practice() {
                    for s in &state.sounds {
                        if obs.t - s.t < 2.2 && dist2d([x, y], s.est_pos) < 85.0 {
                            sc -= 65.0;
                        }
                    }
                }
                if best_sw.map(|(bs, _)| sc > bs).unwrap_or(true) {
                    best_sw = Some((sc, [x, y, tz]));
                }
            }
        }
        if let Some((_, pos)) = best_sw {
            return pos;
        }
        return find_safe_zone_target(obs, map, margin_cur);
    }

    if let Some(r_idx) = map.rooftop_covering(nc[0], nc[1]) {
        if is_roof_climbable(map, obs, state, r_idx, cur_roof, false) {
            let roof = &map.rooftops[r_idx];
            let top = map.prisms[roof.prism_id].top;
            if !map.is_cylinder_blocked(nc[0], nc[1], top + 0.05, me.radius() + 0.25, me.height(), 0.5) {
                return [nc[0], nc[1], top];
            }
            if dist2d([roof.pos[0], roof.pos[1]], obs.zone.center) <= obs.zone.radius - 4.0 {
                return roof.pos;
            }
        }
    }

    let mut best_spot = find_safe_zone_target(obs, map, margin_cur);
    let mut best_score = f64::NEG_INFINITY;

    for (r_idx, roof) in map.rooftops.iter().enumerate() {
        if !is_roof_climbable(map, obs, state, r_idx, cur_roof, false) {
            continue;
        }
        if dist2d([roof.pos[0], roof.pos[1]], obs.zone.center) > obs.zone.radius - 4.0 {
            continue;
        }
        let d_nc = dist2d([roof.pos[0], roof.pos[1]], nc);
        if d_nc > (nr - 10.0).max(12.0) {
            continue;
        }
        let d_me = dist2d([roof.pos[0], roof.pos[1]], [me.pos[0], me.pos[1]]);
        let mut sc = roof.height_above_ground.min(40.0) * 2.4
            + roof.area.sqrt().min(32.0) * 1.2
            - d_nc * 0.45
            - d_me * 0.28;
        if roof.has_penthouse_cover { sc += 35.0; }
        if cur_roof == Some(r_idx) { sc += 40.0; }
        if sc > best_score {
            best_score = sc;
            best_spot = roof.pos;
        }
    }

    if best_score == f64::NEG_INFINITY {
        let r_sample = (nr * 0.6).min(60.0);
        for k in 0..8 {
            let ang = (k as f64 / 8.0) * std::f64::consts::TAU;
            let x = nc[0] + ang.cos() * r_sample;
            let y = nc[1] + ang.sin() * r_sample;
            if !zone_in_stadium && in_stadium_zone(x, y) { continue; }
            let tz = map.terrain_z(x, y);
            if map.terrain_slope_deg(x, y) > 44.0
                || map.is_cylinder_blocked(x, y, tz, me.radius() + 0.5, me.height(), me.step_up())
            {
                continue;
            }
            let d_me = dist2d([x, y], [me.pos[0], me.pos[1]]);
            let cov = map.cover_at([x, y]) as f64;
            let sc = cov * 45.0 - d_me * 0.2;
            if sc > best_score {
                best_score = sc;
                best_spot = [x, y, tz];
            }
        }
    }

    best_spot
}
