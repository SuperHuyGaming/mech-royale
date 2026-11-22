use crate::map::ArenaMap;
use crate::protocol::*;
use crate::world_state::{EnemyTrack, WorldState};

#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct CombatDecision {
    pub aim: Option<[f64; 2]>,
    pub fire: [bool; 2],
    pub reload: bool,
    pub target_enemy_id: Option<i64>,
    pub target_has_los: bool,
    pub charging_laser: bool,
    pub laser_ready_soon: bool,
    pub laser_cd: f64,
    pub want_peek: bool,
    pub gun_reloading: bool,
}

#[derive(Debug, Clone, Copy)]
pub struct DodgeOutput {
    pub vec: [f64; 2],
    pub urgency: f64,
    pub want_boost: bool,
    pub want_jump: bool,
    pub mortar_blast_pos: Option<[f64; 2]>,
}

pub fn find_weapon_slot(me: &SelfObs, kind: &str) -> Option<usize> {
    me.weapons.iter().take(2).position(|w| {
        w.as_ref().map(|x| x.kind.as_str() == kind).unwrap_or(false)
    })
}

pub fn cube_damage_mult(cubes: i64, cfg: &Config) -> f64 {
    let capped = (cubes.max(0) as f64).min(cfg.cubes.stat_cap);
    1.0 + cfg.cubes.damage_per_cube * capped
}

#[inline]
fn slew_one_tick(
    cur_aim: [f64; 2],
    cmd_aim: [f64; 2],
    turn_rate: f64,
    pitch_rate: f64,
    min_p: f64,
    max_p: f64,
) -> [f64; 2] {
    let dy = angle_diff(cmd_aim[0], cur_aim[0]).clamp(-turn_rate * 0.05, turn_rate * 0.05);
    let dp = (cmd_aim[1].clamp(min_p, max_p) - cur_aim[1]).clamp(-pitch_rate * 0.05, pitch_rate * 0.05);
    [wrap_angle(cur_aim[0] + dy), (cur_aim[1] + dp).clamp(min_p, max_p)]
}

#[inline]
fn ray_hits_cylinder_exact(
    o: [f64; 3],
    d: [f64; 3],
    max_t: f64,
    pos: [f64; 3],
    r: f64,
    h: f64,
) -> Option<f64> {
    let ox = o[0] - pos[0];
    let oy = o[1] - pos[1];
    let a = d[0] * d[0] + d[1] * d[1];
    let b = 2.0 * (ox * d[0] + oy * d[1]);
    let c = ox * ox + oy * oy - r * r;
    let (t0, t1) = if a < 1e-12 {
        if c <= 0.0 { (0.0, max_t) } else { return None; }
    } else {
        let disc = b * b - 4.0 * a * c;
        if disc < 0.0 { return None; }
        let s = disc.sqrt();
        ((-b - s) / (2.0 * a), (-b + s) / (2.0 * a))
    };
    let mut t_enter = t0.max(0.0);
    let mut t_exit = t1.min(max_t);
    if t_enter > t_exit {
        return None;
    }
    // Exact Z-slab intersection (Fixes P1-2 steep upward/near-vertical angles!)
    if d[2].abs() < 1e-12 {
        if o[2] < pos[2] || o[2] > pos[2] + h {
            return None;
        }
    } else {
        let mut tz0 = (pos[2] - o[2]) / d[2];
        let mut tz1 = (pos[2] + h - o[2]) / d[2];
        if tz0 > tz1 {
            std::mem::swap(&mut tz0, &mut tz1);
        }
        t_enter = t_enter.max(tz0);
        t_exit = t_exit.min(tz1);
        if t_enter > t_exit {
            return None;
        }
    }
    Some(t_enter)
}

/// Exact wall-clipped shot origin + solid occlusion + ray-cylinder check along actual slew aim!
#[allow(clippy::too_many_arguments)]
fn exact_ray_hits_target(
    map: &ArenaMap,
    muzzle: [f64; 3],
    my_r: f64,
    actual_aim: [f64; 2],
    target_feet: [f64; 3],
    target_r: f64,
    target_h: f64,
    max_range: f64,
) -> bool {
    let dir = aim_dir(actual_aim[0], actual_aim[1]);
    let off = my_r + 0.5;
    let clip_t = map.raycast_solid(muzzle, dir, off).map(|h| (h.t - 0.05).max(0.0)).unwrap_or(off);
    if clip_t < off - 0.15 {
        return false; // Barrel blocked inside wall!
    }
    let shot_o = [
        muzzle[0] + dir[0] * clip_t,
        muzzle[1] + dir[1] * clip_t,
        muzzle[2] + dir[2] * clip_t,
    ];
    let solid_t = map.raycast_solid(shot_o, dir, max_range).map(|h| h.t).unwrap_or(max_range);
    ray_hits_cylinder_exact(shot_o, dir, solid_t, target_feet, target_r, target_h).is_some()
}

#[inline]
fn aim_hits_cylinder(actual_aim: [f64; 2], desired_aim: [f64; 2], dist: f64, target_r: f64, target_h: f64) -> bool {
    let rem_yaw = angle_diff(desired_aim[0], actual_aim[0]).abs();
    let rem_pitch = (desired_aim[1] - actual_aim[1]).abs();
    let yaw_tol = (target_r / dist.max(1.0)).atan().clamp(0.004, 0.25);
    let pitch_tol = (target_h * 0.45 / dist.max(1.0)).atan().clamp(0.006, 0.25);
    rem_yaw <= yaw_tol && rem_pitch <= pitch_tol
}

/// Multi-height LOS check against an enemy mech (chest, upper torso, lower torso).
pub fn best_los_point(map: &ArenaMap, from: [f64; 3], e_pos: [f64; 3], e_h: f64) -> Option<[f64; 3]> {
    for frac in [0.50, 0.78, 0.26] {
        let pt = [e_pos[0], e_pos[1], e_pos[2] + e_h * frac];
        if map.has_shot_los(from, pt) {
            return Some(pt);
        }
    }
    None
}

