#![allow(dead_code)]
use crate::map::ArenaMap;
use crate::protocol::*;
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MatchMode {
    Unknown,
    Practice1v15,
    Ffa,
}

#[derive(Debug, Clone, Copy)]
pub enum LaserPeekState {
    Idle,
    ChargeInCover {
        target_id: i64,
        cover_pos: [f64; 3],
        peek_pos: [f64; 3],
        d_edge: f64,
        started_t: f64,
    },
    PeekOut {
        target_id: i64,
        cover_pos: [f64; 3],
        peek_pos: [f64; 3],
        started_t: f64,
    },
    SnapBack {
        cover_pos: [f64; 3],
        until_t: f64,
    },
}

#[derive(Debug, Clone, Copy)]
pub struct RoofHopState {
    pub from_pos: [f64; 3],
    pub to_pos: [f64; 3],
    pub to_roof_idx: usize,
    pub min_energy: f64,
    pub phase: u8, // 0 = sprint to edge, 1 = jump+jet leap, 2 = settle
    pub started_t: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct ClimbState {
    pub roof_idx: usize,
    pub launch_xy: [f64; 2],
    pub edge_pos: [f64; 3],
    pub goal_pos: [f64; 3],
    pub ascending: bool,
    pub started_t: f64,
}

#[derive(Debug, Clone)]
pub struct EnemyTrack {
    pub id: i64,
    pub pos: [f64; 3],
    pub vel: [f64; 3],
    pub accel: [f64; 3],
    pub aim: [f64; 2],
    pub hp: f64,
    pub max_hp: f64,
    pub cubes: i64,
    pub weapons: Vec<String>,
    pub charging: bool,
    pub charge_start_t: Option<f64>,
    pub last_seen_t: f64,
    pub visible_now: bool,
    pub damaged_by_us: f64,
    pub dmg_dealt_to_us: f64,
    pub last_hit_us_t: f64,
    pub last_damaged_by_us_t: f64,
    pub prev_hp: f64,
    // v3.0 ballistics & awareness tracking
    pub vel_hist: [[f64; 2]; 6],
    pub vel_hist_len: usize,
    pub vel_hist_idx: usize,
    pub strafe_erratic: f64,
    pub last_direct_vision_t: f64,
    pub last_saw_us_t: f64,
    pub source_is_beam: bool,
    pub source_is_guess: bool,
    pub laser_ready_t: f64,
    pub external_dmg_t: f64,
}

impl EnemyTrack {
    pub fn has_weapon(&self, w: &str) -> bool {
        self.weapons.iter().any(|x| x == w)
    }
    pub fn scale(&self, size_per_cube: f64) -> f64 {
        1.0 + size_per_cube * (self.cubes.max(0) as f64)
    }
    pub fn radius(&self, base_r: f64, size_per_cube: f64) -> f64 {
        base_r * self.scale(size_per_cube)
    }
    pub fn height(&self, base_h: f64, size_per_cube: f64) -> f64 {
        base_h * self.scale(size_per_cube)
    }
    pub fn center(&self, base_h: f64, size_per_cube: f64) -> [f64; 3] {
        [self.pos[0], self.pos[1], self.pos[2] + 0.5 * self.height(base_h, size_per_cube)]
    }
    pub fn muzzle(&self, base_muzzle: f64, size_per_cube: f64) -> [f64; 3] {
        [self.pos[0], self.pos[1], self.pos[2] + base_muzzle * self.scale(size_per_cube)]
    }
    pub fn eye(&self, base_eye: f64, size_per_cube: f64) -> [f64; 3] {
        [self.pos[0], self.pos[1], self.pos[2] + base_eye * self.scale(size_per_cube)]
    }
    pub fn charge_elapsed(&self, now: f64) -> f64 {
        if self.charging {
            self.charge_start_t.map(|s| (now - s).max(0.0)).unwrap_or(0.0)
        } else {
            0.0
        }
    }
    pub fn push_vel_sample(&mut self, vxy: [f64; 2], my_pos: [f64; 3]) {
        self.vel_hist[self.vel_hist_idx] = vxy;
        self.vel_hist_idx = (self.vel_hist_idx + 1) % self.vel_hist.len();
        if self.vel_hist_len < self.vel_hist.len() {
            self.vel_hist_len += 1;
        }
        if self.vel_hist_len < 3 {
            self.strafe_erratic = 0.0;
            return;
        }
        let dx = self.pos[0] - my_pos[0];
        let dy = self.pos[1] - my_pos[1];
        let d = dx.hypot(dy).max(1e-6);
        let lat_x = -dy / d;
        let lat_y = dx / d;

        let n = self.vel_hist_len;
        let start = if n < self.vel_hist.len() { 0 } else { self.vel_hist_idx };
        let mut prev_lat: f64 = 0.0;
        let mut flips = 0.0;
        let mut jitter = 0.0;
        for k in 0..n {
            let v = self.vel_hist[(start + k) % self.vel_hist.len()];
            let lat = v[0] * lat_x + v[1] * lat_y;
            if k > 0 {
                if prev_lat.abs() > 2.0 && lat.abs() > 2.0 && (prev_lat * lat < 0.0) {
                    flips += 1.0;
                }
                jitter += (lat - prev_lat).abs();
            }
            prev_lat = lat;
        }
        let avg_jitter = jitter / ((n - 1) as f64);
        let raw = (flips * 0.45 + (avg_jitter / 14.0)).clamp(0.0, 1.0);
        self.strafe_erratic = 0.65 * self.strafe_erratic + 0.35 * raw;
    }
}

#[derive(Debug, Clone)]
pub struct SoundContact {
    pub kind: String,
    pub est_pos: [f64; 2],
    pub bearing: f64,
    pub dist_est: f64,
    pub t: f64,
}

#[derive(Debug, Clone)]
pub struct KnownPickup {
    pub id: i64,
    pub kind: String,
    pub pos: [f64; 3],
    pub last_seen_t: f64,
}

#[derive(Debug, Clone)]
pub struct KnownCrate {
    pub id: i64,
    pub pos: [f64; 3],
    pub hp: f64,
    pub last_seen_t: f64,
}

#[derive(Debug, Clone)]
pub struct KnownCube {
    pub id: i64,
    pub pos: [f64; 3],
    pub last_seen_t: f64,
}

pub struct WorldState {
    pub config: Config,
    pub my_slot: i64,
    pub our_kills: i64,
    pub initial_alive: i64,
    pub prev_non_self_deaths: i64,
    pub enemies: HashMap<i64, EnemyTrack>,
    pub npcs: HashMap<i64, ActorObs>,
    pub sounds: Vec<SoundContact>,
    pub pickups: HashMap<i64, KnownPickup>,
    pub crates: HashMap<i64, KnownCrate>,
    pub cubes: HashMap<i64, KnownCube>,
    // Combat / survival state
    pub last_combat_t: f64,
    pub last_damaged_t: f64, // non-zone attacker only!
    pub last_damaged_bearing: Option<f64>,
    pub last_damaged_by: Option<i64>,
    pub last_damaged_rear: bool,
    pub laser_shorted_until: f64,
    pub last_enemy_los_t: f64,
    pub my_laser_charge_start_t: Option<f64>,
    pub squad_last_saw_us_t: f64,
    pub healing_mode: bool,
    // v3.0 Laser Peek & Blind Probe state
    pub laser_peek: LaserPeekState,
    pub last_probe_t: f64,
    // Mode detection (Practice 1v15 vs Final FFA)
    pub ffa_confirmed: bool,
    pub ffa_evidence: f64,
    pub practice_evidence: f64,
    pub mode: MatchMode,
    pub last_kill_drop_pos: Option<([f64; 3], f64, bool)>,
    pub visited_sectors: [f64; 192],
    // Movement / stuck tracking
    pub prev_pos: [f64; 3],
    pub prev_t: f64,
    pub prev_tick: i64,
    pub last_cmd_move: [f64; 2],
    pub stuck_time: f64,
    pub stuck_escape_until: f64,
    pub stuck_escape_dir: [f64; 2],
    pub stuck_streak: u32,
    pub last_stuck_pos: [f64; 2],
    pub zone_boost_active: bool,
    // Rooftop climb, roof-hop & tactical goal hysteresis
    pub climb_state: Option<ClimbState>,
    pub roof_hop: Option<RoofHopState>,
    pub climb_cooldown_until: f64,
    pub last_tactical_goal: Option<[f64; 3]>,
    // Cached A* waypoint (eliminates per-tick heap churn & keeps native max_ms < 1.5ms!)
    pub astar_cache_from: [f64; 2],
    pub astar_cache_to: [f64; 2],
    pub astar_cache_wp: [f64; 2],
    pub astar_cache_t: f64,
    // Strafe & awareness scan
    pub strafe_sign: f64,
    pub next_strafe_flip_t: f64,
    pub scan_yaw: f64,
    pub rng_state: u64,
}

impl WorldState {
    pub fn new(hello: &Hello) -> Self {
        let seed = (hello.seed as u64)
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        WorldState {
            config: hello.config.clone(),
            my_slot: hello.slot,
            our_kills: 0,
            initial_alive: 16,
            prev_non_self_deaths: 0,
            enemies: HashMap::new(),
            npcs: HashMap::new(),
            sounds: Vec::new(),
            pickups: HashMap::new(),
            crates: HashMap::new(),
            cubes: HashMap::new(),
            last_combat_t: -100.0,
            last_damaged_t: -100.0,
            last_damaged_bearing: None,
            last_damaged_by: None,
            last_damaged_rear: false,
            laser_shorted_until: -10.0,
            last_enemy_los_t: -100.0,
            my_laser_charge_start_t: None,
            squad_last_saw_us_t: -100.0,
            healing_mode: false,
            laser_peek: LaserPeekState::Idle,
            last_probe_t: -100.0,
            ffa_confirmed: false,
            ffa_evidence: 0.0,
            practice_evidence: 0.0,
            mode: MatchMode::Unknown,
            last_kill_drop_pos: None,
            visited_sectors: [-1000.0; 192],
            prev_pos: [0.0; 3],
            prev_t: -1.0,
            prev_tick: -10,
            last_cmd_move: [0.0, 0.0],
            stuck_time: 0.0,
            stuck_escape_until: -10.0,
            stuck_escape_dir: [1.0, 0.0],
            stuck_streak: 0,
            last_stuck_pos: [1e9, 1e9],
            zone_boost_active: false,
            climb_state: None,
            roof_hop: None,
            climb_cooldown_until: -10.0,
            last_tactical_goal: None,
            astar_cache_from: [0.0, 0.0],
            astar_cache_to: [0.0, 0.0],
            astar_cache_wp: [0.0, 0.0],
            astar_cache_t: -10.0,
            strafe_sign: 1.0,
            next_strafe_flip_t: 1.5,
            scan_yaw: 0.0,
            rng_state: if seed == 0 { 0x9E3779B97F4A7C15 } else { seed },
        }
    }

