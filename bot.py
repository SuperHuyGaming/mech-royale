"""
Main Submission Bot for Mech Royale (Builders Buildathon).
Team: GMUNVIS
Bot Name: GMUNVIS-Apex

Complete, self-contained rule-based heuristic bot:
1. Threat Evaluation: Prioritizes vulnerable (low HP, rear exposed) and immediate threats.
2. Predictive Ballistic Aiming: Computes bullet lead time (speed 252 m/s) and muzzle elevation.
3. Trigger Discipline: Only fires when torso aim is aligned to prevent ammo waste & reload downtime.
4. Weapon Triangle Control:
   - Laser: Charges up to 0.7s, releases for instant 315 damage burst; cookoff bonus vs mortar.
   - Lightning: Auto-fire in 30° cone within 44m; shorts enemy lasers.
   - Mortar: Trajectory lob over medium range.
5. Defensive Positioning & Rear Protection:
   - Maintains safe zone buffer without rushing dead center (where house bots swarm).
   - Uncoupled torso/leg motion: always keeps torso facing threats to avoid 1.5x rear damage.
   - HP-critical disengage: stops firing and retreats to trigger 45 HP/s passive regeneration.
6. Active Dodging:
   - Detects incoming bullets/shells and applies perpendicular evasive strafe.
   - Evades charging enemy lasers.
7. Strategic Looting & Smart Upgrade Selection:
   - Prioritizes armor, max HP, regen, and matching weapon buffs.
   - Harvests power cubes and safe crates.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple
import mechfight as mf


def angle_diff(a: float, b: float) -> float:
    """Signed shortest difference between two angles in radians [-pi, pi]."""
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def clamp(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


class Bot:
    name = "GMUNVIS-Apex"

    def __init__(self, hello: mf.Hello):
        self.slot = hello.get("slot", 0)
        self.cfg = hello.get("config", {})
        self.mech_cfg = self.cfg.get("mech", {})
        self.weapons_cfg = self.cfg.get("weapons", {})
        self.loot_cfg = self.cfg.get("loot", {})

        self.gun_speed = self.weapons_cfg.get("gun", {}).get("speed", 252.0)
        self.gun_range = self.weapons_cfg.get("gun", {}).get("range", 230.0)
        self.pickup_radius = self.loot_cfg.get("pickup_radius", 4.0)

        # Internal state
        self.last_aim_yaw = 0.0
        self.last_aim_pitch = 0.0

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        action: mf.Action = {"tick": tick}
        me = obs.get("self")
        if not me:
            return action

        x, y, z = me["pos"]
        torso_yaw, torso_pitch = me.get("aim", [0.0, 0.0])
        hp = me.get("hp", 1400.0)
        max_hp = me.get("max_hp", 1400.0)
        energy = me.get("energy", 100.0)
        in_zone = me.get("in_zone", True)
        grounded = me.get("grounded", True)
        weapons = me.get("weapons", [None, None])
        muzzle_h = me.get("stats", {}).get("muzzle_height", 7.0)

        # -------------------------------------------------------------
        # 1. SMART UPGRADE SELECTION
        # -------------------------------------------------------------
        offer = obs.get("offer")
        if offer and "choices" in offer:
            choices = offer["choices"]
            best_idx = 0
            best_score = -999.0
            held_secondary = weapons[1]["kind"] if len(weapons) > 1 and weapons[1] else None

            for i, ch in enumerate(choices):
                cid = ch.get("id", "").lower()
                desc = ch.get("desc", "").lower()
                score = 0.0

                if "armor" in cid or "armor" in desc:
                    score = 100.0  # -8% damage taken is critical for survival
                elif "hp" in cid or "health" in desc:
                    score = 80.0
                elif "regen" in cid:
                    score = 70.0
                elif "gun" in cid or "rotary" in cid:
                    score = 65.0
                elif held_secondary and (held_secondary in cid or held_secondary in desc):
                    score = 60.0
                elif "sensor" in cid:
                    score = 40.0
                elif "speed" in cid or "boost" in cid:
                    score = 35.0
                else:
                    score = 10.0

                if score > best_score:
                    best_score = score
                    best_idx = i

            action["pick"] = best_idx

        # -------------------------------------------------------------
        # 2. WEAPON LOOTING
        # -------------------------------------------------------------
        weapon_weights = {"lightning": 3, "laser": 2, "mortar": 1}
        has_secondary = len(weapons) > 1 and weapons[1] is not None
        pickups = obs.get("pickups", [])

        if not has_secondary and pickups:
            best_pickup = None
            best_weight = -1
            for p in pickups:
                p_kind = p.get("kind", "")
                p_pos = p.get("pos", [0, 0, 0])
                d_horiz = math.hypot(p_pos[0] - x, p_pos[1] - y)
                d_vert = abs(p_pos[2] - z)
                if d_horiz <= self.pickup_radius and d_vert <= 4.0:
                    w = weapon_weights.get(p_kind, 0)
                    if w > best_weight:
                        best_weight = w
                        best_pickup = p

            if best_pickup:
                action["take"] = best_pickup["id"]

        # -------------------------------------------------------------
        # 3. THREAT EVALUATION & TARGET SELECTION
        # -------------------------------------------------------------
        visible_mechs = [m for m in obs.get("mechs", []) if m.get("kind") == "mech"]
        target = None
        best_target_score = -9999.0

        for m in visible_mechs:
            mx, my, mz = m["pos"]
            dist = math.hypot(mx - x, my - y)
            m_hp = m.get("hp", 1400.0)
            m_weapons = m.get("weapons", [])

            score = 250.0 - (dist * 0.8) + ((1400.0 - m_hp) * 0.15)
            if m_hp < 250.0:
                score += 80.0
            if has_secondary and weapons[1]["kind"] == "laser" and "mortar" in m_weapons:
                score += 50.0
            if m.get("charging", False):
                score += 40.0

            m_yaw = m.get("aim", [0.0, 0.0])[0]
            bearing_to_me = math.atan2(y - my, x - mx)
            facing_diff = abs(angle_diff(m_yaw, bearing_to_me))
            if facing_diff > math.pi * 0.5:
                score += 35.0  # Rear exposure bonus (1.5x damage)

            if score > best_target_score:
                best_target_score = score
                target = m

        crate_target = None
        if not target and obs.get("crates"):
            nearby_crates = [c for c in obs["crates"] if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < 40.0]
            if nearby_crates:
                nearby_crates.sort(key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                crate_target = nearby_crates[0]

        # -------------------------------------------------------------
        # 4. PREDICTIVE AIMING & BALLISTIC LEAD
        # -------------------------------------------------------------
        aim_yaw = torso_yaw
        aim_pitch = 0.0
        fire_slot0 = False
        fire_slot1 = False

        if target:
            tx, ty, tz = target["pos"]
            tvx, tvy, tvz = target.get("vel", [0.0, 0.0, 0.0])
            dist_xy = math.hypot(tx - x, ty - y)

            t_flight = dist_xy / max(1.0, self.gun_speed)
            pred_tx = tx + tvx * t_flight
            pred_ty = ty + tvy * t_flight
            target_center_z = tz + 4.5 + tvz * t_flight
            pred_tz_diff = target_center_z - (z + muzzle_h)

            aim_yaw = math.atan2(pred_ty - y, pred_tx - x)
            aim_pitch = math.atan2(pred_tz_diff, max(1.0, dist_xy))
            aim_pitch = clamp(aim_pitch, -0.7, 1.3)

            yaw_err = abs(angle_diff(torso_yaw, aim_yaw))
            pitch_err = abs(torso_pitch - aim_pitch)
            aligned = (yaw_err < 0.14) and (pitch_err < 0.18)

            disengage_for_heal = (hp < 600.0) and (dist_xy > 40.0)

            if aligned and not disengage_for_heal and dist_xy <= self.gun_range:
                fire_slot0 = True

            if has_secondary and not disengage_for_heal:
                sec = weapons[1]
                sec_kind = sec.get("kind")
                if sec_kind == "lightning":
                    if dist_xy <= 44.0 and yaw_err < 0.50:
                        fire_slot1 = True
                elif sec_kind == "laser":
                    current_charge = sec.get("charge", 0.0)
                    if dist_xy <= 260.0:
                        fire_slot1 = True if current_charge < 0.65 else (not aligned)
                elif sec_kind == "mortar":
                    if 35.0 <= dist_xy <= 180.0 and yaw_err < 0.20:
                        fire_slot1 = True

            mf.draw.line([x, y, z + muzzle_h], [pred_tx, pred_ty, target_center_z], "#ff4444")
            mf.draw.label(f"HP:{hp:.0f} | Target:#{int(target['id'])} Dist:{dist_xy:.1f}m")

        elif crate_target:
            cx, cy, cz = crate_target["pos"]
            aim_yaw = math.atan2(cy - y, cx - x)
            aim_pitch = math.atan2(cz - (z + muzzle_h), max(1.0, math.hypot(cx - x, cy - y)))
            yaw_err = abs(angle_diff(torso_yaw, aim_yaw))
            if yaw_err < 0.15:
                fire_slot0 = True
            mf.draw.label(f"HP:{hp:.0f} | Harvesting Crate")
        else:
            mf.draw.label(f"HP:{hp:.0f} | Scanning Area")

        action["aim"] = [aim_yaw, aim_pitch]
        action["fire"] = [fire_slot0, fire_slot1]

        # -------------------------------------------------------------
        # 5. TACTICAL MOVEMENT & POSITIONING
        # -------------------------------------------------------------
        move_vx = 0.0
        move_vy = 0.0
        zone = obs.get("zone", {})
        z_curr_center = zone.get("center", [0.0, 0.0])
        z_curr_radius = zone.get("radius", 500.0)
        z_next_center = zone.get("next_center", z_curr_center)
        z_next_radius = zone.get("next_radius", z_curr_radius)

        dist_to_curr_center = math.hypot(x - z_curr_center[0], y - z_curr_center[1])
        dist_to_next_center = math.hypot(x - z_next_center[0], y - z_next_center[1])

        margin = z_curr_radius - dist_to_curr_center
        if not in_zone or margin < 25.0:
            to_next_x = z_next_center[0] - x
            to_next_y = z_next_center[1] - y
            d_next = max(1.0, dist_to_next_center)
            move_vx += (to_next_x / d_next) * 2.5
            move_vy += (to_next_y / d_next) * 2.5
            action["boost"] = True
        else:
            action["boost"] = False
            desired_radius = z_next_radius * 0.55
            radius_diff = dist_to_next_center - desired_radius
            if abs(radius_diff) > 20.0 and dist_to_next_center > 1.0:
                dir_x = (z_next_center[0] - x) / dist_to_next_center
                dir_y = (z_next_center[1] - y) / dist_to_next_center
                if radius_diff > 0:
                    move_vx += dir_x * 0.8
                    move_vy += dir_y * 0.8
                else:
                    move_vx -= dir_x * 0.6
                    move_vy -= dir_y * 0.6

        # Combat Spacing / Kiting:
        if target:
            tx, ty, tz = target["pos"]
            edx = tx - x
            edy = ty - y
            edist = max(1.0, math.hypot(edx, edy))
            dir_to_enemy_x = edx / edist
            dir_to_enemy_y = edy / edist

            has_lightning = has_secondary and weapons[1]["kind"] == "lightning"
            optimal_dist = 32.0 if has_lightning else 60.0

            if edist < optimal_dist:
                kite_weight = 1.6 if hp < 800.0 else 1.0
                move_vx -= dir_to_enemy_x * kite_weight
                move_vy -= dir_to_enemy_y * kite_weight
            elif edist > (optimal_dist + 30.0) and in_zone and margin > 40.0:
                move_vx += dir_to_enemy_x * 0.5
                move_vy += dir_to_enemy_y * 0.5

        # -------------------------------------------------------------
        # 6. ACTIVE DODGING
        # -------------------------------------------------------------
        projectiles = obs.get("projectiles", [])
        for proj in projectiles:
            px, py, _ = proj.get("pos", [0, 0, 0])
            pvx, pvy, _ = proj.get("vel", [0, 0, 0])
            p_dist = math.hypot(px - x, py - y)

            if p_dist < 60.0:
                p_speed = math.hypot(pvx, pvy)
                if p_speed > 10.0:
                    to_me_x = x - px
                    to_me_y = y - py
                    dot = (to_me_x * pvx + to_me_y * pvy)
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
                    strafe_yaw = m_yaw + (math.pi * 0.5)
                    move_vx += math.cos(strafe_yaw) * 2.2
                    move_vy += math.sin(strafe_yaw) * 2.2
                    action["boost"] = energy > 25.0

        if not target and obs.get("cubes") and margin > 30.0:
            cubes = obs["cubes"]
            cubes.sort(key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
            nearest_cube = cubes[0]
            cx, cy, _ = nearest_cube["pos"]
            cdist = math.hypot(cx - x, cy - y)
            if cdist < 25.0:
                move_vx += ((cx - x) / max(1.0, cdist)) * 0.8
                move_vy += ((cy - y) / max(1.0, cdist)) * 0.8

        move_len = math.hypot(move_vx, move_vy)
        if move_len > 1e-3:
            norm = min(1.0, move_len)
            action["move"] = [(move_vx / move_len) * norm, (move_vy / move_len) * norm]
        else:
            action["move"] = [0.0, 0.0]

        mf.draw.point([z_next_center[0], z_next_center[1], z], "#00d0ff", 6)
        if target:
            mf.draw.point(target["pos"], "#ff2020", 5)

        return action


if __name__ == "__main__":
    mf.run(Bot)