/// OU-damped lateral lead + rooftop-safe support_z vertical prediction + wall-slide clamp!
#[allow(clippy::too_many_arguments)]
pub fn lead_aim_damped(
    map: &ArenaMap,
    muzzle: [f64; 3],
    my_vel: [f64; 3],
    target_pt: [f64; 3],
    target_feet_z: f64,
    target_vel: [f64; 3],
    strafe_erratic: f64,
    proj_speed: f64,
    shot_offset: f64,
    t_delay: f64,
) -> ([f64; 2], [f64; 3], f64) {
    let m0 = [
        muzzle[0] + my_vel[0] * t_delay,
        muzzle[1] + my_vel[1] * t_delay,
        muzzle[2] + my_vel[2] * t_delay,
    ];
    let t0 = [
        target_pt[0] + target_vel[0] * t_delay,
        target_pt[1] + target_vel[1] * t_delay,
        target_pt[2] + target_vel[2] * t_delay,
    ];

    let rdx = t0[0] - m0[0];
    let rdy = t0[1] - m0[1];
    let rlen = rdx.hypot(rdy).max(1e-6);
    let rx = rdx / rlen;
    let ry = rdy / rlen;
    let v_rad = target_vel[0] * rx + target_vel[1] * ry;
    let v_lat_x = target_vel[0] - v_rad * rx;
    let v_lat_y = target_vel[1] - v_rad * ry;

    // Only apply OU lateral damping when enemy actually reverses strafe; straight-line runners get exact linear intercept!
    let eff_erratic = (strafe_erratic - 0.12).max(0.0);
    let lambda = eff_erratic * 2.1;
    let pt_h_off = (target_pt[2] - target_feet_z).max(0.5);

    let surf_z = map.support_z(target_pt[0], target_pt[1], target_feet_z, 1.2, 4.0);
    let airborne = target_feet_z > surf_z + 0.6 || target_vel[2].abs() > 1.5;

    let mut pred = t0;
    let mut flight_t = 0.0;
    for _ in 0..3 {
        let d = dist3d(m0, pred);
        flight_t = ((d - shot_offset).max(0.0)) / proj_speed.max(1.0);
        let tf = flight_t.min(0.90);
        let t_lat = if lambda > 1e-3 {
            (1.0 - (-lambda * tf).exp()) / lambda
        } else {
            tf * 0.98
        };
        let t_rad = tf * 0.98;
        let px = t0[0] + rx * v_rad * t_rad + v_lat_x * t_lat;
        let py = t0[1] + ry * v_rad * t_rad + v_lat_y * t_lat;
        let p_surf = map.support_z(px, py, target_feet_z, 1.2, 4.0);
        let pz = if airborne {
            let g_eff = if target_vel[2] > 4.5 { 0.0 } else { 22.0 };
            let raw_z = t0[2] + target_vel[2] * tf - 0.5 * g_eff * tf * tf;
            raw_z.max(p_surf + pt_h_off)
        } else if (p_surf - target_feet_z).abs() < 2.2 {
            p_surf + pt_h_off
        } else {
            t0[2]
        };
        pred = [px, py, pz];
    }

    let pred_feet = pred[2] - pt_h_off;
    if map.is_cylinder_blocked(pred[0], pred[1], pred_feet + 0.1, 1.5, 4.0, 1.0) {
        let mut lo = 0.0;
        let mut hi = 1.0;
        for _ in 0..4 {
            let mid = 0.5 * (lo + hi);
            let cx = t0[0] + (pred[0] - t0[0]) * mid;
            let cy = t0[1] + (pred[1] - t0[1]) * mid;
            let cz = t0[2] + (pred[2] - t0[2]) * mid;
            if map.is_cylinder_blocked(cx, cy, cz - pt_h_off + 0.1, 1.5, 4.0, 1.0) {
                hi = mid;
            } else {
                lo = mid;
            }
        }
        pred = [
            t0[0] + (pred[0] - t0[0]) * lo,
            t0[1] + (pred[1] - t0[1]) * lo,
            t0[2] + (pred[2] - t0[2]) * lo,
        ];
    }

    let dx = pred[0] - m0[0];
    let dy = pred[1] - m0[1];
    let dz = pred[2] - m0[2];
    let horiz = dx.hypot(dy).max(1e-6);
    ([dy.atan2(dx), dz.atan2(horiz)], pred, flight_t)
}

pub fn solve_mortar_arc(
    map: &ArenaMap,
    me: &SelfObs,
    target_feet: [f64; 3],
    target_vel: [f64; 3],
    speed: f64,
    gravity: f64,
    prefer_high: bool,
) -> Option<([f64; 2], f64)> {
    let tube = [
        me.pos[0] + me.vel[0] * 0.05,
        me.pos[1] + me.vel[1] * 0.05,
        me.pos[2] + me.vel[2] * 0.05 + me.mortar_height(),
    ];
    let min_p = me.min_pitch();
    let max_p = me.max_pitch();
    let off = me.radius() + 0.5;
    let aim_target_base = [target_feet[0], target_feet[1], target_feet[2] + 1.0];

    let arcs = if prefer_high { [true, false] } else { [false, true] };
    if gravity <= 0.1 || speed <= 1.0 {
        return None;
    }
    for high_arc in arcs {
        let mut pred = aim_target_base;
        let mut launch_o = tube;
        let mut solved: Option<(f64, f64, f64)> = None;

        for _ in 0..4 {
            let dx = pred[0] - launch_o[0];
            let dy = pred[1] - launch_o[1];
            let r = dx.hypot(dy);
            if r < 11.0 {
                solved = None;
                break;
            }
            let dz = pred[2] - launch_o[2];
            let v2 = speed * speed;
            let disc = v2 * v2 - gravity * (gravity * r * r + 2.0 * dz * v2);
            if disc < 0.0 {
                solved = None;
                break;
            }
            let s = disc.sqrt();
            let pitch = if high_arc {
                ((v2 + s) / (gravity * r)).atan()
            } else {
                ((v2 - s) / (gravity * r)).atan()
            };
            if !pitch.is_finite() || pitch < min_p || pitch > max_p {
                solved = None;
                break;
            }
            let vh = speed * pitch.cos();
            if vh < 1.0 {
                solved = None;
                break;
            }
            let t_flight = r / vh;
            if t_flight > 6.6 {
                solved = None;
                break;
            }
            let yaw = dy.atan2(dx);
            let dir = aim_dir(yaw, pitch);
            launch_o = [
                tube[0] + dir[0] * off,
                tube[1] + dir[1] * off,
                tube[2] + dir[2] * off,
            ];
            let lead_t = t_flight.min(2.0);
            pred = [
                aim_target_base[0] + target_vel[0] * lead_t * 0.68,
                aim_target_base[1] + target_vel[1] * lead_t * 0.68,
                aim_target_base[2],
            ];
            solved = Some((yaw, pitch, t_flight));
        }

        if let Some((yaw, pitch, t_flight)) = solved {
            let dir = aim_dir(yaw, pitch);
            let launch_origin = [
                tube[0] + dir[0] * off,
                tube[1] + dir[1] * off,
                tube[2] + dir[2] * off,
            ];
            if !map.has_shot_los(tube, launch_origin) {
                continue;
            }
            let vel = [dir[0] * speed, dir[1] * speed, dir[2] * speed];
            if map.mortar_arc_clear(launch_origin, vel, gravity, t_flight) {
                return Some(([yaw, pitch], t_flight));
            }
        }
    }
    None
}

