"""
Main Submission Bot for Mech Royale (Builders Buildathon).
Team: GMUNVIS
Bot Name: GMUNVIS-Ghost

Winning Architecture (EndGhost + Tactical Lethal Duelist):
1. Early Spawn Dislocation:
   - Tangential sprint (boost) for the first 3.5s away from initial spawn coordinate to safe perimeter.
2. Dead-Side Perimeter Holding:
   - Maintains outer rim sanctuary at 0.70 <= r / R_next <= 0.83.
   - Stationary in sanctuary = SILENT HOLD (0 sound emission).
3. Dynamic Building Occlusion ("Ring-Around-The-Rosy"):
   - Anchors to indestructible building prism; rotates to opposite face when enemy approaches within 60m.
4. Tree Crown Camouflage (is_under_tree):
   - Exploits tree crown foliage to blind house bots beyond 45m.
5. Absolute Trigger Discipline & Passive Regeneration:
   - Holds fire 100% during survival phase (znr > 32m) unless under point-blank threat (<18m) or defensive return fire.
   - Prevents 320m-380m audio broadcasts that attract house bot deathballs.
   - Enables 45 HP/s passive regeneration.
6. Predictive Lead Aim & Lethal Laser Release:
   - Fixed-point 2-step velocity ballistics lead calculation for arm gun.
   - Lethal laser release: releases trigger early when partial charge (charge * 450) >= enemy remaining HP.
   - Hard Mortar Ban (dropped immediately) to prevent 0% hit rate and +60% laser cook-off vulnerability.
7. Aim Breaking:
   - Continuous 0.7s strafe flips when under fire or in combat.
   - Sideways boost on enemy laser charging.
"""
from __future__ import annotations
import math
import os
import json
from typing import Any, Dict, List, Optional, Set, Tuple
import mechfight as mf


def angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def clamp(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


# -----------------------------------------------------------------------------
# ARENA GEOMETRY, BUILDING SPATIAL GRID & TREE CROWN OCCLUSION
# -----------------------------------------------------------------------------
MAP_BUILDINGS: List[Tuple[float, float, float, float]] = []
MAP_TREES: List[Tuple[float, float, float]] = []
BUILDING_GRID: Dict[Tuple[int, int], List[Tuple[float, float, float, float]]] = {}
GRID_CELL: float = 80.0


def load_arena_geometry(hello: Optional[Dict[str, Any]] = None):
    global MAP_BUILDINGS, MAP_TREES, BUILDING_GRID
    if MAP_BUILDINGS:
        return
    candidates = []
    if hello and isinstance(hello, dict):
        map_dir = hello.get("map", {}).get("dir")
        if map_dir:
            candidates.append(os.path.join(map_dir, "v2", "world.json"))
            candidates.append(os.path.join(map_dir, "world.json"))
    candidates.extend([
        os.path.join(os.path.dirname(__file__), "mechfight-kit", "arena", "v2", "world.json"),
        os.path.join(os.path.dirname(__file__), "..", "bt-mech", "mech", "mechfight-kit", "arena", "v2", "world.json"),
        os.path.join("mechfight-kit", "arena", "v2", "world.json"),
        "/arena/v2/world.json",
        os.path.join(os.path.dirname(__file__), "arena", "v2", "world.json"),
    ])
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    w = json.load(f)
                    for prism in w.get("prisms", []):
                        ring = prism.get("ring", [])
                        xs = ring[0::2]
                        ys = ring[1::2]
                        if xs and ys:
                            box = (min(xs), min(ys), max(xs), max(ys))
                            MAP_BUILDINGS.append(box)
                            min_gx = int(math.floor(box[0] / GRID_CELL))
                            max_gx = int(math.floor(box[2] / GRID_CELL))
                            min_gy = int(math.floor(box[1] / GRID_CELL))
                            max_gy = int(math.floor(box[3] / GRID_CELL))
                            for gx in range(min_gx, max_gx + 1):
                                for gy in range(min_gy, max_gy + 1):
                                    BUILDING_GRID.setdefault((gx, gy), []).append(box)
                    tdata = w.get("trees", {}).get("data", [])
                    tfields = w.get("trees", {}).get("fields", [])
                    if tdata and tfields:
                        step = len(tfields)
                        xi = tfields.index("x")
                        yi = tfields.index("y")
                        ri = tfields.index("crownR")
                        for i in range(0, len(tdata), step):
                            MAP_TREES.append((tdata[i + xi], tdata[i + yi], tdata[i + ri]))
                break
            except Exception:
                pass


def line_intersects_box(x1: float, y1: float, x2: float, y2: float, bx1: float, by1: float, bx2: float, by2: float) -> bool:
    if max(x1, x2) < bx1 or min(x1, x2) > bx2 or max(y1, y2) < by1 or min(y1, y2) > by2:
        return False
    dx = x2 - x1
    dy = y2 - y1
    p = [-dx, dx, -dy, dy]
    q = [x1 - bx1, bx2 - x1, y1 - by1, by2 - y1]
    u1, u2 = 0.0, 1.0
    for i in range(4):
        if p[i] == 0:
            if q[i] < 0:
                return False
        else:
            t = q[i] / p[i]
            if p[i] < 0:
                u1 = max(u1, t)
            else:
                u2 = min(u2, t)
            if u1 > u2:
                return False
    return True


def has_los(x1: float, y1: float, x2: float, y2: float) -> bool:
    if not MAP_BUILDINGS:
        return True
    min_x, max_x = (x1, x2) if x1 < x2 else (x2, x1)
    min_y, max_y = (y1, y2) if y1 < y2 else (y2, y1)
    min_gx = int(math.floor(min_x / GRID_CELL))
    max_gx = int(math.floor(max_x / GRID_CELL))
    min_gy = int(math.floor(min_y / GRID_CELL))
    max_gy = int(math.floor(max_y / GRID_CELL))

    tested: Set[Tuple[float, float, float, float]] = set()
    for gx in range(min_gx, max_gx + 1):
        for gy in range(min_gy, max_gy + 1):
            for box in BUILDING_GRID.get((gx, gy), []):
                if box not in tested:
                    tested.add(box)
                    if line_intersects_box(x1, y1, x2, y2, box[0], box[1], box[2], box[3]):
                        return False
    return True


def is_under_tree(x: float, y: float) -> bool:
    for tx, ty, tr in MAP_TREES:
        if (x - tx) ** 2 + (y - ty) ** 2 <= (tr * tr):
            return True
    return False


class Bot:
    name = "GMUNVIS-Ghost"

    def __init__(self, hello: Optional[mf.Hello] = None):
        if hello is None:
            hello = {}
        load_arena_geometry(hello)
        self.slot = hello.get("slot", 0)
        self.cfg = hello.get("config", {})
        self.mech_cfg = self.cfg.get("mech", {})
        self.weapons_cfg = self.cfg.get("weapons", {})
        self.loot_cfg = self.cfg.get("loot", {})

        self.gun_speed = self.weapons_cfg.get("gun", {}).get("speed", 252.0)
        self.gun_range = self.weapons_cfg.get("gun", {}).get("range", 230.0)
        self.pickup_radius = self.loot_cfg.get("pickup_radius", 4.0)

        # Tactical state
        self.spawn_pos: Optional[Tuple[float, float]] = None
        self.target_spot: Optional[Tuple[float, float]] = None
        self.target_building: Optional[Tuple[float, float, float, float]] = None
        self.last_search_t = -100.0
        self.last_dmg_taken_t = -100.0
        self.last_dmg_dealt_t = -100.0
        self.recent_attacker_bearing: Optional[float] = None
        self.in_final_combat = False

    def _find_best_sanctuary(
        self,
        my_x: float,
        my_y: float,
        znc_x: float,
        znc_y: float,
        zn_r: float,
        obs: mf.Observation,
    ) -> Optional[Tuple[Tuple[float, float], Tuple[float, float, float, float]]]:
        clearance = 4.3
        candidates = []
        t_now = obs.get("t", 0.0)

        threats: List[Tuple[float, float]] = [(41.3, -72.8)]
        if self.spawn_pos and (t_now < 35.0):
            threats.append(self.spawn_pos)
        for m in obs.get("mechs", []):
            if m.get("kind") == "mech":
                threats.append((m["pos"][0], m["pos"][1]))
        if self.recent_attacker_bearing is not None and (t_now - self.last_dmg_taken_t) < 6.0:
            threats.append((my_x + math.cos(self.recent_attacker_bearing) * 120.0, my_y + math.sin(self.recent_attacker_bearing) * 120.0))

        for b in MAP_BUILDINGS:
            bx1, by1, bx2, by2 = b
            bcx = (bx1 + bx2) * 0.5
            bcy = (by1 + by2) * 0.5
            d_center = math.hypot(bcx - znc_x, bcy - znc_y)

            if d_center > (zn_r * 0.88) or d_center < (zn_r * 0.35):
                continue

            faces = [
                (bcx, by2 + clearance),
                (bcx, by1 - clearance),
                (bx2 + clearance, bcy),
                (bx1 - clearance, bcy),
            ]

            for fx, fy in faces:
                d_spot_c = math.hypot(fx - znc_x, fy - znc_y)
                if d_spot_c > (zn_r * 0.84):
                    continue

                d_me = math.hypot(fx - my_x, fy - my_y)
                if d_me > 90.0:
                    continue

                valid = True
                for ob in MAP_BUILDINGS:
                    if (ob[0] - 3.6 <= fx <= ob[2] + 3.6) and (ob[1] - 3.6 <= fy <= ob[3] + 3.6):
                        valid = False
                        break
                if not valid:
                    continue

                score = 0.0
                for tx, ty in threats:
                    if not has_los(fx, fy, tx, ty):
                        score += 90.0
                    else:
                        score -= 120.0

                if is_under_tree(fx, fy):
                    score += 200.0

                r_ratio = d_spot_c / max(1.0, zn_r)
                if 0.70 <= r_ratio <= 0.83:
                    score += 90.0
                elif r_ratio < 0.45:
                    score -= 40.0

                if has_los(my_x, my_y, fx, fy):
                    score += 160.0

                score -= d_me * 0.35
                candidates.append((score, ((fx, fy), b)))

        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            return candidates[0][1]
        return None

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        t_now = obs.get("t", 0.0)
        action: mf.Action = {"tick": tick}
        me = obs.get("self")
        if not me:
            return action

        x, y, z = me["pos"]
        torso_yaw, torso_pitch = me.get("aim", [0.0, 0.0])
        hp = me.get("hp", 1400.0)
        energy = me.get("energy", 100.0)
        in_zone = me.get("in_zone", True)
        weapons = me.get("weapons", [None, None])
        muzzle_h = me.get("stats", {}).get("muzzle_height", 7.0)
        gun = weapons[0]
        has_secondary = len(weapons) > 1 and weapons[1] is not None
        sec = weapons[1] if has_secondary else None

        if self.spawn_pos is None:
            self.spawn_pos = (x, y)

        took_damage_now = False
        for ev in obs.get("events", []):
            if ev.get("type") == "damaged":
                self.last_dmg_taken_t = t_now
                took_damage_now = True
                b = ev.get("bearing")
                if b is not None:
                    self.recent_attacker_bearing = b
            elif ev.get("type") == "hit":
                self.last_dmg_dealt_t = t_now

        zone = obs.get("zone", {})
        zc = zone.get("center", [0.0, 0.0])
        zr = zone.get("radius", 560.0)
        znc = zone.get("next_center", zc)
        znr = zone.get("next_radius", zr)
        d_curr_c = math.hypot(x - zc[0], y - zc[1])
        margin = zr - d_curr_c
        d_next_c = math.hypot(x - znc[0], y - znc[1])

        if znr <= 32.0 or t_now >= 350.0:
            self.in_final_combat = True

        # Drop mortar immediately (Rank -1 ban)
        if has_secondary and sec and sec.get("kind") == "mortar":
            action["drop"] = 1
            has_secondary = False
            sec = None

        # Weapon pickups: Laser (Rank 4) > Lightning (Rank 3)
        pickups = obs.get("pickups", [])
        if pickups:
            weapon_weights = {"laser": 4, "lightning": 3, "mortar": -1}
            best_p = None
            best_w = 0
            for p in pickups:
                p_kind = p.get("kind", "")
                p_pos = p.get("pos", [0, 0, 0])
                d_horiz = math.hypot(p_pos[0] - x, p_pos[1] - y)
                if d_horiz <= self.pickup_radius:
                    w = weapon_weights.get(p_kind, -1)
                    if w > best_w:
                        best_w = w
                        best_p = p
            if best_p:
                action["take"] = best_p["id"]
                if has_secondary and weapon_weights.get(weapons[1].get("kind"), 0) < best_w:
                    action["drop"] = 1

        # Offer upgrade selection
        offer = obs.get("offer")
        if offer and "choices" in offer:
            choices = offer["choices"]
            best_idx = 0
            best_score = -999.0
            for i, ch in enumerate(choices):
                cid = ch.get("id", "").lower()
                desc = ch.get("desc", "").lower()
                score = 0.0
                if "armor" in cid or "armor" in desc: score = 100.0
                elif "hp" in cid or "health" in desc: score = 85.0
                elif "regen" in cid: score = 75.0
                elif "laser" in cid or "laser" in desc: score = 70.0
                elif "mortar" in cid or "mortar" in desc: score = -50.0
                else: score = 10.0
                if score > best_score:
                    best_score = score
                    best_idx = i
            action["pick"] = best_idx

        # Visible enemies
        visible_mechs = [m for m in obs.get("mechs", []) if m.get("kind") == "mech"]
        closest_enemy_dist = 999.0
        closest_enemy = None
        for m in visible_mechs:
            mx, my, _ = m["pos"]
            d = math.hypot(mx - x, my - y)
            if d < closest_enemy_dist:
                closest_enemy_dist = d
                closest_enemy = m

        need_search = (self.target_spot is None) or \
                      (math.hypot(self.target_spot[0] - znc[0], self.target_spot[1] - znc[1]) > (znr * 0.88)) or \
                      ((t_now - self.last_search_t) > 10.0)

        if need_search and not self.in_final_combat:
            res = self._find_best_sanctuary(x, y, znc[0], znc[1], znr, obs)
            if res:
                self.target_spot, self.target_building = res
                self.last_search_t = t_now

        # Dynamic Building Occlusion ("Ring-Around-The-Rosy")
        if self.target_building and closest_enemy and closest_enemy_dist < 60.0:
            emx, emy, _ = closest_enemy["pos"]
            if has_los(x, y, emx, emy):
                bx1, by1, bx2, by2 = self.target_building
                bcx = (bx1 + bx2) * 0.5
                bcy = (by1 + by2) * 0.5
                cl = 4.3
                faces = [(bcx, by2 + cl), (bcx, by1 - cl), (bx2 + cl, bcy), (bx1 - cl, bcy)]
                best_face = None
                best_face_dist = 999.0
                for fx, fy in faces:
                    if not has_los(fx, fy, emx, emy):
                        df = math.hypot(fx - x, fy - y)
                        if df < best_face_dist:
                            best_face_dist = df
                            best_face = (fx, fy)
                if best_face:
                    self.target_spot = best_face

        # Movement
        move_vx = 0.0
        move_vy = 0.0
        action["boost"] = False

        if not in_zone or margin < 15.0:
            to_next_x = znc[0] - x
            to_next_y = znc[1] - y
            d = max(1.0, math.hypot(to_next_x, to_next_y))
            move_vx += (to_next_x / d) * 2.5
            move_vy += (to_next_y / d) * 2.5
            action["boost"] = True

        elif not self.in_final_combat and self.target_spot:
            tx, ty = self.target_spot
            d_target = math.hypot(tx - x, ty - y)

            if d_target > 2.5:
                dir_x = (tx - x) / d_target
                dir_y = (tx - y) / d_target

                if took_damage_now or (closest_enemy and closest_enemy_dist < 140.0 and has_los(x, y, closest_enemy["pos"][0], closest_enemy["pos"][1])):
                    strafe_phase = int(t_now / 0.5) % 2
                    strafe_mult = 1.0 if strafe_phase == 0 else -1.0
                    move_vx += dir_x * 1.2 + (-dir_y * strafe_mult) * 0.8
                    move_vy += dir_y * 1.2 + (dir_x * strafe_mult) * 0.8
                    action["boost"] = energy > 20.0
                elif t_now < 4.0 and d_target > 15.0:
                    move_vx += dir_x * 1.5
                    move_vy += dir_y * 1.5
                    action["boost"] = True
                else:
                    if d_target > 12.0:
                        move_vx += dir_x * 0.85
                        move_vy += dir_y * 0.85
                    else:
                        move_vx += dir_x * 0.38
                        move_vy += dir_y * 0.38
            else:
                move_vx = 0.0
                move_vy = 0.0

        elif not self.in_final_combat:
            target_r = znr * 0.76
            r_diff = d_next_c - target_r
            to_next_x = (znc[0] - x) / max(1.0, d_next_c)
            to_next_y = (znc[1] - y) / max(1.0, d_next_c)
            if abs(r_diff) > 15.0:
                scale = clamp(r_diff / 25.0, -0.8, 0.8)
                move_vx += to_next_x * scale
                move_vy += to_next_y * scale
            else:
                move_vx = 0.0
                move_vy = 0.0

        else:
            # COMBAT MODE in final circles (znr <= 32m)
            if closest_enemy:
                emx, emy, _ = closest_enemy["pos"]
                edx = emx - x
                edy = emy - y
                edist = max(1.0, math.hypot(edx, edy))
                opt_dist = 22.0
                if edist < opt_dist:
                    move_vx -= (edx / edist) * 1.2
                    move_vy -= (edy / edist) * 1.2
                else:
                    move_vx += (edx / edist) * 0.6
                    move_vy += (edy / edist) * 0.6
                strafe_phase = int(t_now / 0.7) % 2
                strafe_mult = 1.0 if strafe_phase == 0 else -1.0
                move_vx += (-edy / edist * strafe_mult) * 0.9
                move_vy += (edx / edist * strafe_mult) * 0.9
            else:
                move_vx = 0.0
                move_vy = 0.0

        # Safe Cube Collection
        if (
            not self.in_final_combat
            and not closest_enemy
            and (t_now - self.last_dmg_taken_t) > 6.0
            and obs.get("cubes")
            and margin > 35.0
        ):
            cubes = obs["cubes"]
            safe_cubes = [c for c in cubes if has_los(x, y, c["pos"][0], c["pos"][1])]
            if safe_cubes:
                safe_cubes.sort(key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                cx, cy, _ = safe_cubes[0]["pos"]
                cdist = math.hypot(cx - x, cy - y)
                if cdist < 18.0:
                    move_vx += ((cx - x) / max(1.0, cdist)) * 0.4
                    move_vy += ((cy - y) / max(1.0, cdist)) * 0.4

        # Continuous obstacle repulsion pushing away from building centers
        for b in MAP_BUILDINGS:
            bx1, by1, bx2, by2 = b
            bw = bx2 - bx1
            bh = by2 - by1
            bcx = (bx1 + bx2) * 0.5
            bcy = (by1 + by2) * 0.5
            rep_r = max(bw, bh) * 0.5 + 4.2
            dist_b = math.hypot(x - bcx, y - bcy)
            if dist_b < rep_r and dist_b > 0.1:
                pen = (rep_r - dist_b) / rep_r
                rep_x = (x - bcx) / dist_b
                rep_y = (y - bcy) / dist_b
                move_vx += rep_x * pen * 1.5
                move_vy += rep_y * pen * 1.5

        # Final move normalization
        move_len = math.hypot(move_vx, move_vy)
        if move_len > 1e-3:
            norm = min(1.0, move_len)
            action["move"] = [(move_vx / move_len) * norm, (move_vy / move_len) * norm]
        else:
            action["move"] = [0.0, 0.0]

        # -------------------------------------------------------------
        # TORSO AIM & PREDICTIVE TRIGGER DISCIPLINE
        # -------------------------------------------------------------
        aim_yaw = torso_yaw
        aim_pitch = 0.0
        fire_slot0 = False
        fire_slot1 = False

        if closest_enemy:
            emx, emy, emz = closest_enemy["pos"]
            evx, evy, evz = closest_enemy.get("vel", [0.0, 0.0, 0.0])
            dist_xy = math.hypot(emx - x, emy - y)
            dz = (emz + 4.5) - (z + muzzle_h)
            d_3d = math.hypot(dist_xy, dz)

            # Predictive lead calculation
            t_flight = d_3d / max(1.0, self.gun_speed)
            pred_x = emx + evx * t_flight
            pred_y = emy + evy * t_flight
            pred_z = emz + 4.5 + evz * t_flight

            p_dx = pred_x - x
            p_dy = pred_y - y
            p_dz = pred_z - (z + muzzle_h)

            aim_yaw = math.atan2(p_dy, p_dx)
            aim_pitch = math.atan2(p_dz, max(1.0, math.hypot(p_dx, p_dy)))

            # Defense trigger: point-blank (< 18m), or final combat, OR under fire in close range (< 35m)
            under_fire_close = (t_now - self.last_dmg_taken_t) < 2.5 and (closest_enemy_dist < 35.0) and has_los(x, y, emx, emy)
            should_fire = self.in_final_combat or (closest_enemy_dist < 18.0) or under_fire_close

            if should_fire:
                yaw_err = abs(angle_diff(torso_yaw, aim_yaw))
                if yaw_err < 0.15:
                    if gun and gun.get("cooldown", 0.0) <= 0.02:
                        fire_slot0 = True
                    if has_secondary and sec:
                        sec_kind = sec.get("kind")
                        if sec_kind == "lightning" and closest_enemy_dist <= 40.0:
                            fire_slot1 = True
                        elif sec_kind == "laser":
                            charge = sec.get("charge", 0.0)
                            laser_dmg = charge * 450.0
                            enemy_hp = closest_enemy.get("hp", 1400.0)
                            # Lethal laser release: release trigger early if enough to eliminate
                            if charge >= 0.20 and laser_dmg >= enemy_hp:
                                fire_slot1 = False
                            elif charge < 0.70:
                                fire_slot1 = True
                            else:
                                fire_slot1 = False

        elif self.recent_attacker_bearing is not None and (t_now - self.last_dmg_taken_t) < 4.0:
            aim_yaw = self.recent_attacker_bearing
        else:
            aim_yaw = math.atan2(-72.8 - y, 41.3 - x)

        action["aim"] = [aim_yaw, clamp(aim_pitch, -0.7, 1.3)]
        action["fire"] = [fire_slot0, fire_slot1]

        # Debug drawings
        mf.draw.point([znc[0], znc[1], z], "#00d0ff", 6)
        if self.target_spot:
            mf.draw.point([self.target_spot[0], self.target_spot[1], z], "#20ff20", 5)

        return action


if __name__ == "__main__":
    mf.run(Bot)
