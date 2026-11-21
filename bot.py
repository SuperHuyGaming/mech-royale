"""
Main Submission Bot for Mech Royale (Builders Buildathon).
Team: GMUNVIS
Bot Name: GMUNVIS-v3

Design:
1. Silence & Discipline:
   - NEVER fires blindly or at crates; fires ONLY at visible enemies in effective range.
   - Stationary in cover (speed 0 m/s) emits 0 sound radius (silent).
2. Cover inside Next Zone Circle:
   - Relocates early to dead-side building cover inside the next circle.
   - Positions outside the rear wall away from zone center to block enemy LOS and protect rear armor.
3. Low HP (<40%) Regen Retreat:
   - Immediately holds fire (to allow 5.0s combat timer to expire for 45 HP/s passive regen).
   - Retreats away from threats while keeping torso aimed at incoming bearing to prevent 1.5x rear armor hits.
4. Battle-Tested Combat & Evasion (from v1):
   - Ballistic lead aiming (speed 252 m/s, pitch compensation, alignment gate <0.14 rad).
   - Full secondary weapon control (laser charge burst, lightning cone, mortar lob).
   - Dynamic kiting and projectile dodging (bullets, mortars, charging lasers).
   - Priority upgrade selection (Armor > HP > Regen > Weapons).
5. Zone Safety:
   - Hard override: never stands outside zone or within 20m of the shrinking perimeter.
"""
from __future__ import annotations
import math
import os
import json
from typing import Any, Dict, List, Optional, Tuple
import mechfight as mf


def angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def clamp(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


# Load arena buildings once
MAP_BUILDINGS: List[Tuple[float, float, float, float]] = []
MAP_TREES: List[Tuple[float, float, float]] = []

def _load_map():
    global MAP_BUILDINGS, MAP_TREES
    if MAP_BUILDINGS:
        return
    for p in [
        os.path.join(os.path.dirname(__file__), "mechfight-kit", "arena", "v2", "world.json"),
        os.path.join("mechfight-kit", "arena", "v2", "world.json"),
        "/arena/v2/world.json",
        os.path.join(os.path.dirname(__file__), "..", "arena", "v2", "world.json"),
    ]:
        if os.path.exists(p):
            try:
                with open(p, "r") as f:
                    w = json.load(f)
                    for prism in w.get("prisms", []):
                        ring = prism.get("ring", [])
                        xs = ring[0::2]
                        ys = ring[1::2]
                        if xs and ys:
                            MAP_BUILDINGS.append((min(xs), min(ys), max(xs), max(ys)))
                    tdata = w.get("trees", {}).get("data", [])
                    tfields = w.get("trees", {}).get("fields", [])
                    if tdata and tfields:
                        step = len(tfields)
                        xi = tfields.index("x")
                        yi = tfields.index("y")
                        ri = tfields.index("crownR")
                        for i in range(0, len(tdata), step):
                            MAP_TREES.append((tdata[i+xi], tdata[i+yi], tdata[i+ri]))
                break
            except Exception:
                pass

_load_map()


class Bot:
    name = "GMUNVIS-v3"

    def __init__(self, hello: mf.Hello = None):
        if hello is None:
            hello = {}
        self.slot = hello.get("slot", 0)
        self.cfg = hello.get("config", {})
        self.weapons_cfg = self.cfg.get("weapons", {})
        self.loot_cfg = self.cfg.get("loot", {})

        self.gun_speed = self.weapons_cfg.get("gun", {}).get("speed", 252.0)
        self.gun_range = self.weapons_cfg.get("gun", {}).get("range", 220.0)
        self.pickup_radius = self.loot_cfg.get("pickup_radius", 4.0)

        self.cover_spot: Optional[Tuple[float, float]] = None

    def _find_cover_in_next_circle(self, my_x: float, my_y: float, nx: float, ny: float, nr: float) -> Optional[Tuple[float, float]]:
        candidates = []
        for bx1, by1, bx2, by2 in MAP_BUILDINGS:
            bcx = (bx1 + bx2) * 0.5
            bcy = (by1 + by2) * 0.5
            d_next = math.hypot(bcx - nx, bcy - ny)
            if 0.35 * nr <= d_next <= 0.82 * nr:
                # Place spot on outer side (away from next_center)
                dx = bcx - nx
                dy = bcy - ny
                dlen = math.hypot(dx, dy)
                if dlen > 1e-3:
                    ux, uy = dx / dlen, dy / dlen
                else:
                    ux, uy = 1.0, 0.0
                half_w = (bx2 - bx1) * 0.5
                half_h = (by2 - by1) * 0.5
                spot_x = bcx + ux * (half_w + 3.5)
                spot_y = bcy + uy * (half_h + 3.5)
                
                d_me = math.hypot(spot_x - my_x, spot_y - my_y)
                score = -d_me + (d_next * 0.2)
                candidates.append((score, (spot_x, spot_y)))

        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            return candidates[0][1]

        # Fallback orbit
        angle = math.atan2(my_y - ny, my_x - nx)
        return (nx + math.cos(angle) * (nr * 0.6), ny + math.sin(angle) * (nr * 0.6))

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
        # 1. UPGRADE SELECTION
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
                    score = 100.0
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
        # 3. THREAT EVALUATION
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
                score += 35.0

            if score > best_target_score:
                best_target_score = score
                target = m

        # -------------------------------------------------------------
        # 4. PREDICTIVE AIMING & SILENT DISCIPLINE
        # -------------------------------------------------------------
        aim_yaw = torso_yaw
        aim_pitch = 0.0
        fire_slot0 = False
        fire_slot1 = False

        # RULE: RETREAT TO REGEN WHEN HP < 40% (stop firing to allow 5.0s combat timer to expire)
        is_low_hp = hp < (max_hp * 0.40)

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

            # SILENCE RULE: Fire ONLY if not retreating for regen, and within range
            if not is_low_hp:
                if aligned and dist_xy <= self.gun_range:
                    fire_slot0 = True

                if has_secondary:
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
        else:
            # NO TARGET: STAY SILENT (never shoot crates or blind fire)
            fire_slot0 = False
            fire_slot1 = False
            # Handle damage events: if shot from behind, snap torso to attacker
            for ev in obs.get("events", []):
                if ev.get("type") == "damaged":
                    b = ev.get("bearing")
                    if b is not None:
                        aim_yaw = b
            mf.draw.label(f"HP:{hp:.0f} | Stealth Hold")

        action["aim"] = [aim_yaw, aim_pitch]
        action["fire"] = [fire_slot0, fire_slot1]

        # -------------------------------------------------------------
        # 5. MOVEMENT & COVER POSITIONING
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

        # RULE 1: NEVER STAND OUTSIDE ZONE
        if not in_zone or margin < 20.0:
            to_next_x = z_next_center[0] - x
            to_next_y = z_next_center[1] - y
            d_next = max(1.0, dist_to_next_center)
            move_vx += (to_next_x / d_next) * 3.0
            move_vy += (to_next_y / d_next) * 3.0
            action["boost"] = True

        elif is_low_hp and target:
            # RETREAT BEHIND COVER TO REGEN
            # Move away from target while keeping aim locked on target (no rear damage)
            tx, ty, _ = target["pos"]
            away_x = x - tx
            away_y = y - ty
            d_away = max(1.0, math.hypot(away_x, away_y))
            move_vx += (away_x / d_away) * 2.0
            move_vy += (away_y / d_away) * 2.0
            action["boost"] = energy > 20.0

        elif target:
            # IN COMBAT: Kiting & spacing
            tx, ty, _ = target["pos"]
            edx = tx - x
            edy = ty - y
            edist = max(1.0, math.hypot(edx, edy))
            dir_to_enemy_x = edx / edist
            dir_to_enemy_y = edy / edist

            has_lightning = has_secondary and weapons[1]["kind"] == "lightning"
            optimal_dist = 32.0 if has_lightning else 65.0

            if edist < optimal_dist:
                move_vx -= dir_to_enemy_x * 1.5
                move_vy -= dir_to_enemy_y * 1.5
            elif edist > (optimal_dist + 25.0) and margin > 35.0:
                move_vx += dir_to_enemy_x * 0.6
                move_vy += dir_to_enemy_y * 0.6

        else:
            # OUT OF COMBAT: STAY IN COVER INSIDE NEXT ZONE CIRCLE
            need_cover = False
            if self.cover_spot is None:
                need_cover = True
            else:
                cdist_next = math.hypot(self.cover_spot[0] - z_next_center[0], self.cover_spot[1] - z_next_center[1])
                if cdist_next > (z_next_radius * 0.85):
                    need_cover = True

            if need_cover:
                self.cover_spot = self._find_cover_in_next_circle(x, y, z_next_center[0], z_next_center[1], z_next_radius)

            if self.cover_spot:
                cs_x, cs_y = self.cover_spot
                d_to_spot = math.hypot(cs_x - x, cs_y - y)
                if d_to_spot > 6.0:
                    dir_x = (cs_x - x) / d_to_spot
                    dir_y = (cs_y - y) / d_to_spot
                    move_vx += dir_x * 0.9
                    move_vy += dir_y * 0.9
                    action["boost"] = False
                else:
                    # At cover spot! STAY STILL (speed = 0 -> 100% silent, 0m sound radius!)
                    move_vx = 0.0
                    move_vy = 0.0
                    action["boost"] = False

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

        # Collect nearby cubes quietly if margin is safe
        if not target and obs.get("cubes") and margin > 30.0:
            cubes = obs["cubes"]
            cubes.sort(key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
            nearest_cube = cubes[0]
            cx, cy, _ = nearest_cube["pos"]
            cdist = math.hypot(cx - x, cy - y)
            if cdist < 15.0:
                move_vx += ((cx - x) / max(1.0, cdist)) * 0.6
                move_vy += ((cy - y) / max(1.0, cdist)) * 0.6

        move_len = math.hypot(move_vx, move_vy)
        if move_len > 1e-3:
            norm = min(1.0, move_len)
            action["move"] = [(move_vx / move_len) * norm, (move_vy / move_len) * norm]
        else:
            action["move"] = [0.0, 0.0]

        return action


if __name__ == "__main__":
    mf.run(Bot)