/// Active evasion from incoming projectiles, mortars, and charging lasers (cover-biased + rooftop/penthouse safe!).
pub fn compute_dodge(obs: &Observation, state: &WorldState, map: &ArenaMap) -> DodgeOutput {
    let me = &obs.me;
    let my_r = me.radius();
    let my_h = me.height();
    let my_c = me.center_pos();
    let base_muzzle = state.config.mech_f("muzzle_height", 7.0);
    let spc = state.config.cubes.size_per_cube;
    let hp_frac = me.hp / me.max_hp.max(1.0);
    let cur_roof = map.rooftop_at(me.pos[0], me.pos[1], me.pos[2]);

    let mut dx = 0.0;
    let mut dy = 0.0;
    let mut urgency = 0.0f64;
    let mut want_boost = false;
    let mut closest_mortar_blast: Option<(f64, [f64; 2])> = None;

    let mortar_g = state.config.weapon_f("mortar", "gravity", 22.0);
    let mortar_r = state.config.weapon_f("mortar", "splash_radius", 7.0) + 3.8;

    let side_safe = |sx: f64, sy: f64| -> bool {
        if dist2d([sx, sy], obs.zone.center) > obs.zone.radius - 2.0 {
            return false;
        }
        if let Some(r_idx) = cur_roof {
            let pid = map.rooftops[r_idx].prism_id;
            let top = map.prisms[pid].top;
            return map.point_in_prism_2d(pid, sx, sy)
                && !map.is_cylinder_blocked(sx, sy, top + 0.05, my_r + 0.2, my_h, 0.5);
        }
        let tz = map.terrain_z(sx, sy);
        !map.is_cylinder_blocked(sx, sy, tz, my_r + 0.2, my_h, me.step_up())
    };

    for p in &obs.projectiles {
        if p.kind == "mortar" {
            let mut best_d = f64::INFINITY;
            let mut imp = p.pos;
            let mut imp_t = 0.0;
            for step in 1..=25 {
                let st = step as f64 * 0.1;
                let px = p.pos[0] + p.vel[0] * st;
                let py = p.pos[1] + p.vel[1] * st;
                let pz = p.pos[2] + p.vel[2] * st - 0.5 * mortar_g * st * st;
                let gz = map.support_z(px, py, pz, 0.5, 4.0).max(map.terrain_z(px, py));
                let d_xy = (px - me.pos[0]).hypot(py - me.pos[1]);
                if pz <= me.pos[2] + my_h && pz >= me.pos[2] - 4.0 && d_xy < best_d {
                    best_d = d_xy;
                    imp = [px, py, pz];
                    imp_t = st;
                }
                if pz <= gz + 0.2 {
                    if d_xy < best_d {
                        best_d = d_xy;
                        imp = [px, py, gz];
                        imp_t = st;
                    }
                    break;
                }
            }
            if best_d < mortar_r + my_r && imp_t < 2.2 {
                if closest_mortar_blast.map(|(bt, _)| imp_t < bt).unwrap_or(true) {
                    closest_mortar_blast = Some((imp_t, [imp[0], imp[1]]));
                }
                let ex = me.pos[0] - imp[0];
                let ey = me.pos[1] - imp[1];
                let elen = ex.hypot(ey);
                let (ux, uy) = if elen > 0.1 { (ex / elen, ey / elen) } else { (state.strafe_sign, 0.0) };
                let w = ((mortar_r + my_r - best_d) / mortar_r).clamp(0.4, 1.7) * 3.4;
                dx += ux * w;
                dy += uy * w;
                urgency = urgency.max(2.9);
                if imp_t < 1.15 && me.energy > 9.0 {
                    want_boost = true;
                }
            }
        } else {
            let speed = (p.vel[0] * p.vel[0] + p.vel[1] * p.vel[1] + p.vel[2] * p.vel[2]).sqrt();
            if speed < 5.0 { continue; }
            let rx = my_c[0] - p.pos[0];
            let ry = my_c[1] - p.pos[1];
            let rz = my_c[2] - p.pos[2];
            let t_ca = (rx * p.vel[0] + ry * p.vel[1] + rz * p.vel[2]) / (speed * speed);
            if t_ca < 0.0 || t_ca > 0.60 { continue; }
            let cx = p.pos[0] + p.vel[0] * t_ca;
            let cy = p.pos[1] + p.vel[1] * t_ca;
            let cz = p.pos[2] + p.vel[2] * t_ca;
            let miss_xy = (my_c[0] - cx).hypot(my_c[1] - cy);
            let miss_z = (my_c[2] - cz).abs();
            if miss_xy < my_r + 2.4 && miss_z < my_h * 0.65 {
                let vh = p.vel[0].hypot(p.vel[1]).max(1e-6);
                let px_dir = -p.vel[1] / vh;
                let py_dir = p.vel[0] / vh;
                let side_dot = (my_c[0] - cx) * px_dir + (my_c[1] - cy) * py_dir;
                let mut sign = if side_dot.abs() > my_r * 0.65 { side_dot.signum() } else { state.strafe_sign };
                let p_pos = side_safe(me.pos[0] + px_dir * sign * 4.5, me.pos[1] + py_dir * sign * 4.5);
                let p_neg = side_safe(me.pos[0] - px_dir * sign * 4.5, me.pos[1] - py_dir * sign * 4.5);
                if !p_pos && p_neg { sign = -sign; }
                let w = (1.1 - t_ca).clamp(0.3, 1.1);
                dx += px_dir * sign * w;
                dy += py_dir * sign * w;
                urgency = urgency.max(0.95);
                if hp_frac < 0.42 && t_ca < 0.30 && miss_xy < my_r + 0.8 && me.energy > 22.0 {
                    want_boost = true;
                }
            }
        }
    }

    let my_laser_chg = me
        .weapons
        .iter()
        .flatten()
        .find(|w| w.kind == "laser")
        .and_then(|w| w.charge)
        .unwrap_or(0.0);
    let my_laser_full = me
        .weapons
        .iter()
        .flatten()
        .find(|w| w.kind == "laser")
        .map(|w| w.stat("charge", 0.75))
        .unwrap_or(0.75);

    for e in state.enemies.values() {
        if !e.visible_now || !e.charging { continue; }
        let em = e.muzzle(base_muzzle, spc);
        let d = dist3d(em, my_c);
        if d > 285.0 { continue; }
        let edir = aim_dir(e.aim[0], e.aim[1]);
        let vx = my_c[0] - em[0];
        let vy = my_c[1] - em[1];
        let vz = my_c[2] - em[2];
        let proj = vx * edir[0] + vy * edir[1] + vz * edir[2];
        if proj <= 0.0 || proj > d + 10.0 { continue; }
        let px = em[0] + edir[0] * proj;
        let py = em[1] + edir[1] * proj;
        let pz = em[2] + edir[2] * proj;
        let miss = dist3d(my_c, [px, py, pz]);
        let yaw_e_to_us = (my_c[1] - em[1]).atan2(my_c[0] - em[0]);
        let aimed_at_us = miss < my_r + 10.0 || angle_diff(e.aim[0], yaw_e_to_us).abs() < 0.35;
        let chg_t = e.charge_elapsed(obs.t);
        if aimed_at_us && map.has_shot_los(em, my_c) {
            // Verified unobstructed hard-cover duck: real house bots fire into the wall on LOS break (0 dmg + 1.65s CD)!
            let my_almost_ready = my_laser_chg >= (my_laser_full - 0.22).max(0.15);
            let max_step = if my_almost_ready {
                4.5
            } else if my_laser_chg > 0.0 {
                6.0
            } else if me.energy > 12.0 {
                10.5
            } else {
                7.5
            };
            let beam_yaw = yaw_e_to_us;
            let my_waist = [me.pos[0], me.pos[1], me.pos[2] + 3.0];
            let offsets = [
                std::f64::consts::FRAC_PI_2,
                -std::f64::consts::FRAC_PI_2,
                0.75 * std::f64::consts::PI,
                -0.75 * std::f64::consts::PI,
                0.25 * std::f64::consts::PI,
                -0.25 * std::f64::consts::PI,
                0.0,
            ];
            let mut best_duck: Option<(f64, f64, f64)> = None;
            for &off in &offsets {
                let ang = beam_yaw + off;
                let cx_dir = ang.cos();
                let cy_dir = ang.sin();
                for &step in &[3.5, 6.0, 8.5, 10.5] {
                    if step > max_step + 0.01 { break; }
                    let sp = [me.pos[0] + cx_dir * step, me.pos[1] + cy_dir * step];
                    let mid = [me.pos[0] + cx_dir * (step * 0.5), me.pos[1] + cy_dir * (step * 0.5)];
                    if side_safe(mid[0], mid[1])
                        && side_safe(sp[0], sp[1])
                        && map.has_shot_los(my_waist, [sp[0], sp[1], me.pos[2] + 3.0])
                        && !map.has_shot_los(em, [sp[0], sp[1], me.pos[2] + my_h * 0.78])
                    {
                        let cost = step + if off.abs() < 1.0 { 1.8 } else { 0.0 };
                        if best_duck.map(|(bc, _, _)| cost < bc).unwrap_or(true) {
                            best_duck = Some((cost, cx_dir, cy_dir));
                        }
                        break;
                    }
                }
            }
            let Some((_, dux, duy)) = best_duck else {
                continue; // Open air: don't waste movement trying to sidestep a hitscan beam!
            };

            let w = if chg_t >= 0.10 { 4.8 } else { 3.0 };
            dx += dux * w;
            dy += duy * w;
            urgency = urgency.max(if chg_t >= 0.10 { 3.8 } else { 2.6 });
            if me.energy > 8.0 {
                want_boost = true;
            }
        }
    }

    let len = dx.hypot(dy);
    let vec = if len > 1e-6 { [dx / len, dy / len] } else { [0.0, 0.0] };
    DodgeOutput {
        vec,
        urgency,
        want_boost,
        want_jump: false,
        mortar_blast_pos: closest_mortar_blast.map(|(_, p)| p),
    }
}

