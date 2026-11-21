"""
Main Submission Bot for Mech Royale (Builders Buildathon).
Team: GMUNVIS
Bot Name: GMUNVIS-v5

Enhanced Tactical Duelist Architecture:
1. Weapon Hierarchy & Hard Mortar Ban:
   - Laser (Rank 4, hitscan to 260m) > Lightning (Rank 3, 28-44m auto-hit) > Gun (Rank 1).
   - Hard ban on Mortar (Rank -1, immediately dropped): avoids 0% hit rates and +60% laser cook-off damage!
2. Tactical Combat Range Control:
   - Laser: 125m standoff spacing.
   - Lightning: 28m close-quarters spacing.
   - Gun-only: 75m mid-range spacing.
3. Lethal Laser Release:
   - Releases trigger early when partial charge damage (charge * 450) >= enemy remaining HP.
4. Aim Breaking & Evasion:
   - Continuous 0.7s strafe flips to break enemy predictive lead.
   - Instant sideways boost when detecting charging enemy laser.
5. Disengage Matrix with Hysteresis:
   - Retreats to break LOS and trigger 45 HP/s passive regen when:
     * HP < 80% vs >=3 visible enemies
     * HP < 55% vs 2 enemies
     * HP < 35% vs 1 enemy
   - Stays in disengage mode until healed back to 90% HP.
6. Dynamic Dead-Side Rotation & Collision-Free Cover:
   - Spatial hash grid of building bounding boxes loaded from arena world.json (>3.5m clearance).
   - Sound-based rotation towards audio-quiet sectors of the circle.
"""
from __future__ import annotations
import math
import os
import json
from typing import Any, Dict, List, Optional, Set, Tuple
import mechfight as mf


