"""
GMUNVIS-Apex: Official Entry Bot for Mech Royale.
Fresh, modular, high-performance Python bot implementing Huy's Survival Plan.
"""
from __future__ import annotations
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import mechfight as mf
from config import (
    BOT_NAME,
    CAMP_ARRIVE_DIST,
    SILENT_SPEED_LIMIT,
    SILENT_MOVE_CLAMP,
    OPTIMAL_LASER_DIST,
    OPTIMAL_LIGHTNING_DIST,
    OPTIMAL_GUN_DIST,
)
from map_geom import load_map_geometry, has_line_of_sight
from aim import angle_diff, clamp, solve_gun_lead, solve_laser_aim, is_aim_aligned
from weapons import WeaponManager
from survival import SurvivalController
from navigation import Navigator


class Bot:
    name = BOT_NAME

    def __init__(self, hello: Optional[mf.Hello] = None):
        if hello is None:
            hello = {}

        load_map_geometry(hello)
        self.slot = hello.get("slot", 0)
        self.cfg = hello.get("config", {})
        self.weapons_cfg = self.cfg.get("weapons", {})
        self.gun_speed = self.weapons_cfg.get("gun", {}).get("speed", 252.0)
        self.gun_range = self.weapons_cfg.get("gun", {}).get("range", 230.0)

        self.weapon_mgr = WeaponManager(pickup_radius=4.0)
        self.survival = SurvivalController()
        self.nav = Navigator()

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        t_now = float(obs.get("t", 0.0))
        action: mf.Action = {"tick": tick}

        me = obs.get("self")
        if not me:
            return action

        x, y, z = me["pos"]
        my_pos = (x, y, z)
        torso_yaw, torso_pitch = me.get("aim", [0.0, 0.0])
        hp = float(me.get("hp", 1400.0))
        max_hp = float(me.get("max_hp", 1400.0))
        energy = float(me.get("energy", 100.0))
        in_zone = bool(me.get("in_zone", True))
        grounded = bool(me.get("grounded", True))
        alive_count = int(obs.get("alive", 16))
        weapons = me.get("weapons", [None, None])
        muzzle_h = float(me.get("stats", {}).get("muzzle_height", 7.0))
        muzzle_pos = (x, y, z + muzzle_h)

        # -------------------------------------------------------------
        # 1. PROCESS EVENTS
        # -------------------------------------------------------------
        events = obs.get("events", [])
        self.survival.process_events(events, t_now)

        # -------------------------------------------------------------
        # 2. UPGRADE SELECTION (SURVIVAL & DEFENSE FIRST)
        # -------------------------------------------------------------
        offer = obs.get("offer")
        if offer and "choices" in offer:
            action["pick"] = self.survival.pick_upgrade(offer["choices"])

        # -------------------------------------------------------------
        # 3. WEAPON PICKUPS & HARD MORTAR BAN
        # -------------------------------------------------------------
        pickups = obs.get("pickups", [])
        drop_slot, take_id = self.weapon_mgr.evaluate_pickups(my_pos, pickups, weapons)
        if drop_slot is not None:
            action["drop"] = drop_slot
        if take_id is not None:
            action["take"] = take_id

        # -------------------------------------------------------------
        # 4. AUDIO ANALYSIS & DEAD-SIDE DETECTION
        # -------------------------------------------------------------
        danger_vx = 0.0
        danger_vy = 0.0
        sounds = obs.get("sounds", [])
        for s in sounds:
            skind = s.get("kind", "")
            if skind in ("gun", "laser", "mortar", "explosion", "lightning"):
                sb = s.get("bearing")
                if sb is not None:
                    s_dist = s.get("dist", "mid")
                    w = 3.0 if s_dist == "near" else (1.5 if s_dist == "mid" else 0.5)
                    danger_vx += w * math.cos(sb)
                    danger_vy += w * math.sin(sb)

        dead_side_ang: Optional[float] = None
        if math.hypot(danger_vx, danger_vy) > 0.5:
            danger_ang = math.atan2(danger_vy, danger_vx)
            dead_side_ang = angle_diff(danger_ang, math.pi)

        # -------------------------------------------------------------
        # 5. ZONE INFORMATION & ENDGAME STATUS
        # -------------------------------------------------------------
        zone = obs.get("zone", {})
        zc = zone.get("center", [0.0, 0.0])
        zr = float(zone.get("radius", 560.0))
        znc = zone.get("next_center", zc)
        znr = float(zone.get("next_radius", zr))
        is_endgame = self.survival.is_endgame(alive_count, zr)

        # -------------------------------------------------------------
        # 6. THREAT SELECTION
        # -------------------------------------------------------------
        visible_mechs = [m for m in obs.get("mechs", []) if m.get("kind") == "mech"]
        target: Optional[Dict[str, Any]] = None
        best_target_score = -99999.0

        for m in visible_mechs:
            mx, my, mz = m["pos"]
            dist_xy = math.hypot(mx - x, my - y)
            if dist_xy > 240.0:
                continue

            # Must have line-of-sight to target
            if not has_line_of_sight(x, y, mx, my):
                continue

            m_hp = float(m.get("hp", 1400.0))
            m_weapons = m.get("weapons", [])

            # Target score: priority to low HP (quick kills) and close threats
            score = 300.0 - (dist_xy * 1.2) + ((1400.0 - m_hp) * 0.22)
            if m_hp < 280.0:
                score += 120.0  # Lethal finish priority
            if m.get("charging", False):
                score += 60.0   # Interrupt charging laser
            if "laser" in m_weapons:
                score += 30.0

            # Third-party vulture rule: if multiple enemies visible, prioritize the weakest
            if len(visible_mechs) >= 2 and m_hp < 200.0:
                score += 80.0

            if score > best_target_score:
                best_target_score = score
                target = m

        # -------------------------------------------------------------
        # 7. TACTICAL RETREAT WITH HYSTERESIS
        # -------------------------------------------------------------
        is_retreating = self.survival.update_retreat_state(hp, max_hp, len(visible_mechs))

        # -------------------------------------------------------------
        # 8. AIMING, BALLISTICS & WEAPON EXECUTION
        # -------------------------------------------------------------
        aim_yaw = torso_yaw
        aim_pitch = 0.0
        fire0 = False
        fire1 = False
        dist_to_target = 999.0

        has_sec = len(weapons) > 1 and weapons[1] is not None
        sec_kind = weapons[1].get("kind") if has_sec else None

        if target:
            tx, ty, tz = target["pos"]
            tvx, tvy, tvz = target.get("vel", [0.0, 0.0, 0.0])
            dist_to_target = math.hypot(tx - x, ty - y)
            target_hp = float(target.get("hp", 1400.0))
            target_pos = (tx, ty, tz + 4.5)
            target_vel = (tvx, tvy, tvz)

            # Check if target is facing me
            t_yaw = target.get("aim", [0.0, 0.0])[0]
            bearing_to_me = math.atan2(y - ty, x - tx)
            target_is_facing_me = abs(angle_diff(t_yaw, bearing_to_me)) < 0.40

            # Solve lead aim based on equipped weapon
            if has_sec and sec_kind == "laser":
                aim_yaw, aim_pitch, pred_pos = solve_laser_aim(muzzle_pos, target_pos, target_vel)
            else:
                aim_yaw, aim_pitch, pred_pos = solve_gun_lead(muzzle_pos, target_pos, target_vel, self.gun_speed)

            # Angular error check
            aligned = is_aim_aligned((torso_yaw, torso_pitch), (aim_yaw, aim_pitch), dist_to_target)

            # Stealth trigger discipline
            should_stealth_fire = self.survival.can_stealth_fire(
                target_hp, dist_to_target, t_now, is_endgame, is_facing_me=target_is_facing_me
            )

            # If actively retreating behind cover, hold fire once LOS is broken
            if is_retreating and not has_line_of_sight(x, y, tx, ty):
                should_stealth_fire = False

            yaw_err = abs(angle_diff(torso_yaw, aim_yaw))
            fire0, fire1 = self.weapon_mgr.compute_fire_action(
                current_weapons=weapons,
                target_hp=target_hp,
                dist=dist_to_target,
                is_aligned=aligned,
                has_los=True,
                should_stealth_fire=should_stealth_fire,
                energy=energy,
                yaw_err=yaw_err,
            )

        elif not is_retreating and obs.get("crates") and hp >= (max_hp * 0.65) and (t_now - self.survival.last_damage_taken_t) > 5.0 and not any(s.get("dist") == "near" for s in sounds) and not any(math.hypot(m["pos"][0] - x, m["pos"][1] - y) < 60.0 for m in visible_mechs):
            # Safe Crate Targeting (Early Economy: harvest laser/lightning/cubes from crates)
            nearby_crates = []
            for c in obs["crates"]:
                cx, cy, cz = c["pos"]
                cdist = math.hypot(cx - x, cy - y)
                if cdist <= 40.0 and has_line_of_sight(x, y, cx, cy):
                    nearby_crates.append((cdist, c))
            if nearby_crates:
                nearby_crates.sort(key=lambda item: item[0])
                crate_target = nearby_crates[0][1]
                cx, cy, cz = crate_target["pos"]
                aim_yaw = math.atan2(cy - y, cx - x)
                aim_pitch = clamp(math.atan2(cz - (z + muzzle_h), max(1.0, math.hypot(cx - x, cy - y))), -0.7, 1.3)
                yaw_err = abs(angle_diff(torso_yaw, aim_yaw))
                gun = weapons[0] if weapons else None
                if yaw_err < 0.12 and gun and gun.get("cooldown", 0.0) <= 0.02:
                    fire0 = True

        else:
            # No target in LOS: point torso toward recent damage bearing or scan outward
            if self.survival.recent_attacker_bearing is not None and (t_now - self.survival.last_damage_taken_t) < 4.0:
                aim_yaw = self.survival.recent_attacker_bearing

        action["aim"] = [aim_yaw, aim_pitch]
        action["fire"] = [fire0, fire1]

        # -------------------------------------------------------------
        # 9. NAVIGATION, COVER & SURVIVAL POSITIONING
        # -------------------------------------------------------------
        action["boost"] = False
        action["jump"] = False
        move_vx = 0.0
        move_vy = 0.0

        d_curr_center = math.hypot(x - zc[0], y - zc[1])
        zone_margin = zr - d_curr_center
        is_silent = self.survival.is_enemy_nearby(my_pos, visible_mechs, sounds)

        # Mode A: Emergency Safe Zone Recovery
        if not in_zone or zone_margin < 20.0:
            move_vx, move_vy = self.nav.compute_steer(my_pos, (znc[0], znc[1]), (zc[0], zc[1]), zr, is_silent=False, speed_mult=1.0)
            action["boost"] = True

        # Mode B: Tactical Retreat & Evasion (Decoupled legs away from threat)
        elif is_retreating:
            camp_spot = self.survival.get_camp_spot(my_pos, (znc[0], znc[1]), znr, dead_side_ang, t_now)
            dest = camp_spot if camp_spot is not None else (znc[0], znc[1])
            move_vx, move_vy = self.nav.compute_steer(my_pos, dest, (zc[0], zc[1]), zr, is_silent=False, speed_mult=1.0)
            # Boost if low HP to break line of sight
            if hp < (max_hp * 0.50):
                action["boost"] = energy > 20.0

        # Mode C: Active Combat Spacing & Aim Breaking
        elif target:
            tx, ty, _ = target["pos"]
            edx = tx - x
            edy = ty - y
            edist = max(1.0, math.hypot(edx, edy))
            dir_x = edx / edist
            dir_y = edy / edist

            optimal_dist = OPTIMAL_LASER_DIST if (has_sec and sec_kind == "laser") else (
                OPTIMAL_LIGHTNING_DIST if (has_sec and sec_kind == "lightning") else OPTIMAL_GUN_DIST
            )

            if edist < optimal_dist:
                kite_mult = 1.6 if hp < (max_hp * 0.6) else 1.1
                move_vx -= dir_x * kite_mult
                move_vy -= dir_y * kite_mult
            elif edist > (optimal_dist + 25.0) and in_zone and zone_margin > 30.0:
                move_vx += dir_x * 0.6
                move_vy += dir_y * 0.6

            # Aim breaking: periodic strafe flips every 0.7s to break enemy predictive lead
            strafe_phase = int(t_now / 0.7) % 2
            strafe_mult = 1.0 if strafe_phase == 0 else -1.0
            move_vx += (-dir_y * strafe_mult) * 0.95
            move_vy += (dir_x * strafe_mult) * 0.95

            if is_endgame and edist > (optimal_dist + 30.0):
                action["boost"] = energy > 30.0
            elif edist > (optimal_dist + 20.0):
                move_vx += dir_x * 0.8
                move_vy += dir_y * 0.8

            # Aim breaking: periodic strafe flips every 0.7s
            strafe_phase = int(t_now / 0.7) % 2
            strafe_mult = 1.0 if strafe_phase == 0 else -1.0
            move_vx += (-dir_y * strafe_mult) * 0.9
            move_vy += (dir_x * strafe_mult) * 0.9

        # Mode D: Safe Farming (Harvest crates/cubes when standoff distance > 150m)
        elif not target and obs.get("cubes") and zone_margin > 30.0 and self.survival.can_safely_farm(my_pos, my_pos, visible_mechs, sounds):
            cubes = obs["cubes"]
            safe_cubes = [c for c in cubes if has_line_of_sight(x, y, c["pos"][0], c["pos"][1])]
            if safe_cubes:
                safe_cubes.sort(key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                closest_cube = safe_cubes[0]
                cx, cy, _ = closest_cube["pos"]
                if math.hypot(cx - x, cy - y) <= 40.0:
                    move_vx, move_vy = self.nav.compute_steer(my_pos, (cx, cy), (zc[0], zc[1]), zr, is_silent=is_silent)

        # Mode E: Camp Anchoring in Outer Ring (Huy's Point 1 & Point 5)
        else:
            camp_spot = self.survival.get_camp_spot(my_pos, (znc[0], znc[1]), znr, dead_side_ang, t_now)
            if camp_spot:
                cx, cy = camp_spot
                d_to_camp = math.hypot(cx - x, cy - y)
                if d_to_camp > CAMP_ARRIVE_DIST:
                    # Pre-rotate to camp spot; boost sprint if early match
                    move_vx, move_vy = self.nav.compute_steer(my_pos, (cx, cy), (zc[0], zc[1]), zr, is_silent=is_silent)
                    if t_now < 12.0 and not is_silent:
                        action["boost"] = energy > 30.0
                else:
                    # ANCHORED: Stationary = 0 dB sound emission!
                    move_vx = 0.0
                    move_vy = 0.0
            else:
                move_vx = 0.0
                move_vy = 0.0

        # Active Projectile / Laser Dodging
        for proj in obs.get("projectiles", []):
            px, py, _ = proj.get("pos", [0, 0, 0])
            pvx, pvy, _ = proj.get("vel", [0, 0, 0])
            p_dist = math.hypot(px - x, py - y)

            if p_dist < 60.0:
                p_speed = math.hypot(pvx, pvy)
                if p_speed > 10.0:
                    to_me_x = x - px
                    to_me_y = y - py
                    dot = to_me_x * pvx + to_me_y * pvy
                    if dot > 0:
                        perp_x = -pvy / p_speed
                        perp_y = pvx / p_speed
                        move_vx += perp_x * 2.0
                        move_vy += perp_y * 2.0
                        if proj.get("kind") == "mortar" and p_dist < 18.0 and grounded:
                            action["jump"] = True

        for m in visible_mechs:
            if m.get("charging", False):
                mx, my, _ = m["pos"]
                m_yaw = m.get("aim", [0.0, 0.0])[0]
                bearing_to_me = math.atan2(y - my, x - mx)
                if abs(angle_diff(m_yaw, bearing_to_me)) < 0.15:
                    # Enemy laser aimed directly at us: emergency sideways dodge
                    strafe_yaw = m_yaw + (math.pi * 0.5)
                    move_vx += math.cos(strafe_yaw) * 2.0
                    move_vy += math.sin(strafe_yaw) * 2.0
                    action["boost"] = energy > 20.0

        # Unstick Watchdog
        is_moving = math.hypot(move_vx, move_vy) > 0.1
        stuck_jump, _ = self.nav.check_unstick(my_pos, grounded, is_moving)
        if stuck_jump:
            action["jump"] = True

        # Final move vector normalization
        v_mag = math.hypot(move_vx, move_vy)
        if v_mag > 1e-4:
            max_len = SILENT_MOVE_CLAMP if is_silent else 1.0
            norm = min(max_len, v_mag)
            action["move"] = [(move_vx / v_mag) * norm, (move_vy / v_mag) * norm]
        else:
            action["move"] = [0.0, 0.0]

        return action


if __name__ == "__main__":
    mf.run(Bot)