/// Optimal shield-facing yaw minimizing expected rear-armor damage across all threats (Fixes GPT-6.2 P1-3!)
pub fn compute_shield_yaw(obs: &Observation, state: &WorldState, dodge: &DodgeOutput) -> Option<f64> {
    let me = &obs.me;
    let half_front_arc = state.config.mech_f("front_arc_deg", 180.0).to_radians() * 0.5;
    let rear_mult = state.config.mech_f("rear_mult", 1.5);
    let mut threats: Vec<(f64, f64)> = Vec::new();

    if let Some(mb) = dodge.mortar_blast_pos {
        let ang = (mb[1] - me.pos[1]).atan2(mb[0] - me.pos[0]);
        threats.push((ang, 3.8));
    }

    if (obs.t - state.last_damaged_t) < 2.5 {
        if let Some(b) = state.last_damaged_bearing {
            threats.push((b, 2.8));
        }
    }

    for e in state.enemies.values() {
        let age = obs.t - e.last_seen_t;
        if age > 4.5 { continue; }
        let d = dist2d([me.pos[0], me.pos[1]], [e.pos[0], e.pos[1]]);
        if d > 210.0 { continue; }
        let ang = (e.pos[1] - me.pos[1]).atan2(e.pos[0] - me.pos[0]);
        let mut w = (220.0 - d).max(20.0) / 100.0;
        if e.charging { w *= 3.8; }
        if e.visible_now { w *= 1.5; }
        if e.last_hit_us_t > obs.t - 2.5 { w *= 1.8; }
        threats.push((ang, w));
    }

    if threats.is_empty() {
        return None;
    }
    if threats.len() == 1 {
        return Some(threats[0].0);
    }

    let mut best_yaw = me.aim[0];
    let mut best_cost = f64::INFINITY;
    for k in 0..16 {
        let cyaw = -std::f64::consts::PI + (k as f64 / 16.0) * std::f64::consts::TAU;
        let mut cost = angle_diff(cyaw, me.aim[0]).abs() * 0.08;
        for &(tang, tw) in &threats {
            let err = angle_diff(cyaw, tang).abs();
            if err > half_front_arc - 0.06 {
                cost += tw * rear_mult;
            } else {
                cost += tw * (0.2 + 0.3 * (err / half_front_arc.max(0.1)));
            }
        }
        if cost < best_cost {
            best_cost = cost;
            best_yaw = cyaw;
        }
    }
    Some(best_yaw)
}

/// Cone-edge exclusion using full documented `cone_deg` (±30° default) + live obs.crates + 3D dist + LOS!
fn lightning_cone_aim(
    map: &ArenaMap,
    obs: &Observation,
    state: &WorldState,
    enemy_yaw: f64,
    enemy_dist3d: f64,
    light_cone: f64,
) -> f64 {
    let me = &obs.me;
    let my_muz = me.muzzle_pos();
    let mut blocker_angles: Vec<f64> = Vec::new();
    for c in &obs.crates {
        let cc = [c.pos[0], c.pos[1], c.pos[2] + 1.2];
        let d = dist3d(my_muz, cc);
        if d < enemy_dist3d - 0.5 && d > 1.0 {
            let yaw = (c.pos[1] - me.pos[1]).atan2(c.pos[0] - me.pos[0]);
            if angle_diff(yaw, enemy_yaw).abs() < light_cone + 0.05 && map.has_shot_los(my_muz, cc) {
                blocker_angles.push(yaw);
            }
        }
    }
    for n in state.npcs.values() {
        let nc = [n.pos[0], n.pos[1], n.pos[2] + 1.75];
        let d = dist3d(my_muz, nc);
        if d < enemy_dist3d - 0.5 && d > 1.0 {
            let yaw = (n.pos[1] - me.pos[1]).atan2(n.pos[0] - me.pos[0]);
            if angle_diff(yaw, enemy_yaw).abs() < light_cone + 0.05 && map.has_shot_los(my_muz, nc) {
                blocker_angles.push(yaw);
            }
        }
    }
    if blocker_angles.is_empty() {
        return enemy_yaw;
    }
    let max_shift = (light_cone - 0.08).max(0.0);
    let mut best_yaw = enemy_yaw;
    let mut best_clear = 0usize;
    for &frac in &[0.0, 0.35, -0.35, 0.60, -0.60, 0.80, -0.80] {
        let cand = wrap_angle(enemy_yaw + frac * max_shift);
        let clear = blocker_angles
            .iter()
            .filter(|&&ba| angle_diff(ba, cand).abs() > light_cone + 0.02)
            .count();
        if clear > best_clear {
            best_clear = clear;
            best_yaw = cand;
            if clear == blocker_angles.len() {
                break;
            }
        }
    }
    best_yaw
}