    pub fn next_rand(&mut self) -> f64 {
        let mut x = self.rng_state;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.rng_state = x;
        (x >> 11) as f64 * (1.0 / ((1u64 << 53) as f64))
    }

    pub fn mark_visited(&mut self, x: f64, y: f64, t: f64) {
        let gx = (((x + 440.0) / 55.0).floor() as isize).clamp(0, 15) as usize;
        let gy = (((y + 340.0) / 56.67).floor() as isize).clamp(0, 11) as usize;
        self.visited_sectors[gy * 16 + gx] = t;
    }

    pub fn sector_visited_t(&self, x: f64, y: f64) -> f64 {
        let gx = (((x + 440.0) / 55.0).floor() as isize).clamp(0, 15) as usize;
        let gy = (((y + 340.0) / 56.67).floor() as isize).clamp(0, 11) as usize;
        self.visited_sectors[gy * 16 + gx]
    }

    pub fn update(&mut self, obs: &Observation, map: &ArenaMap) {
        let t = obs.t;
        self.mark_visited(obs.me.pos[0], obs.me.pos[1], t);
        let dt = if self.prev_t >= 0.0 { (t - self.prev_t).clamp(0.05, 1.0) } else { 0.1 };
        if self.prev_t < 0.0 {
            self.initial_alive = obs.alive.max(1);
            self.prev_pos = obs.me.pos;
            self.scan_yaw = obs.me.aim[0];
        }

        // Track our own laser charging start time
        let cur_laser_chg = obs
            .me
            .weapons
            .iter()
            .flatten()
            .find(|w| w.kind == "laser")
            .and_then(|w| w.charge)
            .unwrap_or(0.0);
        if cur_laser_chg > 0.0 {
            if self.my_laser_charge_start_t.is_none() {
                self.my_laser_charge_start_t = Some(t - cur_laser_chg);
            }
        } else {
            self.my_laser_charge_start_t = None;
        }

        // 1. Process events
        let mut hit_by_us_ids: Vec<(i64, f64)> = Vec::new();
        let mut unseen_dmg_mech: Option<(i64, f64)> = None;
        for ev in &obs.events {
            match ev.kind.as_str() {
                "damaged" => {
                    self.last_combat_t = t;
                    if let Some(b) = ev.bearing {
                        self.last_damaged_t = t;
                        self.last_damaged_bearing = Some(b);
                        self.last_damaged_rear = ev.rear.unwrap_or(false);
                        if let Some(by) = ev.by {
                            self.last_damaged_by = Some(by);
                            if (0..100).contains(&by) {
                                if let Some(tr) = self.enemies.get_mut(&by) {
                                    tr.dmg_dealt_to_us += ev.amount.unwrap_or(0.0);
                                    tr.last_hit_us_t = t;
                                } else {
                                    unseen_dmg_mech = Some((by, b));
                                }
                            }
                        }
                    }
                }
                "hit" => {
                    self.last_combat_t = t;
                    if let Some(tid) = ev.target {
                        let amt = ev.amount.unwrap_or(0.0);
                        hit_by_us_ids.push((tid, amt));
                        if (0..100).contains(&tid) {
                            if let Some(tr) = self.enemies.get_mut(&tid) {
                                tr.last_damaged_by_us_t = t;
                                tr.damaged_by_us += amt;
                            }
                        }
                    }
                }
                "effect" => {
                    if ev.effect.as_deref() == Some("short") {
                        let short_s = self.config.weapon_f("lightning", "short_s", 2.0);
                        self.laser_shorted_until = t + short_s;
                    }
                }
                "kill" => {
                    if ev.target.map(|tid| (0..100).contains(&tid)).unwrap_or(true) {
                        self.our_kills += 1;
                    }
                    if let Some(tid) = ev.target {
                        if let Some(dead) = self.enemies.remove(&tid) {
                            let hl = dead.has_weapon("laser");
                            let keep = self.last_kill_drop_pos.map(|(_, kt, ol)| ol && !hl && t - kt < 45.0).unwrap_or(false);
                            if !keep { self.last_kill_drop_pos = Some((dead.pos, t, hl)); }
                        }
                        if self.last_damaged_by == Some(tid) {
                            self.last_damaged_by = None;
                            self.last_damaged_bearing = None;
                            self.last_damaged_t = -100.0;
                        }
                    }
                }
                "took" => {
                    if let Some(pid) = ev.id.as_ref().and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)).or_else(|| v.as_f64().map(|f| f.round() as i64))) {
                        self.pickups.remove(&pid);
                    }
                }
                "take_failed" => {
                    if ev.reason.as_deref() == Some("gone") {
                        if let Some(pid) = ev.id.as_ref().and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)).or_else(|| v.as_f64().map(|f| f.round() as i64))) {
                            self.pickups.remove(&pid);
                        }
                    }
                }
                _ => {}
            }
        }
        if !obs.me.in_zone {
            self.last_combat_t = t;
        }

        // 2. Refresh NPCs first
        self.npcs.clear();
        for a in &obs.mechs {
            if a.id != obs.me.id && a.is_npc() {
                self.npcs.insert(a.id, a.clone());
            }
        }

        for tr in self.enemies.values_mut() {
            tr.visible_now = false;
        }

        let base_h = self.config.mech_f("height", 9.0);
        let base_eye = self.config.mech_f("eye_height", 8.0);
        let base_muz = self.config.mech_f("muzzle_height", 7.0);
        let base_r = self.config.mech_f("radius", 2.5);
        let spc = self.config.cubes.size_per_cube;
        let my_eye = obs.me.eye_pos();

        for a in &obs.mechs {
            if a.id == obs.me.id || !a.is_mech() {
                continue;
            }
            let hp = a.hp.unwrap_or(1400.0);
            let max_hp = a.max_hp.unwrap_or(1400.0);
            let cubes = a.cubes.unwrap_or(0);
            let near_any_npc = self
                .npcs
                .values()
                .any(|n| dist2d([a.pos[0], a.pos[1]], [n.pos[0], n.pos[1]]) < 95.0);

            let sc = 1.0 + spc * (cubes.max(0) as f64);
            let eh = base_h * sc;
            let e_eye = [a.pos[0], a.pos[1], a.pos[2] + base_eye * sc];
            let d_to_us = dist3d(e_eye, obs.me.center_pos());
            let yaw_e_to_us = (obs.me.pos[1] - a.pos[1]).atan2(obs.me.pos[0] - a.pos[0]);
            // Lightweight enemy-sees-us check (solid LOS only; skip double tree raycast every tick for <2ms budget!)
            let enemy_sees_us = d_to_us < 142.0
                && (d_to_us < 38.0 || angle_diff(a.aim[0], yaw_e_to_us).abs() < 1.08)
                && map.has_shot_los(e_eye, obs.me.center_pos());
            if enemy_sees_us {
                self.squad_last_saw_us_t = t;
            }
            let direct_vis = map.has_shot_los(my_eye, [a.pos[0], a.pos[1], a.pos[2] + 0.5 * eh]);

            let us_dmg: f64 = hit_by_us_ids
                .iter()
                .filter(|(id, _)| *id == a.id)
                .map(|(_, amt)| *amt)
                .sum();

            if let Some(tr) = self.enemies.get_mut(&a.id) {
                let gap = (t - tr.last_seen_t).max(0.05);
                if gap <= 0.35 && !tr.source_is_beam && !tr.source_is_guess {
                    let dv = [
                        (a.vel[0] - tr.vel[0]) / gap,
                        (a.vel[1] - tr.vel[1]) / gap,
                        (a.vel[2] - tr.vel[2]) / gap,
                    ];
                    tr.accel = [
                        0.5 * tr.accel[0] + 0.5 * dv[0].clamp(-60.0, 60.0),
                        0.5 * tr.accel[1] + 0.5 * dv[1].clamp(-60.0, 60.0),
                        0.5 * tr.accel[2] + 0.5 * dv[2].clamp(-60.0, 60.0),
                    ];
                } else {
                    tr.accel = [0.0; 3];
                }
                let hp_drop = tr.hp - hp;
                let in_z = dist2d([a.pos[0], a.pos[1]], obs.zone.center) < obs.zone.radius - 8.0;
                // Require consecutive obs tick (no skipped hit events!) + tight gap <= 0.25s
                let no_skipped_obs = self.prev_tick >= 0 && (obs.tick - self.prev_tick) <= 2;
                if no_skipped_obs
                    && gap <= 0.25
                    && hp_drop > us_dmg + 40.0
                    && in_z
                    && !near_any_npc
                    && !tr.source_is_beam
                    && !tr.source_is_guess
                {
                    tr.external_dmg_t = t;
                    // > 175 HP burst inside zone not from us and not from a 25-dmg NPC = confirmed mech-vs-mech FFA!
                    if hp_drop > us_dmg + 175.0 {
                        self.ffa_confirmed = true;
                    }
                }
                tr.prev_hp = tr.hp;
                tr.pos = a.pos;
                tr.vel = a.vel;
                tr.aim = a.aim;
                tr.hp = hp;
                tr.max_hp = max_hp;
                tr.cubes = cubes;
                tr.weapons = a.weapons.clone();
                if a.charging {
                    if !tr.charging {
                        tr.charge_start_t = Some(t);
                    }
                } else {
                    tr.charge_start_t = None;
                }
                tr.charging = a.charging;
                tr.last_seen_t = t;
                tr.visible_now = true;
                tr.source_is_beam = false;
                tr.source_is_guess = false;
                if direct_vis {
                    tr.last_direct_vision_t = t;
                }
                if enemy_sees_us {
                    tr.last_saw_us_t = t;
                }
                if us_dmg > 0.0 {
                    tr.last_damaged_by_us_t = t;
                }
                tr.push_vel_sample([a.vel[0], a.vel[1]], obs.me.pos);
            } else {
                let mut tr = EnemyTrack {
                    id: a.id,
                    pos: a.pos,
                    vel: a.vel,
                    accel: [0.0; 3],
                    aim: a.aim,
                    hp,
                    max_hp,
                    cubes,
                    weapons: a.weapons.clone(),
                    charging: a.charging,
                    charge_start_t: if a.charging { Some(t) } else { None },
                    last_seen_t: t,
                    visible_now: true,
                    damaged_by_us: us_dmg,
                    dmg_dealt_to_us: 0.0,
                    last_hit_us_t: -100.0,
                    last_damaged_by_us_t: if us_dmg > 0.0 { t } else { -100.0 },
                    prev_hp: hp,
                    vel_hist: [[0.0; 2]; 6],
                    vel_hist_len: 0,
                    vel_hist_idx: 0,
                    strafe_erratic: 0.0,
                    last_direct_vision_t: if direct_vis { t } else { -100.0 },
                    last_saw_us_t: if enemy_sees_us { t } else { -100.0 },
                    source_is_beam: false,
                    source_is_guess: false,
                    laser_ready_t: -10.0,
                    external_dmg_t: -100.0,
                };
                tr.push_vel_sample([a.vel[0], a.vel[1]], obs.me.pos);
                self.enemies.insert(a.id, tr);
            }
        }

        // 2b. Ingest obs.beams: update enemy laser cooldown for ALL shooters + exact path[0] muzzle for UNSEEN shooters!
        let enemy_laser_cd = self.config.weapon_f("laser", "cooldown", 1.65);
        for b in &obs.beams {
            if b.by == obs.me.id || !(0..100).contains(&b.by) || b.path.len() < 2 {
                continue;
            }
            let fired_t = (t - b.age).max(0.0);
            if b.kind == "laser" {
                if let Some(tr) = self.enemies.get_mut(&b.by) {
                    tr.laser_ready_t = tr.laser_ready_t.max(fired_t + enemy_laser_cd);
                    tr.charging = false;
                    tr.charge_start_t = None;
                    if !tr.has_weapon("laser") {
                        tr.weapons.push("laser".to_string());
                    }
                }
            }
            let already_vis = self.enemies.get(&b.by).map(|e| e.visible_now).unwrap_or(false);
            if already_vis {
                continue;
            }
            let p0 = b.path[0];
            let p1 = b.path[1];
            let dx = p1[0] - p0[0];
            let dy = p1[1] - p0[1];
            let dz = p1[2] - p0[2];
            let len = (dx * dx + dy * dy + dz * dz).sqrt().max(1e-6);
            let ux = dx / len;
            let uy = dy / len;
            let uz = dz / len;
            // Use known cube scale if enemy was previously tracked (P2-5)
            let prev_sc = self.enemies.get(&b.by).map(|e| e.scale(spc)).unwrap_or(1.0);
            let back_off = base_r * prev_sc + 0.5;
            let est_mx = p0[0] - ux * back_off;
            let est_my = p0[1] - uy * back_off;
            let est_mz = p0[2] - uz * back_off;
            let tz = map.terrain_z(est_mx, est_my);
            let est_feet_z = (est_mz - base_muz * prev_sc).max(tz);
            let beam_aim = [uy.atan2(ux), uz.atan2(ux.hypot(uy).max(1e-6))];
            let seen_t = (t - b.age).max(0.0);

            if let Some(tr) = self.enemies.get_mut(&b.by) {
                tr.pos = [est_mx, est_my, est_feet_z];
                tr.vel = [0.0; 3];
                tr.accel = [0.0; 3];
                tr.aim = beam_aim;
                tr.last_seen_t = seen_t;
                tr.source_is_beam = true;
                tr.source_is_guess = false;
                if b.kind == "laser" {
                    tr.charging = false;
                    tr.charge_start_t = None;
                }
                if !tr.has_weapon(&b.kind) {
                    tr.weapons.push(b.kind.clone());
                }
            } else {
                self.enemies.insert(
                    b.by,
                    EnemyTrack {
                        id: b.by,
                        pos: [est_mx, est_my, est_feet_z],
                        vel: [0.0; 3],
                        accel: [0.0; 3],
                        aim: beam_aim,
                        hp: 1400.0,
                        max_hp: 1400.0,
                        cubes: 0,
                        weapons: vec![b.kind.clone()],
                        charging: false,
                        charge_start_t: None,
                        last_seen_t: seen_t,
                        visible_now: false,
                        damaged_by_us: 0.0,
                        dmg_dealt_to_us: 0.0,
                        last_hit_us_t: -100.0,
                        last_damaged_by_us_t: -100.0,
                        prev_hp: 1400.0,
                        vel_hist: [[0.0; 2]; 6],
                        vel_hist_len: 0,
                        vel_hist_idx: 0,
                        strafe_erratic: 0.0,
                        last_direct_vision_t: -100.0,
                        last_saw_us_t: seen_t,
                        source_is_beam: true,
                        source_is_guess: false,
                        laser_ready_t: if b.kind == "laser" { fired_t + enemy_laser_cd } else { -10.0 },
                        external_dmg_t: -100.0,
                    },
                );
            }
        }

        // 2c. Rough bearing track for unseen projectile attackers (marked source_is_guess = true so we NEVER blind-laser it!)
        if let Some((by, bear)) = unseen_dmg_mech {
            if !self.enemies.contains_key(&by) {
                let mut est_dist = 72.0;
                for p in &obs.projectiles {
                    let b_p = (p.pos[1] - obs.me.pos[1]).atan2(p.pos[0] - obs.me.pos[0]);
                    if angle_diff(b_p, bear).abs() < 0.35 {
                        let dp = dist2d([p.pos[0], p.pos[1]], [obs.me.pos[0], obs.me.pos[1]]);
                        if dp > 12.0 {
                            est_dist = (dp + 22.0).clamp(25.0, 140.0);
                            break;
                        }
                    }
                }
                let ex = (obs.me.pos[0] + bear.cos() * est_dist).clamp(map.min_x + 6.0, map.max_x - 6.0);
                let ey = (obs.me.pos[1] + bear.sin() * est_dist).clamp(map.min_y + 6.0, map.max_y - 6.0);
                let ez = map.terrain_z(ex, ey);
                self.enemies.insert(
                    by,
                    EnemyTrack {
                        id: by,
                        pos: [ex, ey, ez],
                        vel: [0.0; 3],
                        accel: [0.0; 3],
                        aim: [wrap_angle(bear + std::f64::consts::PI), 0.0],
                        hp: 1400.0,
                        max_hp: 1400.0,
                        cubes: 0,
                        weapons: vec!["gun".to_string()],
                        charging: false,
                        charge_start_t: None,
                        last_seen_t: t,
                        visible_now: false,
                        damaged_by_us: 0.0,
                        dmg_dealt_to_us: 40.0,
                        last_hit_us_t: t,
                        last_damaged_by_us_t: -100.0,
                        prev_hp: 1400.0,
                        vel_hist: [[0.0; 2]; 6],
                        vel_hist_len: 0,
                        vel_hist_idx: 0,
                        strafe_erratic: 0.0,
                        last_direct_vision_t: -100.0,
                        last_saw_us_t: t,
                        source_is_beam: false,
                        source_is_guess: true,
                        laser_ready_t: -10.0,
                        external_dmg_t: -100.0,
                    },
                );
            }
        }

        self.enemies.retain(|_, tr| t - tr.last_seen_t <= 12.0);

        // 3. Rock-Solid Death & Unattributed-Burst Mode Detection (ZERO false Practice->FFA flips from house bots shooting crates/NPCs!)
        let non_self_deaths = ((self.initial_alive - obs.alive) - self.our_kills).max(0);
        if non_self_deaths > self.prev_non_self_deaths {
            self.prev_non_self_deaths = non_self_deaths;
        }
        if (non_self_deaths >= 1 && obs.zone.radius > 110.0) || non_self_deaths >= 2 {
            self.ffa_confirmed = true;
        }
        self.mode = if self.ffa_confirmed {
            MatchMode::Ffa
        } else if t >= 25.0 && non_self_deaths == 0 {
            MatchMode::Practice1v15
        } else {
            MatchMode::Unknown
        };

        // 4. Update sounds
        self.sounds.retain(|s| t - s.t <= 2.5);
        for s in &obs.sounds {
            if s.kind == "death" || s.kind == "drop" {
                continue;
            }
            let d_est = match s.dist.as_str() {
                "near" => 35.0,
                "mid" => 105.0,
                _ => 210.0,
            };
            let est_pos = [
                obs.me.pos[0] + s.bearing.cos() * d_est,
                obs.me.pos[1] + s.bearing.sin() * d_est,
            ];
            self.sounds.push(SoundContact {
                kind: s.kind.clone(),
                est_pos,
                bearing: s.bearing,
                dist_est: d_est,
                t: t - s.age,
            });
        }
        if self.sounds.len() > 24 {
            let drain_n = self.sounds.len() - 24;
            self.sounds.drain(0..drain_n);
        }

        // 5. Remember seen pickups, crates, cubes (keep trees_block_vision so we NEVER evict lasers/cubes behind trees!)
        let do_evict = obs.tick % 4 == 0;
        let should_be_visible = |p: [f64; 3], z_off: f64| -> bool {
            let d = dist2d([obs.me.pos[0], obs.me.pos[1]], [p[0], p[1]]);
            let pt = [p[0], p[1], p[2] + z_off];
            if d < 36.0 {
                return map.has_shot_los(my_eye, pt);
            }
            if d < 120.0 {
                let ang = angle_diff((p[1] - obs.me.pos[1]).atan2(p[0] - obs.me.pos[0]), obs.me.aim[0]).abs();
                if ang < 48.0f64.to_radians()
                    && map.has_shot_los(my_eye, pt)
                    && !map.trees_block_vision(my_eye, pt)
                {
                    return true;
                }
            }
            false
        };

        let vis_pickup_ids: Vec<i64> = obs.pickups.iter().map(|p| p.id).collect();
        for p in &obs.pickups {
            self.pickups.insert(
                p.id,
                KnownPickup { id: p.id, kind: p.kind.clone(), pos: p.pos, last_seen_t: t },
            );
        }
        self.pickups.retain(|id, p| {
            if vis_pickup_ids.contains(id) { return true; }
            if t - p.last_seen_t > 240.0 { return false; }
            !do_evict || !should_be_visible(p.pos, 0.8)
        });

        let vis_crate_ids: Vec<i64> = obs.crates.iter().map(|c| c.id).collect();
        for c in &obs.crates {
            self.crates.insert(
                c.id,
                KnownCrate { id: c.id, pos: c.pos, hp: c.hp.unwrap_or(300.0), last_seen_t: t },
            );
        }
        self.crates.retain(|id, c| {
            if vis_crate_ids.contains(id) { return true; }
            if t - c.last_seen_t > 18.0 { return false; }
            !do_evict || !should_be_visible(c.pos, 1.2)
        });

        let vis_cube_ids: Vec<i64> = obs.cubes.iter().map(|c| c.id).collect();
        for c in &obs.cubes {
            self.cubes.insert(
                c.id,
                KnownCube { id: c.id, pos: c.pos, last_seen_t: t },
            );
        }
        self.cubes.retain(|id, c| {
            if vis_cube_ids.contains(id) { return true; }
            if t - c.last_seen_t > 15.0 { return false; }
            !do_evict || !should_be_visible(c.pos, 0.8)
        });

        // 6. Stuck detection
        let cmd_len = self.last_cmd_move[0].hypot(self.last_cmd_move[1]);
        let moved = dist2d([obs.me.pos[0], obs.me.pos[1]], [self.prev_pos[0], self.prev_pos[1]]);
        let intentional_vertical = self.climb_state.map(|c| c.ascending).unwrap_or(false) && obs.me.vel[2] > 2.0;
        let intentional_hop = self.roof_hop.map(|h| h.phase == 1).unwrap_or(false);
        if cmd_len > 0.35 && moved < 0.25 * (dt / 0.1) && !intentional_vertical && !intentional_hop {
            self.stuck_time += dt;
        } else {
            self.stuck_time = (self.stuck_time - 2.0 * dt).max(0.0);
        }
        if dist2d([obs.me.pos[0], obs.me.pos[1]], self.last_stuck_pos) > 12.0 {
            self.stuck_streak = 0;
        }
        if self.stuck_time > 0.65 && t > self.stuck_escape_until {
            if dist2d([obs.me.pos[0], obs.me.pos[1]], self.last_stuck_pos) <= 12.0 {
                self.stuck_streak = self.stuck_streak.saturating_add(1);
            } else {
                self.stuck_streak = 1;
                self.last_stuck_pos = [obs.me.pos[0], obs.me.pos[1]];
            }
            let to_zone_yaw = (obs.zone.next_center[1] - obs.me.pos[1])
                .atan2(obs.zone.next_center[0] - obs.me.pos[0]);
            let base_ang = self.last_cmd_move[1].atan2(self.last_cmd_move[0]);
            let prev_esc_ang = self.stuck_escape_dir[1].atan2(self.stuck_escape_dir[0]);
            let rev_ang = wrap_angle(base_ang + std::f64::consts::PI);
            let mut best_dir = [rev_ang.cos(), rev_ang.sin()];
            let mut best_sc = f64::NEG_INFINITY;
            for k in 0..12 {
                let ang = base_ang + (k as f64 / 12.0) * std::f64::consts::TAU;
                if angle_diff(ang, base_ang).abs() < 0.65 {
                    continue;
                }
                let mut clear_depth = 0.0;
                for &dist in &[4.5, 7.5, 14.0] {
                    let tx = obs.me.pos[0] + ang.cos() * dist;
                    let ty = obs.me.pos[1] + ang.sin() * dist;
                    let tz = map.support_z(tx, ty, obs.me.pos[2], obs.me.step_up(), obs.me.height());
                    if !map.is_cylinder_blocked(tx, ty, tz, obs.me.radius() + 0.3, obs.me.height(), obs.me.step_up()) {
                        clear_depth = dist;
                    } else {
                        break;
                    }
                }
                if clear_depth < 4.5 {
                    continue;
                }
                let align_zone = angle_diff(ang, to_zone_yaw).cos();
                let mut sc = align_zone * 2.0 + self.next_rand() * 0.4;
                if self.stuck_streak >= 4 {
                    sc += clear_depth * 0.35;
                    if angle_diff(ang, prev_esc_ang).abs() < 0.7 {
                        sc -= 4.0;
                    }
                }
                if sc > best_sc {
                    best_sc = sc;
                    best_dir = [ang.cos(), ang.sin()];
                }
            }
            self.stuck_escape_dir = best_dir;
            let on_elevated_non_roof = obs.me.pos[2] > map.terrain_z(obs.me.pos[0], obs.me.pos[1]) + 4.0
                && map.rooftop_at(obs.me.pos[0], obs.me.pos[1], obs.me.pos[2]).is_none();
            if self.stuck_streak >= 4 {
                self.stuck_escape_until = t + 2.5;
            } else if !obs.me.in_zone || on_elevated_non_roof {
                let zx = obs.zone.next_center[0] - obs.me.pos[0];
                let zy = obs.zone.next_center[1] - obs.me.pos[1];
                let zd = zx.hypot(zy).max(1e-6);
                let uzx = zx / zd;
                let uzy = zy / zd;
                if best_dir[0] * uzx + best_dir[1] * uzy > 0.15 {
                    let mx = best_dir[0] * 0.72 + uzx * 0.28;
                    let my = best_dir[1] * 0.72 + uzy * 0.28;
                    let ml = mx.hypot(my).max(1e-6);
                    self.stuck_escape_dir = [mx / ml, my / ml];
                }
                self.stuck_escape_until = t + 1.20;
            } else {
                self.stuck_escape_until = t + 0.90;
            }
            self.stuck_time = 0.0;
            self.climb_state = None;
            self.roof_hop = None;
            self.laser_peek = LaserPeekState::Idle;
        }

        // Strafe rhythm flip
        if t >= self.next_strafe_flip_t {
            self.strafe_sign = -self.strafe_sign;
            self.next_strafe_flip_t = t + 1.15 + self.next_rand() * 1.05;
        }

        self.prev_pos = obs.me.pos;
        self.prev_t = t;
        self.prev_tick = obs.tick;
    }

    #[inline]
    pub fn is_ffa(&self) -> bool {
        self.mode == MatchMode::Ffa
    }

    #[inline]
    pub fn is_practice(&self) -> bool {
        self.mode == MatchMode::Practice1v15
    }

    #[inline]
    pub fn is_unknown(&self) -> bool {
        self.mode == MatchMode::Unknown
    }

    pub fn time_until_regen(&self, obs: &Observation) -> f64 {
        let elapsed = obs.t - self.last_combat_t;
        (obs.me.regen_delay() - elapsed).max(0.0)
    }

    pub fn is_laser_shorted(&self, t: f64) -> bool {
        t < self.laser_shorted_until
    }
}