def angle_diff(a: float, b: float) -> float:
    """Signed shortest difference between two angles in radians [-pi, pi]."""
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def clamp(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


# -----------------------------------------------------------------------------
# HIGH-PERFORMANCE GEOMETRY & LINE-OF-SIGHT
# -----------------------------------------------------------------------------
MAP_BUILDINGS: List[Tuple[float, float, float, float]] = []
BUILDING_GRID: Dict[Tuple[int, int], List[Tuple[float, float, float, float]]] = {}
GRID_CELL: float = 80.0


def load_building_geometry(hello: Optional[Dict[str, Any]] = None):
    global MAP_BUILDINGS, BUILDING_GRID
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
        os.path.join("mechfight-kit", "arena", "v2", "world.json"),
        "/arena/v2/world.json",
        os.path.join(os.path.dirname(__file__), "..", "arena", "v2", "world.json"),
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
    """Checks if a 2D line segment between (x1, y1) and (x2, y2) intersects any building."""
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


# -----------------------------------------------------------------------------
# BOT V5 IMPLEMENTATION
# -----------------------------------------------------------------------------
class Bot:
    name = "GMUNVIS-v5"

    def __init__(self, hello: Optional[mf.Hello] = None):
        if hello is None:
            hello = {}
        load_building_geometry(hello)
        self.slot = hello.get("slot", 0)
        self.cfg = hello.get("config", {})
        self.mech_cfg = self.cfg.get("mech", {})
        self.weapons_cfg = self.cfg.get("weapons", {})
        self.loot_cfg = self.cfg.get("loot", {})

        self.gun_speed = self.weapons_cfg.get("gun", {}).get("speed", 252.0)
        self.gun_range = self.weapons_cfg.get("gun", {}).get("range", 230.0)
        self.pickup_radius = self.loot_cfg.get("pickup_radius", 4.0)

        # State tracking
        self.last_dmg_taken_t = -100.0
        self.last_dmg_dealt_t = -100.0
        self.recent_attacker_bearing: Optional[float] = None
        self.pos_history: List[Tuple[float, float]] = []

        # Disengaged regen state with hysteresis
        self.disengaged_regen: bool = False

        # Cover & dead-side state
        self.cover_spot: Optional[Tuple[float, float]] = None
        self.last_cover_search_t = -100.0

    def _find_cover_in_next_circle(self, my_x: float, my_y: float, nx: float, ny: float, nr: float, dead_side_ang: Optional[float] = None) -> Optional[Tuple[float, float]]:
        """Finds safe cover coordinates >3.5m outside all building collision prisms."""
        clearance = 4.0  # strictly > 3.5m clearance from all building walls
        candidates = []
        for bx1, by1, bx2, by2 in MAP_BUILDINGS:
            bcx = (bx1 + bx2) * 0.5
            bcy = (by1 + by2) * 0.5
            d_next = math.hypot(bcx - nx, bcy - ny)
            if d_next > (nr * 0.88) or d_next < (nr * 0.22):
                continue
            face_spots = [
                (bcx, by1 - clearance),
                (bcx, by2 + clearance),
                (bx1 - clearance, bcy),
                (bx2 + clearance, bcy),
            ]
            for sx, sy in face_spots:
                if math.hypot(sx - nx, sy - ny) > (nr * 0.82):
                    continue
                # Ensure >3.5m clearance against ALL buildings
                is_valid = True
                for b1, b2, b3, b4 in MAP_BUILDINGS:
                    if (b1 - 3.6 <= sx <= b3 + 3.6) and (b2 - 3.6 <= sy <= b4 + 3.6):
                        is_valid = False
                        break
                if not is_valid:
                    continue
                d_me = math.hypot(sx - my_x, sy - my_y)
                score = -d_me + (d_next * 0.15)
                if dead_side_ang is not None:
                    spot_ang = math.atan2(sy - ny, sx - nx)
                    score += math.cos(spot_ang - dead_side_ang) * 50.0
                candidates.append((score, (sx, sy)))
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
        max_hp = me.get("max_hp", 1400.0)
        energy = me.get("energy", 100.0)
        in_zone = me.get("in_zone", True)
        grounded = me.get("grounded", True)
        weapons = me.get("weapons", [None, None])
        muzzle_h = me.get("stats", {}).get("muzzle_height", 7.0)
        gun = weapons[0]
        has_secondary = len(weapons) > 1 and weapons[1] is not None
        sec = weapons[1] if has_secondary else None

        # -------------------------------------------------------------
        # 1. PROCESS EVENTS (DAMAGE TAKEN & REAR PROTECTION)
        # -------------------------------------------------------------
        for ev in obs.get("events", []):
            if ev.get("type") == "damaged":
                self.last_dmg_taken_t = t_now
                b = ev.get("bearing")
                if b is not None:
                    self.recent_attacker_bearing = b
            elif ev.get("type") == "hit":
                self.last_dmg_dealt_t = t_now

        # -------------------------------------------------------------
        # 2. SMART UPGRADE SELECTION
        # -------------------------------------------------------------
        offer = obs.get("offer")
        if offer and "choices" in offer:
            choices = offer["choices"]
            best_idx = 0
            best_score = -999.0
            held_secondary = weapons[1]["kind"] if has_secondary else None

            for i, ch in enumerate(choices):
                cid = ch.get("id", "").lower()
                desc = ch.get("desc", "").lower()
                score = 0.0

                if "armor" in cid or "armor" in desc:
                    score = 100.0  # -8% damage taken critical for survival
                elif "hp" in cid or "health" in desc:
                    score = 85.0
                elif "regen" in cid:
                    score = 75.0
                elif "laser" in cid or "laser" in desc:
                    score = 70.0
                elif "gun" in cid or "rotary" in cid:
                    score = 65.0
                elif held_secondary and (held_secondary in cid or held_secondary in desc):
                    score = 60.0
                elif "sensor" in cid:
                    score = 40.0
                elif "speed" in cid or "boost" in cid:
                    score = 35.0
                elif "mortar" in cid or "mortar" in desc:
                    score = -50.0  # Never upgrade mortar
                else:
                    score = 10.0

                if score > best_score:
                    best_score = score
                    best_idx = i

            action["pick"] = best_idx

        # -------------------------------------------------------------
        # 3. WEAPON LOOTING & PICKUPS (HARD MORTAR BAN)
        # -------------------------------------------------------------
        # Hard drop mortar if currently held
        if has_secondary and sec and sec.get("kind") == "mortar":
            action["drop"] = 1
            has_secondary = False
            sec = None

        # Weapon hierarchy: Laser (4) > Lightning (3). Hard ban Mortar (-1).
        weapon_weights = {"laser": 4, "lightning": 3, "mortar": -1}
        pickups = obs.get("pickups", [])

        if pickups:
            best_pickup = None
            best_weight = -1
            for p in pickups:
                p_kind = p.get("kind", "")
                p_pos = p.get("pos", [0, 0, 0])
                d_horiz = math.hypot(p_pos[0] - x, p_pos[1] - y)
                d_vert = abs(p_pos[2] - z)
                if d_horiz <= self.pickup_radius and d_vert <= 4.0:
                    w = weapon_weights.get(p_kind, -1)
                    if w > 0:
                        if not has_secondary:
                            if w > best_weight:
                                best_weight = w
                                best_pickup = p
                        else:
                            curr_w = weapon_weights.get(weapons[1].get("kind"), 0)
                            if w > curr_w and w > best_weight:
                                best_weight = w
                                best_pickup = p

            if best_pickup:
                action["take"] = best_pickup["id"]
                if has_secondary:
                    action["drop"] = 1

        # -------------------------------------------------------------
        # 4. SOUND-BASED DEAD-SIDE ROTATION ANALYSIS
        # -------------------------------------------------------------
        danger_vx = 0.0
        danger_vy = 0.0
        for s in obs.get("sounds", []):
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
        # 5. THREAT EVALUATION & TARGET SELECTION
        # -------------------------------------------------------------
        visible_mechs = [m for m in obs.get("mechs", []) if m.get("kind") == "mech"]
        target = None
        best_target_score = -9999.0

        for m in visible_mechs:
            mx, my, mz = m["pos"]
            dist = math.hypot(mx - x, my - y)
            if dist > min(230.0, self.gun_range):
                continue

            m_hp = m.get("hp", 1400.0)
            m_weapons = m.get("weapons", [])

            # Base score: closer is higher threat
            score = 300.0 - (dist * 1.2) + ((1400.0 - m_hp) * 0.18)

            # High priority: low HP targets can be eliminated quickly
            if m_hp < 280.0:
                score += 90.0

            # Weapon triangle: Laser deals +60% cookoff damage vs Mortar
            if has_secondary and sec and sec.get("kind") == "laser" and "mortar" in m_weapons:
                score += 65.0

            # Charging laser is an immediate danger
            if m.get("charging", False):
                score += 50.0

            # Rear exposure: hits deal 1.5x damage
            m_yaw = m.get("aim", [0.0, 0.0])[0]
            bearing_to_me = math.atan2(y - my, x - mx)
            facing_diff = abs(angle_diff(m_yaw, bearing_to_me))
            if facing_diff > (math.pi * 0.5):
                score += 40.0

            if score > best_target_score:
                best_target_score = score
                target = m

        # -------------------------------------------------------------
        # 6. DISENGAGE MATRIX (HEAL TO 90% WITH HYSTERESIS)
        # -------------------------------------------------------------
        n_visible = len(visible_mechs)
        hp_thresh = 0.80 if n_visible >= 3 else (0.55 if n_visible >= 2 else 0.35)
        if not self.disengaged_regen:
            if hp < (max_hp * hp_thresh):
                self.disengaged_regen = True
        else:
            if hp >= (max_hp * 0.90):
                self.disengaged_regen = False
        is_low_hp = self.disengaged_regen

        # Safe Crate Targeting (Early Economy: when no enemies within 60m and quiet)
        crate_target = None
        is_safe_for_harvest = (
            target is None
            and hp >= (max_hp * 0.65)
            and (t_now - self.last_dmg_taken_t) > 5.0
            and not any(s.get("dist") == "near" for s in obs.get("sounds", []))
            and not any(math.hypot(m["pos"][0] - x, m["pos"][1] - y) < 60.0 for m in visible_mechs)
        )

        if is_safe_for_harvest and obs.get("crates"):
            nearby_crates = []
            for c in obs["crates"]:
                cx, cy, cz = c["pos"]
                cdist = math.hypot(cx - x, cy - y)
                if cdist <= 40.0 and has_los(x, y, cx, cy):
                    nearby_crates.append((cdist, c))
            if nearby_crates:
                nearby_crates.sort(key=lambda item: item[0])
                crate_target = nearby_crates[0][1]

        # -------------------------------------------------------------
        # 7. PREDICTIVE AIMING, BALLISTICS & TRIGGER DISCIPLINE
        # -------------------------------------------------------------
        aim_yaw = torso_yaw
        aim_pitch = 0.0
        fire_slot0 = False
        fire_slot1 = False

        if target:
            # Active enemy in LOS: MUST return fire with predictive lead! Never cease fire in open!
            tx, ty, tz = target["pos"]
            tvx, tvy, tvz = target.get("vel", [0.0, 0.0, 0.0])
            dist_xy = math.hypot(tx - x, ty - y)
            dz = (tz + 4.5) - (z + muzzle_h)
            d_3d = math.hypot(dist_xy, dz)

            # 2-step fixed-point lead calculation
            t_flight = d_3d / max(1.0, self.gun_speed)
            pred_tx = tx + tvx * t_flight
            pred_ty = ty + tvy * t_flight
            pred_tz = tz + 4.5 + tvz * t_flight

            p_dx = pred_tx - x
            p_dy = pred_ty - y
            p_dz = pred_tz - (z + muzzle_h)
            p_dxy = math.hypot(p_dx, p_dy)

            aim_yaw = math.atan2(p_dy, p_dx)
            aim_pitch = math.atan2(p_dz, max(1.0, p_dxy))
            aim_pitch = clamp(aim_pitch, -0.7, 1.3)

            yaw_err = abs(angle_diff(torso_yaw, aim_yaw))
            pitch_err = abs(torso_pitch - aim_pitch)
            aligned = (yaw_err < 0.14) and (pitch_err < 0.18)

            # Arm Gun firing
            if aligned and dist_xy <= self.gun_range:
                if gun and gun.get("cooldown", 0.0) <= 0.02:
                    fire_slot0 = True
                    self.last_dmg_dealt_t = t_now

            # Secondary Weapon firing
            if has_secondary and sec:
                sec_kind = sec.get("kind")
                if sec_kind == "lightning":
                    if dist_xy <= 44.0 and yaw_err < math.radians(18.0) and energy > 10.0:
                        fire_slot1 = True
                        self.last_dmg_dealt_t = t_now
                elif sec_kind == "laser":
                    charge = sec.get("charge", 0.0)
                    if dist_xy <= 260.0:
                        # Lethal laser release: if current charge damage >= target HP, fire immediately!
                        laser_dmg = charge * 450.0
                        target_hp = target.get("hp", 1400.0)
                        if charge >= 0.20 and laser_dmg >= target_hp and aligned:
                            fire_slot1 = False  # Release trigger to fire lethal burst!
                            self.last_dmg_dealt_t = t_now
                        elif charge < 0.70:
                            fire_slot1 = True
                        else:
                            fire_slot1 = not aligned  # Release trigger when aligned
                        if not fire_slot1 and charge >= 0.65:
                            self.last_dmg_dealt_t = t_now

            mf.draw.line([x, y, z + muzzle_h], [pred_tx, pred_ty, pred_tz], "#ff4444")
            mf.draw.label(f"HP:{hp:.0f} | Target:#{int(target['id'])} Dist:{dist_xy:.1f}m")

        elif crate_target:
            cx, cy, cz = crate_target["pos"]
            aim_yaw = math.atan2(cy - y, cx - x)
            aim_pitch = math.atan2(cz - (z + muzzle_h), max(1.0, math.hypot(cx - x, cy - y)))
            aim_pitch = clamp(aim_pitch, -0.7, 1.3)
            yaw_err = abs(angle_diff(torso_yaw, aim_yaw))
            if yaw_err < 0.10 and gun and gun.get("cooldown", 0.0) <= 0.02:
                fire_slot0 = True
            mf.draw.label(f"HP:{hp:.0f} | Harvesting Crate")

        elif is_low_hp and not target:
            # Low HP behind cover: disengage for passive regen (45 HP/s)
            # Cover incoming bearing if recently damaged
            if self.recent_attacker_bearing is not None and (t_now - self.last_dmg_taken_t) < 4.0:
                aim_yaw = self.recent_attacker_bearing
            mf.draw.label(f"HP:{hp:.0f} | Disengaged Regen (Goal 90%)")

        else:
            # Standby / patrol: face recent damage bearing or movement direction
            if self.recent_attacker_bearing is not None and (t_now - self.last_dmg_taken_t) < 3.0:
                aim_yaw = self.recent_attacker_bearing
            mf.draw.label(f"HP:{hp:.0f} | Scanning Area")

        action["aim"] = [aim_yaw, aim_pitch]
        action["fire"] = [fire_slot0, fire_slot1]

        # -------------------------------------------------------------
        # 8. DYNAMIC ZONE NAVIGATION & COVER/DEAD-SIDE ROTATION
        # -------------------------------------------------------------
        move_vx = 0.0
        move_vy = 0.0
        action["boost"] = False

        zone = obs.get("zone", {})
        zc = zone.get("center", [0.0, 0.0])
        zr = zone.get("radius", 560.0)
        znc = zone.get("next_center", zc)
        znr = zone.get("next_radius", zr)
        shrink_start = zone.get("shrink_start", 60.0)

        d_curr = math.hypot(x - zc[0], y - zc[1])
        margin = zr - d_curr
        d_next = math.hypot(x - znc[0], y - znc[1])

        # Periodically refresh collision-free cover spot
        if (t_now - self.last_cover_search_t) > 6.0 or self.cover_spot is None:
            self.cover_spot = self._find_cover_in_next_circle(x, y, znc[0], znc[1], znr, dead_side_ang)
            self.last_cover_search_t = t_now

        # Emergency Zone Recovery
        if not in_zone or margin < 20.0:
            to_next_x = znc[0] - x
            to_next_y = znc[1] - y
            d = max(1.0, math.hypot(to_next_x, to_next_y))
            move_vx += (to_next_x / d) * 3.0
            move_vy += (to_next_y / d) * 3.0
            action["boost"] = True

        elif not target and self.cover_spot:
            # Move towards verified cover spot outside building collisions
            cs_x, cs_y = self.cover_spot
            d_to_spot = math.hypot(cs_x - x, cs_y - y)
            if d_to_spot > 4.5:
                dir_x = (cs_x - x) / d_to_spot
                dir_y = (cs_y - y) / d_to_spot
                move_vx += dir_x * 0.85
                move_vy += dir_y * 0.85
            else:
                # SILENT HOLD in cover: 0 sound emission
                move_vx = 0.0
                move_vy = 0.0

        else:
            # Adaptive Orbit Radius Scaling & Silent Hold
            if znr >= 240.0:
                alpha = 0.55
            elif znr >= 70.0:
                alpha = 0.52
            elif znr >= 25.0:
                alpha = 0.45
            else:
                alpha = 0.25

            target_r = znr * alpha
            r_diff = d_next - target_r

            to_next_x = (znc[0] - x) / max(1.0, d_next)
            to_next_y = (znc[1] - y) / max(1.0, d_next)

            if abs(r_diff) > 20.0:
                scale = clamp(r_diff / 30.0, -1.0, 1.0)
                move_vx += to_next_x * scale * 0.8
                move_vy += to_next_y * scale * 0.8
            else:
                # SILENT HOLD: Inside safe orbit band, stationary = 0 sound emission
                move_vx = 0.0
                move_vy = 0.0

            time_to_shrink = max(0.0, shrink_start - t_now)
            if (d_next > znr * 0.90) and (time_to_shrink < 8.0):
                action["boost"] = True

        # -------------------------------------------------------------
        # 9. COMBAT SPACING, REVERSE KITING & AIM BREAKING
        # -------------------------------------------------------------
        if target:
            tx, ty, _ = target["pos"]
            edx = tx - x
            edy = ty - y
            edist = max(1.0, math.hypot(edx, edy))
            dir_to_enemy_x = edx / edist
            dir_to_enemy_y = edy / edist

            has_laser = has_secondary and sec and sec.get("kind") == "laser"
            has_lightning = has_secondary and sec and sec.get("kind") == "lightning"
            # Exact range control per architect recipe:
            optimal_dist = 125.0 if has_laser else (28.0 if has_lightning else 75.0)

            if edist < optimal_dist:
                kite_weight = 1.8 if is_low_hp else 1.2
                move_vx -= dir_to_enemy_x * kite_weight
                move_vy -= dir_to_enemy_y * kite_weight
            elif edist > (optimal_dist + 25.0) and in_zone and margin > 40.0:
                move_vx += dir_to_enemy_x * 0.6
                move_vy += dir_to_enemy_y * 0.6

            # Aim breaking: periodic strafe flips every 0.7s during combat to break enemy lead aim
            strafe_phase = int(t_now / 0.7) % 2
            strafe_mult = 1.0 if strafe_phase == 0 else -1.0
            move_vx += (-dir_to_enemy_y * strafe_mult) * 0.95
            move_vy += (dir_to_enemy_x * strafe_mult) * 0.95

        # -------------------------------------------------------------
        # 10. ACTIVE DODGING (PROJECTILES & CHARGING LASERS)
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
                    strafe_yaw = m_yaw + (math.pi * 0.5)
                    move_vx += math.cos(strafe_yaw) * 2.5
                    move_vy += math.sin(strafe_yaw) * 2.5
                    action["boost"] = energy > 20.0

        # Safe Cube Harvesting
        if not target and obs.get("cubes") and margin > 30.0:
            cubes = obs["cubes"]
            safe_cubes = [c for c in cubes if has_los(x, y, c["pos"][0], c["pos"][1])]
            if safe_cubes:
                safe_cubes.sort(key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                cx, cy, _ = safe_cubes[0]["pos"]
                cdist = math.hypot(cx - x, cy - y)
                if cdist < 25.0:
                    move_vx += ((cx - x) / max(1.0, cdist)) * 0.8
                    move_vy += ((cy - y) / max(1.0, cdist)) * 0.8

        # -------------------------------------------------------------
        # 11. HARD ZONE BOUNDARY PROJECTION (ZERO OUTWARD VELOCITY)
        # -------------------------------------------------------------
        dx_c = x - zc[0]
        dy_c = y - zc[1]
        dist_c = max(1.0, math.hypot(dx_c, dy_c))
        margin = zr - dist_c

        if margin < 30.0:
            ur_x = dx_c / dist_c
            ur_y = dy_c / dist_c
            v_out = move_vx * ur_x + move_vy * ur_y
            if v_out > 0.0:
                move_vx -= v_out * ur_x
                move_vy -= v_out * ur_y
            pen = (30.0 - margin) / 15.0
            move_vx -= ur_x * min(2.5, pen * 2.5)
            move_vy -= ur_y * min(2.5, pen * 2.5)

        # Anti-Stuck Watchdog (only when out of combat)
        if not target:
            self.pos_history.append((x, y))
            if len(self.pos_history) > 10:
                self.pos_history.pop(0)
                travel = math.hypot(x - self.pos_history[0][0], y - self.pos_history[0][1])
                mv_speed = math.hypot(move_vx, move_vy)
                if travel < 0.20 and mv_speed > 0.6 and grounded:
                    action["jump"] = True
                    move_vx, move_vy = -move_vy, move_vx
        else:
            self.pos_history.clear()

        # Final Velocity Normalization
        move_len = math.hypot(move_vx, move_vy)
        if move_len > 1e-3:
            norm = min(1.0, move_len)
            action["move"] = [(move_vx / move_len) * norm, (move_vy / move_len) * norm]
        else:
            action["move"] = [0.0, 0.0]

        mf.draw.point([znc[0], znc[1], z], "#00d0ff", 6)
        if target:
            mf.draw.point(target["pos"], "#ff2020", 5)

        return action


if __name__ == "__main__":
    mf.run(Bot)