/// Core v3.1 Combat: retains v2.0's proven 80.5% laser lethality & full cadence, upgraded with OU lead,
/// shield-facing yaw, fresh beam counter-snipe, lightning cone exclusion, and Tier-1/2 kill confirmation!
#[allow(clippy::too_many_arguments)]
pub fn decide_combat(
    obs: &Observation,
    state: &mut WorldState,
    map: &ArenaMap,
    hold_fire_for_regen: bool,
    farm_crate_id: Option<i64>,
    farm_npc_id: Option<i64>,
    dodge: &DodgeOutput,
    dropped_slot: Option<usize>,
) -> CombatDecision {
    let me = &obs.me;
    let t = obs.t;
    let my_muzzle = me.muzzle_pos();
    let my_r = me.radius();
    let base_r = state.config.mech_f("radius", 2.5);
    let base_h = state.config.mech_f("height", 9.0);
    let spc = state.config.cubes.size_per_cube;

    let gun_slot = find_weapon_slot(me, "gun").filter(|s| Some(*s) != dropped_slot);
    let laser_slot = find_weapon_slot(me, "laser").filter(|s| Some(*s) != dropped_slot);
    let mortar_slot = find_weapon_slot(me, "mortar").filter(|s| Some(*s) != dropped_slot);
    let light_slot = find_weapon_slot(me, "lightning").filter(|s| Some(*s) != dropped_slot);

    let have_laser = laser_slot.and_then(|s| me.weapons.get(s)).and_then(|w| w.as_ref());
    let have_gun = gun_slot.and_then(|s| me.weapons.get(s)).and_then(|w| w.as_ref());
    let have_mortar = mortar_slot.and_then(|s| me.weapons.get(s)).and_then(|w| w.as_ref());
    let have_light = light_slot.and_then(|s| me.weapons.get(s)).and_then(|w| w.as_ref());

    let laser_charge = have_laser.and_then(|w| w.charge).unwrap_or(0.0);
    let laser_elapsed = state
        .my_laser_charge_start_t
        .map(|s| (t - s).max(laser_charge))
        .unwrap_or(laser_charge);
    let laser_full = have_laser.map(|w| w.stat("charge", 0.75)).unwrap_or(0.75);
    let laser_min = have_laser.map(|w| w.stat("min_charge", 0.15)).unwrap_or(0.15);
    let laser_hold = have_laser.map(|w| w.stat("hold_s", 2.0)).unwrap_or(2.0);
    let laser_cd = have_laser.map(|w| w.cooldown).unwrap_or(99.0);
    let laser_range = have_laser.map(|w| w.stat("range", 280.0)).unwrap_or(280.0);
    let laser_dmg_base = have_laser.map(|w| w.stat("damage", 315.0)).unwrap_or(315.0);
    let laser_min_frac = have_laser.map(|w| w.stat("min_frac", 0.35)).unwrap_or(0.35);
    let laser_cookoff = have_laser.map(|w| w.stat("cookoff", 0.6)).unwrap_or(0.6);

    let light_range = have_light.map(|w| w.stat("range", 45.0)).unwrap_or(45.0);
    let light_cone = have_light.map(|w| w.stat("cone_deg", 30.0)).unwrap_or(30.0).to_radians();
    let light_energy = have_light.map(|w| w.stat("energy", 3.0)).unwrap_or(3.0);
    let light_cd = have_light.map(|w| w.cooldown).unwrap_or(99.0);

    let gun_speed = have_gun.map(|w| w.stat("speed", 252.0)).unwrap_or(252.0);
    let gun_range = have_gun.map(|w| w.stat("range", 230.0)).unwrap_or(230.0);
    let gun_cd = have_gun.map(|w| w.cooldown).unwrap_or(99.0);
    let gun_reloading = have_gun.map(|g| g.reload.unwrap_or(0.0) > 0.0).unwrap_or(false);
    let gun_ready = have_gun
        .map(|g| g.ammo.unwrap_or(0.0) > 0.0 && g.reload.unwrap_or(0.0) <= 0.0)
        .unwrap_or(false);

    let mortar_speed = have_mortar.map(|w| w.stat("speed", 72.0)).unwrap_or(72.0);
    let mortar_grav = have_mortar.map(|w| w.stat("gravity", 22.0)).unwrap_or(22.0);
    let mortar_cd = have_mortar.map(|w| w.cooldown).unwrap_or(99.0);

    let dmg_mult = cube_damage_mult(me.cubes, &state.config);
    let est_instant_burst = if have_laser.is_some() && (laser_cd <= 0.05 || laser_charge > 0.0) {
        laser_dmg_base * dmg_mult * 0.92
    } else if have_light.is_some() && me.energy >= light_energy * 2.0 {
        220.0 * dmg_mult
    } else {
        160.0 * dmg_mult
    };

    let half_front_arc = state.config.mech_f("front_arc_deg", 180.0).to_radians() * 0.5;
    let rear_mult = state.config.mech_f("rear_mult", 1.5);
    let dt_sim = 0.05;

    // 1. Score all tracked enemies — keep ATTACKABLE (direct LOS) target strictly separate from behind-wall contacts! (Fixes P0-1)
    let mut best_attackable: Option<(i64, f64, [f64; 3])> = None;
    let mut best_move_enemy: Option<(i64, f64, Option<[f64; 3]>)> = None;
    let mut any_lightning_threat_close = false;
    let mut any_enemy_within_70 = false;
    let mut any_enemy_has_los = false;

    for e in state.enemies.values() {
        let age = t - e.last_seen_t;
        if age > 5.0 || (e.source_is_beam && age > 1.0) || (e.source_is_guess && age > 2.0) {
            continue;
        }
        let eh = e.height(base_h, spc);
        let ec = e.center(base_h, spc);
        let d = dist3d(my_muzzle, ec);
        if d < 70.0 { any_enemy_within_70 = true; }
        if e.visible_now && e.has_weapon("lightning") && d < 50.0 {
            any_lightning_threat_close = true;
        }
        if d > 285.0 { continue; }

        let can_check_los = e.visible_now || (e.source_is_beam && age < 0.40);
        let los_pt = if can_check_los {
            best_los_point(map, my_muzzle, e.pos, eh)
        } else {
            None
        };
        if los_pt.is_some() { any_enemy_has_los = true; }

        // FFA target filtering + third-party snipe (never drop target mid-charge!)
        let third_party_kill = e.hp <= est_instant_burst * 1.15
            || ((t - e.external_dmg_t) < 2.5 && e.hp <= est_instant_burst * 1.35);
        if state.is_ffa() && laser_charge <= 0.0 && obs.alive > 4 && !e.charging && e.last_hit_us_t < t - 4.0 && d > 115.0 && e.hp > e.max_hp * 0.72 && !third_party_kill {
            continue;
        }
        // Fable #3 (verified on real server stream.jsonl.gz): don't poke distant passive Practice bots that are ignoring us!
        let yaw_e_to_us_pre = (me.pos[1] - e.pos[1]).atan2(me.pos[0] - e.pos[0]);
        let passive_distant = !state.is_ffa()
            && laser_charge <= 0.0
            && e.last_hit_us_t < t - 5.0
            && !e.charging
            && e.hp > e.max_hp * 0.92
            && d > 115.0
            && angle_diff(e.aim[0], yaw_e_to_us_pre).abs() > 0.65;
        if passive_distant && have_laser.is_none() {
            continue; // Never wake a passive 150m Hard/Expert bot with gun chip while hunting for our Laser!
        }

        let mut score = 0.0;
        if los_pt.is_some() {
            score += if e.visible_now { 135.0 } else { 100.0 };
        } else if e.visible_now {
            score += 42.0;
        } else {
            score += 10.0 - age * 2.5;
        }
        score += (260.0 - d).max(0.0) * 0.55;
        let hp_frac_e = (e.hp / e.max_hp.max(1.0)).clamp(0.0, 1.0);
        score += (1.0 - hp_frac_e) * 165.0;
        if passive_distant {
            score -= 120.0; // Finish active hunters first before waking a passive bot!
        }
        if state.is_ffa() && third_party_kill {
            score += 95.0;
        }

        if e.hp <= est_instant_burst {
            score += 135.0;
        } else if e.hp <= 360.0 {
            score += 85.0;
        }
        // Practice anti-regen reset tag!
        if state.is_practice() && e.hp < e.max_hp - 180.0 {
            let since_hit = t - e.last_damaged_by_us_t;
            if (3.2..=4.9).contains(&since_hit) {
                score += 80.0;
            }
        }
        // Offensive Rear-Arc using exact configured front_arc_deg / 2 (90 deg, not 114.6 deg!)
        let yaw_e_to_us = (me.pos[1] - e.pos[1]).atan2(me.pos[0] - e.pos[0]);
        if angle_diff(e.aim[0], yaw_e_to_us).abs() > half_front_arc + 0.08 {
            score += 55.0;
        }

        if e.last_hit_us_t > t - 3.0 { score += 65.0; }
        if e.charging { score += 60.0; }
        if have_laser.is_some() && e.has_weapon("mortar") { score += 60.0; }
        if have_light.is_some() && e.has_weapon("laser") && d < light_range + 8.0 { score += 80.0; }
        if have_mortar.is_some() && e.has_weapon("lightning") && d < 90.0 { score += 45.0; }

        if let Some(lp) = los_pt {
            if best_attackable.map(|(_, bs, _)| score > bs).unwrap_or(true) {
                best_attackable = Some((e.id, score, lp));
            }
        }
        if best_move_enemy.map(|(_, bs, _)| score > bs).unwrap_or(true) {
            best_move_enemy = Some((e.id, score, los_pt));
        }
    }

    // Always attack a directly shootable enemy if one exists; never let a behind-wall low-HP mech suppress a clear shot!
    let best_enemy: Option<(i64, f64, Option<[f64; 3]>)> = match best_attackable {
        Some((id, sc, lp)) => Some((id, sc, Some(lp))),
        None => best_move_enemy,
    };

    if any_enemy_has_los {
        state.last_enemy_los_t = t;
    }

    let mut fire = [false, false];
    // Explicit charged-laser ownership (Fixes P0-3): DEFAULT fire[ls] = true whenever charging so NO fallback can accidentally fire into air!
    if let Some(ls) = laser_slot {
        if laser_charge > 0.0 {
            fire[ls] = true;
        }
    }
    let mut reload = false;
    let mut cmd_aim: Option<[f64; 2]> = None;
    let mut target_enemy_id = None;
    let mut target_has_los = false;
    let mut want_peek = false;

    if let Some(g) = have_gun {
        let ammo = g.ammo.unwrap_or(12.0);
        let rel = g.reload.unwrap_or(0.0);
        if rel <= 0.0 && ammo <= 3.0 && !any_enemy_has_los && (t - state.last_enemy_los_t) >= 1.0 {
            reload = true;
        }
    }

    let under_unseen_fire = (t - state.last_damaged_t) < 2.0
        && state.last_damaged_bearing.is_some()
        && state
            .last_damaged_by
            .map(|id| (0..100).contains(&id) && !state.enemies.get(&id).map(|e| e.visible_now).unwrap_or(false))
            .unwrap_or(false);

    // 2. Combat against selected enemy mech (Exact 2-tick firing model + exact 3D ray-cylinder verification!)
    if let Some((eid, _, los_pt)) = best_enemy {
        let e: EnemyTrack = state.enemies[&eid].clone();
        target_enemy_id = Some(e.id);
        let has_los = los_pt.is_some();
        target_has_los = has_los;
        let aim_pt = los_pt.unwrap_or_else(|| e.center(base_h, spc));
        let er = e.radius(base_r, spc);
        let eh = e.height(base_h, spc);
        let d = dist3d(my_muzzle, aim_pt);

        let allow_shoot = !hold_fire_for_regen || has_los || e.hp <= 360.0 || d < 48.0;

        let my_muz_t1 = [
            my_muzzle[0] + me.vel[0] * dt_sim,
            my_muzzle[1] + me.vel[1] * dt_sim,
            my_muzzle[2] + me.vel[2] * dt_sim,
        ];
        let my_muz_t2 = [
            my_muzzle[0] + me.vel[0] * (2.0 * dt_sim),
            my_muzzle[1] + me.vel[1] * (2.0 * dt_sim),
            my_muzzle[2] + me.vel[2] * (2.0 * dt_sim),
        ];
        let e_feet_t1 = [
            e.pos[0] + e.vel[0] * dt_sim,
            e.pos[1] + e.vel[1] * dt_sim,
            e.pos[2] + e.vel[2] * dt_sim,
        ];
        let lead_hitscan = [
            aim_pt[0] + e.vel[0] * dt_sim,
            aim_pt[1] + e.vel[1] * dt_sim,
            aim_pt[2] + e.vel[2] * dt_sim,
        ];
        let l_dx = lead_hitscan[0] - my_muz_t1[0];
        let l_dy = lead_hitscan[1] - my_muz_t1[1];
        let l_dz = lead_hitscan[2] - my_muz_t1[2];
        let hitscan_aim = [l_dy.atan2(l_dx), l_dz.atan2(l_dx.hypot(l_dy).max(1e-6))];

        // Exact first firing tick for Gun (Fixes P0-2: cd=0.04 fires tick 1, cd=0.09 fires tick 2!)
        let gun_fires_t1 = gun_ready && gun_cd <= dt_sim + 1e-4;
        let gun_fires_t2 = gun_ready && !gun_fires_t1 && gun_cd <= 2.0 * dt_sim + 1e-4;
        let gun_delay = if gun_fires_t1 { dt_sim } else { 2.0 * dt_sim };
        let (gun_aim, gun_pred, _) = lead_aim_damped(
            map, my_muzzle, me.vel, aim_pt, e.pos[2], e.vel, e.strafe_erratic, gun_speed, my_r + 0.5, gun_delay,
        );
        let gun_check_muz = if gun_fires_t1 { my_muz_t1 } else { my_muz_t2 };
        let gun_pred_los = has_los && map.has_shot_los(gun_check_muz, gun_pred);
        let desired_gun_aim = if gun_pred_los { gun_aim } else { hitscan_aim };

        let mut mortar_solution: Option<[f64; 2]> = None;
        if have_mortar.is_some() && allow_shoot && e.visible_now && mortar_cd <= 2.0 * dt_sim + 1e-4 && d >= 14.0 && d <= 230.0 {
            let prefer_high = !has_los;
            if let Some((m_aim, _ft)) = solve_mortar_arc(map, me, e.pos, e.vel, mortar_speed, mortar_grav, prefer_high) {
                mortar_solution = Some(m_aim);
            }
        }

        // Strict LOS-only laser start (validated by 144-match ablation: ground LOS + boost-charge beats blind pre-charging!)
        let close_range_speed_trap = d < 44.0
            && (gun_ready || (have_light.is_some() && me.energy >= light_energy))
            && e.hp > 420.0;
        let ducking_enemy_laser = dodge.urgency >= 2.5 && dodge.mortar_blast_pos.is_none();
        let laser_can_start = laser_slot.is_some()
            && allow_shoot
            && has_los
            && laser_cd <= dt_sim + 1e-4
            && !state.is_laser_shorted(t)
            && d <= laser_range
            && !any_lightning_threat_close
            && !close_range_speed_trap
            && !ducking_enemy_laser;

        let mortar_owns_aim = mortar_solution.is_some()
            && laser_charge <= 0.0
            && (!has_los
                || (e.has_weapon("lightning") && d < 78.0)
                || (have_laser.is_none() || laser_cd > 0.35));

        let light_active = light_slot.is_some()
            && allow_shoot
            && has_los
            && d <= light_range - 0.5
            && !mortar_owns_aim
            && me.energy >= light_energy;
        let light_fires_t1 = light_active && light_cd <= dt_sim + 1e-4;
        let light_fires_t2 = light_active && !light_fires_t1 && light_cd <= 2.0 * dt_sim + 1e-4;

        if laser_charge > 0.0 {
            cmd_aim = Some(hitscan_aim);
        } else if mortar_owns_aim {
            cmd_aim = mortar_solution;
        } else if (light_fires_t1 || light_fires_t2) && !laser_can_start {
            let clean_lyaw = lightning_cone_aim(map, obs, state, hitscan_aim[0], d, light_cone);
            cmd_aim = Some([clean_lyaw, hitscan_aim[1]]);
        } else if laser_can_start {
            cmd_aim = Some(hitscan_aim);
        } else if gun_ready && has_los && d <= gun_range {
            cmd_aim = Some(desired_gun_aim);
        } else if has_los {
            cmd_aim = Some(hitscan_aim);
        }

        let chosen_aim = cmd_aim.unwrap_or(hitscan_aim);
        let aim_t1 = slew_one_tick(
            me.aim, chosen_aim, me.turn_rate(), me.pitch_rate(), me.min_pitch(), me.max_pitch(),
        );
        let aim_t2 = slew_one_tick(
            aim_t1, chosen_aim, me.turn_rate(), me.pitch_rate(), me.min_pitch(), me.max_pitch(),
        );
        let shot_dir_t1 = aim_dir(aim_t1[0], aim_t1[1]);
        let shot_o_t1 = [
            my_muz_t1[0] + shot_dir_t1[0] * (my_r + 0.5),
            my_muz_t1[1] + shot_dir_t1[1] * (my_r + 0.5),
            my_muz_t1[2] + shot_dir_t1[2] * (my_r + 0.5),
        ];
        let barrel_clear_t1 = map.has_shot_los(my_muz_t1, shot_o_t1);

        // B1) LASER (Exact 3D ray-cylinder hit verification + rear-armor kill math + explicit ownership!)
        if let Some(ls) = laser_slot {
            if laser_charge > 0.0 {
                // Exact 3D ray-cylinder intersection along tick-1 actual aim (Fixes P1-1 280m 0.012-rad misses!)
                let exact_hit = exact_ray_hits_target(
                    map, my_muz_t1, my_r, aim_t1, e_feet_t1, er * 0.90, eh * 0.92, laser_range,
                );
                let frac = (laser_charge / laser_full.max(0.1)).clamp(0.0, 1.0);
                let cook_mult = if e.has_weapon("mortar") { 1.0 + laser_cookoff } else { 1.0 };
                let yaw_e_to_us = (me.pos[1] - e.pos[1]).atan2(me.pos[0] - e.pos[0]);
                let is_rear = angle_diff(e.aim[0], yaw_e_to_us).abs() > half_front_arc + 0.12;
                let r_mult = if is_rear { rear_mult } else { 1.0 };
                let est_dmg = laser_dmg_base * (laser_min_frac + (1.0 - laser_min_frac) * frac) * dmg_mult * cook_mult * r_mult;
                let full_dmg = laser_dmg_base * dmg_mult * cook_mult * r_mult;
                let armor_margin = if e.cubes > 0 { 1.24 } else { 1.04 };

                // Wait 1 extra tick for full 315 dmg if 0.70s (301 dmg) misses a kill threshold that full charge reaches! (Fixes P1-3)
                let need_full_for_kill = full_dmg >= e.hp * armor_margin && est_dmg < e.hp * armor_margin;
                let full_ready = laser_charge >= laser_full - 0.01 || (!need_full_for_kill && laser_charge >= laser_full - 0.06);
                let early_kill = laser_charge >= laser_min && est_dmg >= e.hp * armor_margin;
                let about_to_autofire = laser_elapsed >= laser_full + laser_hold - 0.15;

                if laser_charge >= laser_full - 0.16 {
                    want_peek = true;
                }

                if has_los && exact_hit && (full_ready || early_kill || about_to_autofire) {
                    fire[ls] = false; // EXPLICIT VERIFIED RELEASE -> HITSCAN BEAM HITS!
                } else {
                    fire[ls] = true; // KEEP HOLDING CHARGE!
                }
            } else if laser_can_start {
                fire[ls] = true;
            }
        }

        // B2) MORTAR
        if let Some(ms) = mortar_slot {
            if mortar_owns_aim {
                if let Some(m_aim) = mortar_solution {
                    let check_aim = if mortar_cd <= dt_sim + 1e-4 { aim_t1 } else { aim_t2 };
                    let dy = angle_diff(m_aim[0], check_aim[0]).abs();
                    let dp = (m_aim[1] - check_aim[1]).abs();
                    if dy < 0.028 && dp < 0.018 {
                        fire[ms] = true;
                    }
                }
            }
        }

        // B3) LIGHTNING (Full documented cone_deg = ±30° + exact firing tick! Fixes P1-4)
        if let Some(ts) = light_slot {
            if light_fires_t1 || light_fires_t2 {
                let check_aim = if light_fires_t1 { aim_t1 } else { aim_t2 };
                let yaw_err = angle_diff(hitscan_aim[0], check_aim[0]).abs();
                if yaw_err <= light_cone * 0.96 {
                    fire[ts] = true;
                }
            }
        }

        // B4) GUN (Validate strictly on the FIRST sim tick that will consume the held trigger! Fixes P0-2)
        if let Some(gs) = gun_slot {
            if allow_shoot && has_los && d <= gun_range && !mortar_owns_aim {
                if gun_fires_t1 && barrel_clear_t1 {
                    let hit1 = aim_hits_cylinder(aim_t1, desired_gun_aim, d, (er + 0.3) * 1.05, eh * 0.96);
                    let hit_dir = d < 32.0 && aim_hits_cylinder(aim_t1, hitscan_aim, d, (er + 0.3) * 1.05, eh * 0.96);
                    if hit1 || hit_dir {
                        fire[gs] = true;
                    }
                } else if gun_fires_t2 {
                    let sdir2 = aim_dir(aim_t2[0], aim_t2[1]);
                    let so2 = [
                        my_muz_t2[0] + sdir2[0] * (my_r + 0.5),
                        my_muz_t2[1] + sdir2[1] * (my_r + 0.5),
                        my_muz_t2[2] + sdir2[2] * (my_r + 0.5),
                    ];
                    if map.has_shot_los(my_muz_t2, so2) {
                        let hit2 = aim_hits_cylinder(aim_t2, desired_gun_aim, d, (er + 0.3) * 1.05, eh * 0.96);
                        let hit_dir2 = d < 32.0 && aim_hits_cylinder(aim_t2, hitscan_aim, d, (er + 0.3) * 1.05, eh * 0.96);
                        if hit2 || hit_dir2 {
                            fire[gs] = true;
                        }
                    }
                }
            }
        }
    }

    // 3. Unseen rear-attacker snap
    if under_unseen_fire && !target_has_los && cmd_aim.is_none() && laser_charge <= 0.0 && !fire[0] && !fire[1] {
        if let Some(b) = state.last_damaged_bearing {
            cmd_aim = Some([b, 0.0]);
        }
    }

    // 4a. ALWAYS defend against an aggroed NPC shooting us (Fixes 4/48 NPC deaths! Never drop a charged laser!)
    let npc_hit_us_recently = state.last_damaged_by.map(|id| (100..1000).contains(&id)).unwrap_or(false)
        && (t - state.last_damaged_t) < 2.5;
    let npc_aggro_active = laser_charge <= 0.0
        && farm_npc_id
            .and_then(|nid| state.npcs.get(&nid))
            .map(|n| dist2d([me.pos[0], me.pos[1]], [n.pos[0], n.pos[1]]) < 48.0 || npc_hit_us_recently)
            .unwrap_or(false);

    // 4b. Crate / NPC farming (NEVER interrupts an active charged laser!)
    let close_active_mech = target_enemy_id
        .and_then(|eid| state.enemies.get(&eid))
        .map(|e| target_has_los && dist3d(my_muzzle, e.center(base_h, spc)) < 80.0)
        .unwrap_or(false);
    let in_enemy_fight = laser_charge > 0.0
        || (target_enemy_id.is_some() && (close_active_mech || (!npc_aggro_active && (target_has_los || fire[0] || fire[1]))))
        || (under_unseen_fire && !npc_hit_us_recently)
        || (any_enemy_within_70 && !npc_aggro_active);

    if !in_enemy_fight && (!hold_fire_for_regen || npc_aggro_active) {
        if let Some(nid) = farm_npc_id {
            if let Some(npc) = state.npcs.get(&nid) {
                let nc = [npc.pos[0], npc.pos[1], npc.pos[2] + 1.75];
                let d = dist3d(my_muzzle, nc);
                if map.has_shot_los(my_muzzle, nc) && d < 220.0 {
                    let use_gun = gun_slot.is_some() && gun_ready && d < gun_range;
                    let use_light = !use_gun && light_slot.is_some() && d < light_range - 1.0 && me.energy > 30.0;
                    // Never start charging Laser on NPCs (prevents P0-1 charged-laser farming lockup & 380m beacon!)
                    if use_gun || use_light {
                        let naim = if use_gun {
                            lead_aim_damped(map, my_muzzle, me.vel, nc, npc.pos[2], npc.vel, 0.1, gun_speed, my_r + 0.5, 0.05).0
                        } else {
                            let n_next = [nc[0] + npc.vel[0] * 0.05, nc[1] + npc.vel[1] * 0.05, nc[2] + npc.vel[2] * 0.05];
                            let m_next = [my_muzzle[0] + me.vel[0] * 0.05, my_muzzle[1] + me.vel[1] * 0.05, my_muzzle[2] + me.vel[2] * 0.05];
                            let dx = n_next[0] - m_next[0];
                            let dy = n_next[1] - m_next[1];
                            let dz = n_next[2] - m_next[2];
                            [dy.atan2(dx), dz.atan2(dx.hypot(dy).max(1e-6))]
                        };
                        cmd_aim = Some(naim);
                        let nxt = slew_one_tick(me.aim, naim, me.turn_rate(), me.pitch_rate(), me.min_pitch(), me.max_pitch());
                        let sdir = aim_dir(nxt[0], nxt[1]);
                        let bclear = map.has_shot_los(my_muzzle, [my_muzzle[0] + sdir[0] * (my_r + 0.5), my_muzzle[1] + sdir[1] * (my_r + 0.5), my_muzzle[2] + sdir[2] * (my_r + 0.5)]);

                        if use_gun {
                            if let Some(gs) = gun_slot {
                                if bclear && aim_hits_cylinder(nxt, naim, d, 1.8, 3.5) { fire[gs] = true; }
                            }
                        } else if use_light {
                            if let Some(ts) = light_slot {
                                if angle_diff(naim[0], nxt[0]).abs() < light_cone * 0.45 { fire[ts] = true; }
                            }
                        }
                    }
                }
            }
        }
        if cmd_aim.is_none() {
            if let Some(cid) = farm_crate_id {
                if let Some(cr) = state.crates.get(&cid) {
                    let cc = [cr.pos[0], cr.pos[1], cr.pos[2] + 1.2];
                    let d = dist3d(my_muzzle, cc);
                    if map.has_shot_los(my_muzzle, cc) && d < 140.0 {
                        let use_gun = gun_slot.is_some() && gun_ready && d < gun_range;
                        let use_light = !use_gun && light_slot.is_some() && d < light_range - 1.0 && me.energy > 35.0;
                        // Never start charging Laser on crates (prevents P0-1 charged-laser farming lockup & 380m beacon!)
                        if use_gun || use_light {
                            let c_dx = cc[0] - my_muzzle[0];
                            let c_dy = cc[1] - my_muzzle[1];
                            let c_dz = cc[2] - my_muzzle[2];
                            let caim = [c_dy.atan2(c_dx), c_dz.atan2(c_dx.hypot(c_dy).max(1e-6))];
                            cmd_aim = Some(caim);
                            let nxt = slew_one_tick(me.aim, caim, me.turn_rate(), me.pitch_rate(), me.min_pitch(), me.max_pitch());
                            let sdir = aim_dir(nxt[0], nxt[1]);
                            let bclear = map.has_shot_los(my_muzzle, [my_muzzle[0] + sdir[0] * (my_r + 0.5), my_muzzle[1] + sdir[1] * (my_r + 0.5), my_muzzle[2] + sdir[2] * (my_r + 0.5)]);
                            if use_gun {
                                if let Some(gs) = gun_slot {
                                    if bclear && aim_hits_cylinder(nxt, caim, d, 1.4, 2.4) { fire[gs] = true; }
                                }
                            } else if use_light {
                                if let Some(ts) = light_slot {
                                    if angle_diff(caim[0], nxt[0]).abs() < light_cone * 0.45 { fire[ts] = true; }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 5. Shield-Facing Yaw when not actively aiming at a target; unblock 360-deg scan when hunting Laser!
    if cmd_aim.is_none() {
        let safe_scan = have_laser.is_none() && !any_enemy_has_los && !any_enemy_within_70
            && (t - state.last_damaged_t) > 2.2 && dodge.mortar_blast_pos.is_none();
        if !safe_scan {
            if let Some(syaw) = compute_shield_yaw(obs, state, dodge) {
                cmd_aim = Some([syaw, -0.02]);
            } else {
                let max_sound_d = if have_laser.is_some() { 120.0 } else { 55.0 };
                if let Some(s) = state.sounds.iter().rev().find(|s| t - s.t < 1.5 && s.dist_est < max_sound_d) {
                    cmd_aim = Some([s.bearing, 0.0]);
                }
            }
        }
    }

    CombatDecision {
        aim: cmd_aim,
        fire,
        reload,
        target_enemy_id,
        target_has_los,
        charging_laser: laser_charge > 0.0 || laser_slot.map(|ls| fire[ls]).unwrap_or(false),
        laser_ready_soon: laser_slot.is_some() && (laser_charge >= laser_full - 0.35 || laser_cd <= 0.25),
        laser_cd,
        want_peek,
        gun_reloading,
    }
}
