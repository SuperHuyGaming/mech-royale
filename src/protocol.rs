#![allow(dead_code)]
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;
use std::collections::HashMap;

fn de_i64_flex<'de, D>(deserializer: D) -> Result<i64, D::Error>
where
    D: Deserializer<'de>,
{
    let v = Value::deserialize(deserializer)?;
    match v {
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i)
            } else if let Some(u) = n.as_u64() {
                Ok(u as i64)
            } else if let Some(f) = n.as_f64() {
                Ok(f.round() as i64)
            } else {
                Err(serde::de::Error::custom("invalid number"))
            }
        }
        _ => Err(serde::de::Error::custom("expected number")),
    }
}

fn de_opt_i64_flex<'de, D>(deserializer: D) -> Result<Option<i64>, D::Error>
where
    D: Deserializer<'de>,
{
    let v = Option::<Value>::deserialize(deserializer)?;
    match v {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => {
            if let Some(i) = n.as_i64() {
                Ok(Some(i))
            } else if let Some(u) = n.as_u64() {
                Ok(Some(u as i64))
            } else if let Some(f) = n.as_f64() {
                Ok(Some(f.round() as i64))
            } else {
                Ok(None)
            }
        }
        _ => Ok(None),
    }
}

#[inline]
fn map_f64(map: &HashMap<String, Value>, k: &str, default: f64) -> f64 {
    map.get(k)
        .and_then(|v| v.as_f64())
        .filter(|v| v.is_finite())
        .unwrap_or(default)
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    Hello(Hello),
    Obs(Observation),
    End(EndMsg),
    #[serde(other)]
    Unknown,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Hello {
    #[serde(default)]
    pub protocol: String,
    #[serde(deserialize_with = "de_i64_flex")]
    pub slot: i64,
    #[serde(default, deserialize_with = "de_i64_flex")]
    pub seed: i64,
    pub map: HelloMap,
    pub config: Config,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HelloMap {
    pub dir: String,
    #[serde(default)]
    pub files: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    #[serde(rename = "match", default)]
    pub match_cfg: HashMap<String, Value>,
    #[serde(default)]
    pub bot: HashMap<String, Value>,
    #[serde(default)]
    pub mech: HashMap<String, Value>,
    pub vision: ConfigVision,
    pub sound: ConfigSound,
    pub weapons: ConfigWeapons,
    #[serde(default)]
    pub projectile: HashMap<String, Value>,
    pub cubes: ConfigCubes,
    #[serde(default)]
    pub crates: HashMap<String, Value>,
    pub loot: ConfigLoot,
    #[serde(default)]
    pub npc: HashMap<String, Value>,
    #[serde(default)]
    pub drops: Option<ConfigDrops>,
    pub zone: ConfigZone,
    #[serde(default)]
    pub upgrades: Vec<UpgradeDef>,
}

impl Config {
    pub fn mech_f(&self, k: &str, default: f64) -> f64 {
        map_f64(&self.mech, k, default)
    }
    pub fn weapon_f(&self, w: &str, k: &str, default: f64) -> f64 {
        let m = match w {
            "gun" => &self.weapons.gun,
            "mortar" => &self.weapons.mortar,
            "laser" => &self.weapons.laser,
            "lightning" => &self.weapons.lightning,
            _ => return default,
        };
        map_f64(m, k, default)
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigVision {
    pub range: f64,
    pub fov_deg: f64,
    pub near_range: f64,
    #[serde(default = "default_true")]
    pub canopy_blocks: bool,
    #[serde(default = "default_true")]
    pub show_enemy_hp: bool,
    #[serde(default = "default_memory_s")]
    pub memory_s: f64,
}

fn default_true() -> bool { true }
fn default_memory_s() -> f64 { 5.0 }

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigSound {
    pub bearing_noise_deg: f64,
    pub near: f64,
    pub mid: f64,
    pub step_speed: f64,
    pub step_every: f64,
    #[serde(default)]
    pub range: HashMap<String, Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigWeapons {
    #[serde(default)]
    pub gun: HashMap<String, Value>,
    #[serde(default)]
    pub mortar: HashMap<String, Value>,
    #[serde(default)]
    pub laser: HashMap<String, Value>,
    #[serde(default)]
    pub lightning: HashMap<String, Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigCubes {
    #[serde(default = "default_cube_pickup")]
    pub pickup_radius: f64,
    #[serde(default = "default_cube_hp")]
    pub hp_per_cube: f64,
    #[serde(default = "default_cube_dmg")]
    pub damage_per_cube: f64,
    #[serde(default = "default_stat_cap")]
    pub stat_cap: f64,
    #[serde(default = "default_size_per_cube")]
    pub size_per_cube: f64,
    #[serde(default)]
    pub upgrade_thresholds: Vec<f64>,
}

fn default_cube_pickup() -> f64 { 3.5 }
fn default_cube_hp() -> f64 { 0.04 }
fn default_cube_dmg() -> f64 { 0.04 }
fn default_stat_cap() -> f64 { 10.0 }
fn default_size_per_cube() -> f64 { 0.05 }

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigLoot {
    pub pickup_radius: f64,
    pub equip_s: f64,
    #[serde(default)]
    pub floor_count: f64,
    #[serde(default)]
    pub crate_weapon_chance: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigDrops {
    #[serde(default)]
    pub times: Vec<f64>,
    pub fall_s: f64,
    pub radius: f64,
    pub capture_s: f64,
    pub cubes: f64,
    #[serde(default)]
    pub repair: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigZone {
    pub start_center: [f64; 2],
    pub start_radius: f64,
    #[serde(default)]
    pub phases: Vec<ZonePhase>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ZonePhase {
    pub hold_s: f64,
    pub shrink_s: f64,
    pub radius: f64,
    pub dps: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct UpgradeDef {
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub desc: String,
    #[serde(default)]
    pub requires: Option<String>,
    #[serde(default)]
    pub max: f64,
    #[serde(default)]
    pub effects: Vec<UpgradeEffect>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct UpgradeEffect {
    pub path: String,
    #[serde(default)]
    pub mul: Option<f64>,
    #[serde(default)]
    pub add: Option<f64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Observation {
    #[serde(deserialize_with = "de_i64_flex")]
    pub tick: i64,
    pub t: f64,
    #[serde(deserialize_with = "de_i64_flex")]
    pub alive: i64,
    #[serde(rename = "self")]
    pub me: SelfObs,
    #[serde(default)]
    pub mechs: Vec<ActorObs>,
    #[serde(default)]
    pub projectiles: Vec<ProjectileObs>,
    #[serde(default)]
    pub crates: Vec<ItemObs>,
    #[serde(default)]
    pub cubes: Vec<ItemObs>,
    #[serde(default)]
    pub pickups: Vec<PickupObs>,
    #[serde(default)]
    pub sounds: Vec<SoundObs>,
    #[serde(default)]
    pub beams: Vec<BeamObs>,
    #[serde(default)]
    pub events: Vec<GameEvent>,
    pub zone: ZoneObs,
    #[serde(default)]
    pub offer: Option<Offer>,
    #[serde(default)]
    pub drops: Vec<DropObs>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SelfObs {
    #[serde(deserialize_with = "de_i64_flex")]
    pub id: i64,
    pub pos: [f64; 3],
    pub vel: [f64; 3],
    pub aim: [f64; 2],
    pub hp: f64,
    pub max_hp: f64,
    pub energy: f64,
    pub max_energy: f64,
    pub grounded: bool,
    #[serde(default, deserialize_with = "de_i64_flex")]
    pub cubes: i64,
    #[serde(default)]
    pub upgrades: Vec<String>,
    #[serde(default)]
    pub weapons: Vec<Option<WeaponObs>>,
    pub in_zone: bool,
    #[serde(default)]
    pub stats: HashMap<String, Value>,
}

impl SelfObs {
    pub fn stat(&self, k: &str, default: f64) -> f64 {
        map_f64(&self.stats, k, default)
    }
    pub fn size_scale(&self, spc: f64) -> f64 {
        1.0 + spc * (self.cubes.max(0) as f64)
    }
    /// Defensively guarantees cube size scaling even if self.stats reports unscaled base radius/height.
    pub fn radius(&self) -> f64 {
        let s = self.stat("radius", 2.5);
        s.max(2.5 * self.size_scale(0.05))
    }
    pub fn height(&self) -> f64 {
        let s = self.stat("height", 9.0);
        s.max(9.0 * self.size_scale(0.05))
    }
    pub fn eye_height(&self) -> f64 {
        let s = self.stat("eye_height", 8.0);
        s.max(8.0 * self.size_scale(0.05))
    }
    pub fn muzzle_height(&self) -> f64 {
        let s = self.stat("muzzle_height", 7.0);
        s.max(7.0 * self.size_scale(0.05))
    }
    pub fn mortar_height(&self) -> f64 { self.height() + 0.5 }
    pub fn walk_speed(&self) -> f64 { self.stat("walk_speed", 11.7) }
    pub fn boost_speed(&self) -> f64 { self.stat("boost_speed", 19.5) }
    pub fn turn_rate(&self) -> f64 { self.stat("turn_rate", 4.0) }
    pub fn pitch_rate(&self) -> f64 { self.stat("pitch_rate", 3.0) }
    pub fn min_pitch(&self) -> f64 { self.stat("min_pitch", -0.7) }
    pub fn max_pitch(&self) -> f64 { self.stat("max_pitch", 1.3) }
    pub fn regen_delay(&self) -> f64 { self.stat("regen_delay", 5.0) }
    pub fn regen_per_s(&self) -> f64 { self.stat("regen_per_s", 45.0) }
    pub fn armor(&self) -> f64 { self.stat("armor", 0.0) }
    pub fn jet_drain(&self) -> f64 { self.stat("jet_drain", 15.0) }
    pub fn boost_drain(&self) -> f64 { self.stat("boost_drain", 17.0) }
    pub fn step_up(&self) -> f64 { self.stat("step_up", 1.2) }
    pub fn eye_pos(&self) -> [f64; 3] {
        [self.pos[0], self.pos[1], self.pos[2] + self.eye_height()]
    }
    pub fn muzzle_pos(&self) -> [f64; 3] {
        [self.pos[0], self.pos[1], self.pos[2] + self.muzzle_height()]
    }
    pub fn center_pos(&self) -> [f64; 3] {
        [self.pos[0], self.pos[1], self.pos[2] + 0.5 * self.height()]
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct WeaponObs {
    pub kind: String,
    #[serde(default)]
    pub cooldown: f64,
    #[serde(default)]
    pub ammo: Option<f64>,
    #[serde(default)]
    pub reload: Option<f64>,
    #[serde(default)]
    pub charge: Option<f64>,
    #[serde(default)]
    pub stats: HashMap<String, Value>,
}

impl WeaponObs {
    pub fn stat(&self, k: &str, default: f64) -> f64 {
        map_f64(&self.stats, k, default)
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ActorObs {
    #[serde(deserialize_with = "de_i64_flex")]
    pub id: i64,
    pub kind: String,
    pub pos: [f64; 3],
    pub vel: [f64; 3],
    pub aim: [f64; 2],
    #[serde(default)]
    pub hp: Option<f64>,
    #[serde(default)]
    pub max_hp: Option<f64>,
    #[serde(default, deserialize_with = "de_opt_i64_flex")]
    pub cubes: Option<i64>,
    #[serde(default)]
    pub weapons: Vec<String>,
    #[serde(default)]
    pub charging: bool,
}

impl ActorObs {
    pub fn is_mech(&self) -> bool { self.kind == "mech" }
    pub fn is_npc(&self) -> bool { self.kind == "npc" }
    pub fn scale(&self, size_per_cube: f64) -> f64 {
        if self.is_mech() {
            1.0 + size_per_cube * (self.cubes.unwrap_or(0).max(0) as f64)
        } else {
            1.0
        }
    }
    pub fn radius(&self, base_r: f64, size_per_cube: f64) -> f64 {
        if self.is_mech() { base_r * self.scale(size_per_cube) } else { 1.8 }
    }
    pub fn height(&self, base_h: f64, size_per_cube: f64) -> f64 {
        if self.is_mech() { base_h * self.scale(size_per_cube) } else { 3.5 }
    }
    pub fn has_weapon(&self, w: &str) -> bool {
        self.weapons.iter().any(|x| x == w)
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ProjectileObs {
    #[serde(deserialize_with = "de_i64_flex")]
    pub id: i64,
    pub kind: String,
    pub pos: [f64; 3],
    pub vel: [f64; 3],
}

#[derive(Debug, Clone, Deserialize)]
pub struct ItemObs {
    #[serde(deserialize_with = "de_i64_flex")]
    pub id: i64,
    pub pos: [f64; 3],
    #[serde(default)]
    pub hp: Option<f64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PickupObs {
    #[serde(deserialize_with = "de_i64_flex")]
    pub id: i64,
    pub kind: String,
    pub pos: [f64; 3],
}

#[derive(Debug, Clone, Deserialize)]
pub struct SoundObs {
    pub kind: String,
    pub bearing: f64,
    pub dist: String,
    #[serde(default)]
    pub age: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BeamObs {
    #[serde(deserialize_with = "de_i64_flex")]
    pub by: i64,
    pub kind: String,
    #[serde(default)]
    pub path: Vec<[f64; 3]>,
    #[serde(default)]
    pub age: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct GameEvent {
    #[serde(rename = "type", default)]
    pub kind: String,
    #[serde(default)]
    pub amount: Option<f64>,
    #[serde(default)]
    pub weapon: Option<String>,
    #[serde(default)]
    pub bearing: Option<f64>,
    #[serde(default, deserialize_with = "de_opt_i64_flex")]
    pub by: Option<i64>,
    #[serde(default, deserialize_with = "de_opt_i64_flex")]
    pub target: Option<i64>,
    #[serde(default)]
    pub rear: Option<bool>,
    #[serde(default)]
    pub effect: Option<String>,
    #[serde(default)]
    pub id: Option<Value>,
    #[serde(default, deserialize_with = "de_opt_i64_flex")]
    pub cubes: Option<i64>,
    #[serde(default, deserialize_with = "de_opt_i64_flex")]
    pub slot: Option<i64>,
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ZoneObs {
    pub center: [f64; 2],
    pub radius: f64,
    pub next_center: [f64; 2],
    pub next_radius: f64,
    pub shrink_start: f64,
    pub shrink_end: f64,
    pub dps: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Offer {
    #[serde(deserialize_with = "de_i64_flex")]
    pub id: i64,
    #[serde(default)]
    pub choices: Vec<OfferChoice>,
    #[serde(default)]
    pub expires: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct OfferChoice {
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub desc: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DropObs {
    #[serde(deserialize_with = "de_i64_flex")]
    pub id: i64,
    pub pos: [f64; 3],
    pub radius: f64,
    pub lands_in: f64,
    pub capture_s: f64,
    #[serde(default)]
    pub progress: f64,
    #[serde(default, deserialize_with = "de_opt_i64_flex")]
    pub holder: Option<i64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct EndMsg {
    #[serde(default, deserialize_with = "de_i64_flex")]
    pub place: i64,
    #[serde(default, deserialize_with = "de_i64_flex")]
    pub kills: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ReadyMsg {
    #[serde(rename = "type")]
    pub msg_type: &'static str,
    pub name: String,
}

#[derive(Debug, Clone, Default)]
pub struct Action {
    pub tick: i64,
    pub move_dir: Option<[f64; 2]>,
    pub boost: Option<bool>,
    pub jump: Option<bool>,
    pub aim: Option<[f64; 2]>,
    pub fire: Option<[bool; 2]>,
    pub reload: Option<bool>,
    pub pick: Option<usize>,
    pub take: Option<i64>,
    pub drop: Option<usize>,
}

#[derive(Debug, Clone, Serialize)]
pub struct WireAction {
    pub tick: i64,
    #[serde(rename = "move", skip_serializing_if = "Option::is_none")]
    pub move_dir: Option<[f64; 2]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub boost: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub jump: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub aim: Option<[f64; 2]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fire: Option<[bool; 2]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reload: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pick: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub take: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub drop: Option<usize>,
}

impl Action {
    pub fn to_wire_sanitized(&self, min_pitch: f64, max_pitch: f64) -> WireAction {
        let (lo_p, hi_p) = if min_pitch.is_finite() && max_pitch.is_finite() && min_pitch <= max_pitch {
            (min_pitch, max_pitch)
        } else {
            (-0.7, 1.3)
        };
        let move_dir = self.move_dir.map(|[mx, my]| {
            let mut x = if mx.is_finite() { mx } else { 0.0 };
            let mut y = if my.is_finite() { my } else { 0.0 };
            let len = x.hypot(y);
            if len > 1.0 {
                x /= len;
                y /= len;
            }
            // Truncate toward zero so post-rounding magnitude NEVER exceeds 1.0!
            let mut rx = (x * 10000.0).trunc() / 10000.0;
            let mut ry = (y * 10000.0).trunc() / 10000.0;
            if rx * rx + ry * ry > 1.0 {
                let rlen = rx.hypot(ry).max(1.0);
                rx = ((rx / rlen) * 9999.0).trunc() / 10000.0;
                ry = ((ry / rlen) * 9999.0).trunc() / 10000.0;
            }
            [rx, ry]
        });
        let aim = self.aim.map(|[yaw, pitch]| {
            let y = if yaw.is_finite() { round4(wrap_angle(yaw)) } else { 0.0 };
            let p = if pitch.is_finite() {
                round4(pitch.clamp(lo_p, hi_p)).clamp(lo_p, hi_p)
            } else {
                0.0f64.clamp(lo_p, hi_p)
            };
            [y, p]
        });
        WireAction {
            tick: self.tick,
            move_dir,
            boost: self.boost,
            jump: self.jump,
            aim,
            fire: self.fire,
            reload: self.reload,
            pick: self.pick,
            take: self.take,
            drop: self.drop,
        }
    }
}

#[inline]
pub fn round4(v: f64) -> f64 {
    if !v.is_finite() {
        return 0.0;
    }
    (v * 10000.0).round() / 10000.0
}

#[inline]
pub fn wrap_angle(mut a: f64) -> f64 {
    if !a.is_finite() {
        return 0.0;
    }
    let pi = std::f64::consts::PI;
    let tau = 2.0 * pi;
    a = (a + pi).rem_euclid(tau) - pi;
    a
}

#[inline]
pub fn angle_diff(target: f64, current: f64) -> f64 {
    wrap_angle(target - current)
}

#[inline]
pub fn dist2d(a: [f64; 2], b: [f64; 2]) -> f64 {
    (a[0] - b[0]).hypot(a[1] - b[1])
}

#[inline]
pub fn dist3d(a: [f64; 3], b: [f64; 3]) -> f64 {
    let dx = a[0] - b[0];
    let dy = a[1] - b[1];
    let dz = a[2] - b[2];
    (dx * dx + dy * dy + dz * dz).sqrt()
}

#[inline]
pub fn aim_dir(yaw: f64, pitch: f64) -> [f64; 3] {
    let cp = pitch.cos();
    [cp * yaw.cos(), cp * yaw.sin(), pitch.sin()]
}
