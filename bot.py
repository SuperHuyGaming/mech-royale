"""
GMUNVIS - Bot A: "Hide + Spam" (Huy's strategy)
Team: GMUNVIS
Protocol: mechfight/1

Strategy:
1. Move early to cover (behind a building, under dense tree crown, or circle perimeter)
   strictly inside the NEXT safe zone circle.
2. Once at cover: stay still (silent footstep movement < 5 m/s).
3. Fire all weapons constantly (slot 0 arm gun has unlimited ammo with reloads; slot 1 secondary).
4. Relocate to new cover inside the next circle before each shrink phase.
5. Avoid getting caught outside the zone, path around buildings/walls, stay under 50 ms budget.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import mechfight as mf


def angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def clamp(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


class Bot:
    name = "GMUNVIS-HideSpam"

    def __init__(self, hello: mf.Hello):
        self.slot = hello.get("slot", 0)
        self.cfg = hello.get("config", {})
        self.mech_cfg = self.cfg.get("mech", {})
        self.weapons_cfg = self.cfg.get("weapons", {})
        self.loot_cfg = self.cfg.get("loot", {})

        self.gun_speed = self.weapons_cfg.get("gun", {}).get("speed", 252.0)
        self.gun_range = self.weapons_cfg.get("gun", {}).get("range", 230.0)
        self.pickup_radius = self.loot_cfg.get("pickup_radius", 4.0)

        # Load map geometry (buildings and trees)
        self.buildings: List[Tuple[float, float, float, float]] = []
        self.trees: List[Tuple[float, float, float]] = []
        self._load_map(hello)

        # Tactical state
        self.current_cover_target: Optional[Tuple[float, float]] = None
        self.current_cover_phase_idx: int = -1
        self.last_shrink_start: float = -1.0
        self.aim_rotation_yaw: float = 0.0

    def _load_map(self, hello: mf.Hello):
        map_info = hello.get("map", {})
        map_dir = map_info.get("dir", "/arena")
        candidates = [
            os.path.join(map_dir, "v2", "world.json"),
            os.path.join("mechfight-kit", "arena", "v2", "world.json"),
            os.path.join(os.path.dirname(__file__), "mechfight-kit", "arena", "v2", "world.json"),
        ]
        world_path = None
        for c in candidates:
            if os.path.exists(c):
                world_path = c
                break

        if world_path:
            try:
                with open(world_path, "r") as f:
                    wdata = json.load(f)
                    for p in wdata.get("prisms", []):
                        ring = p.get("ring", [])
                        xs = ring[0::2]
                        ys = ring[1::2]
                        if xs and ys:
                            min_x, max_x = min(xs), max(xs)
                            min_y, max_y = min(ys), max(ys)
                            self.buildings.append((min_x - 3.0, min_y - 3.0, max_x + 3.0, max_y + 3.0))

                    tdata = wdata.get("trees", {}).get("data", [])
                    tfields = wdata.get("trees", {}).get("fields", [])
                    if tdata and tfields:
                        step = len(tfields)
                        xi = tfields.index("x")
                        yi = tfields.index("y")
                        ri = tfields.index("crownR")
                        for i in range(0, len(tdata), step):
                            if tdata[i + ri] >= 3.5:  # Dense crowns only
                                self.trees.append((tdata[i + xi], tdata[i + yi], tdata[i + ri]))
            except Exception as e:
                mf.log(f"Warning: could not load world.json: {e}")

    def find_cover_in_next_circle(self, nx: float, ny: float, nr: float, px: float, py: float) -> Tuple[float, float]:
        """Finds best cover inside the next circle."""
        best_spot = (nx + nr * 0.5, ny)
        best_score = -99999.0

        candidates: List[Tuple[float, float, str]] = []

        # 1. Building cover points
        for bx1, by1, bx2, by2 in self.buildings:
            cx = (bx1 + bx2) * 0.5
            cy = (by1 + by2) * 0.5
            d_center = math.hypot(cx - nx, cy - ny)
            if d_center < nr * 0.85:
                # 4 perimeter points outside building
                candidates.append((bx1 - 2.5, cy, "bldg"))
                candidates.append((bx2 + 2.5, cy, "bldg"))
                candidates.append((cx, by1 - 2.5, "bldg"))
                candidates.append((cx, by2 + 2.5, "bldg"))

        # 2. Dense trees
        for tx, ty, tr in self.trees:
            d_center = math.hypot(tx - nx, ty - ny)
            if d_center < nr * 0.80:
                candidates.append((tx, ty, "tree"))

        # 3. Perimeter fallback positions
        for a_deg in range(0, 360, 45):
            rad = math.radians(a_deg)
            candidates.append((nx + nr * 0.65 * math.cos(rad), ny + nr * 0.65 * math.sin(rad), "perim"))

        for sx, sy, kind in candidates:
            d_to_next = math.hypot(sx - nx, sy - ny)
            if d_to_next > nr * 0.85:
                continue

            d_travel = math.hypot(sx - px, sy - py)
            score = 500.0 - (d_travel * 0.8)

            if 0.40 * nr <= d_to_next <= 0.75 * nr:
                score += 150.0  # Sweet spot ring away from center
            elif d_to_next < 0.25 * nr:
                score -= 100.0  # Dead center is too dangerous

            if kind == "bldg":
                score += 80.0
            elif kind == "tree":
                score += 70.0

            if score > best_score:
                best_score = score
                best_spot = (sx, sy)

        return best_spot

    def navigate_around_buildings(self, x: float, y: float, tx: float, ty: float) -> Tuple[float, float]:
        dx = tx - x
        dy = ty - y
        dist = math.hypot(dx, dy)
        if dist < 1e-3:
            return 0.0, 0.0

        base_yaw = math.atan2(dy, dx)
        test_angles = [0.0, 0.45, -0.45, 0.90, -0.90, 1.35, -1.35, 1.80, -1.80]

        best_heading = base_yaw
        best_clearance = -1.0

        for ang_offset in test_angles:
            test_yaw = base_yaw + ang_offset
            test_vx = math.cos(test_yaw)
            test_vy = math.sin(test_yaw)

            # Check if this ray hits any building within 12m
            clear = True
            for bx1, by1, bx2, by2 in self.buildings:
                probe_x = x + test_vx * 8.0
                probe_y = y + test_vy * 8.0
                if bx1 <= probe_x <= bx2 and by1 <= probe_y <= by2:
                    clear = False
                    break

            if clear:
                return math.cos(test_yaw), math.sin(test_yaw)

        # Fallback to direct normalized vector
        return dx / dist, dy / dist

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
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

        # 1. Smart Upgrades: Armor > HP > Regen > Weapons
        offer = obs.get("offer")
        if offer and "choices" in offer:
            choices = offer["choices"]
            best_idx = 0
            best_score = -999.0
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
                elif "gun" in cid:
                    score = 50.0
                else:
                    score = 20.0
                if score > best_score:
                    best_score = score
                    best_idx = i
            action["pick"] = best_idx

        # 2. Loot Secondary Weapon if empty
        if len(weapons) > 1 and weapons[1] is None:
            pickups = obs.get("pickups", [])
            for p in pickups:
                px, py, pz = p["pos"]
                if math.hypot(px - x, py - y) <= self.pickup_radius and abs(pz - z) <= 4.0:
                    action["take"] = p["id"]
                    break

        # 3. Zone Tracking & Early Relocation
        zone = obs.get("zone", {})
        curr_center = zone.get("center", [0.0, 0.0])
        curr_radius = zone.get("radius", 560.0)
        next_center = zone.get("next_center", curr_center)
        next_radius = zone.get("next_radius", curr_radius)
        shrink_start = zone.get("shrink_start", 60.0)

        # Check if we need to select new cover
        dist_to_curr_cover = math.hypot(self.current_cover_target[0] - x, self.current_cover_target[1] - y) if self.current_cover_target else 999.0
        dist_curr_cover_to_next_center = math.hypot(self.current_cover_target[0] - next_center[0], self.current_cover_target[1] - next_center[1]) if self.current_cover_target else 999.0

        need_new_cover = (
            self.current_cover_target is None
            or dist_curr_cover_to_next_center > next_radius * 0.85
            or shrink_start != self.last_shrink_start
        )

        if need_new_cover:
            self.current_cover_target = self.find_cover_in_next_circle(next_center[0], next_center[1], next_radius, x, y)
            self.last_shrink_start = shrink_start

        # 4. Movement: Relocate to cover early, then STAY STILL
        dist_to_target = math.hypot(self.current_cover_target[0] - x, self.current_cover_target[1] - y)
        dist_to_curr_center = math.hypot(x - curr_center[0], y - curr_center[1])

        # Mandatory: Never stand outside the zone!
        if not in_zone or dist_to_curr_center > curr_radius - 20.0:
            # Escape to zone center immediately!
            yaw = math.atan2(next_center[1] - y, next_center[0] - x)
            action["move"] = [math.cos(yaw), math.sin(yaw)]
            action["boost"] = True
        elif dist_to_target > 3.0:
            # Moving early to cover
            mv_x, mv_y = self.navigate_around_buildings(x, y, self.current_cover_target[0], self.current_cover_target[1])
            action["move"] = [mv_x, mv_y]
            # Boost if far from cover and plenty of energy
            action["boost"] = dist_to_target > 50.0 and energy > 40.0
        else:
            # At cover: STAY STILL (makes zero step sounds, allows regen)
            action["move"] = [0.0, 0.0]
            action["boost"] = False

        # 5. SPAM FIRE CONSTANTLY (Huy's Bot A)
        # Gun has unlimited ammo with automatic reload.
        # If secondary is laser, release trigger when charged to fire the 315 dmg beam!
        sec = weapons[1] if len(weapons) > 1 else None
        if sec and sec.get("kind") == "laser":
            charge = sec.get("charge", 0.0)
            fire_sec = charge < 0.70
        else:
            fire_sec = True
        action["fire"] = [True, fire_sec]

        # Aim towards nearest visible enemy if any, else rotate aim to spam in circle
        visible_enemies = [m for m in obs.get("mechs", []) if m.get("kind") == "mech"]
        if visible_enemies:
            visible_enemies.sort(key=lambda m: math.hypot(m["pos"][0] - x, m["pos"][1] - y))
            target = visible_enemies[0]
            tx, ty, tz = target["pos"]
            dist_t = max(1.0, math.hypot(tx - x, ty - y))
            t_lead = dist_t / self.gun_speed
            tvx, tvy, tvz = target.get("vel", [0.0, 0.0, 0.0])
            pred_tx = tx + tvx * t_lead
            pred_ty = ty + tvy * t_lead
            aim_yaw = math.atan2(pred_ty - y, pred_tx - x)
            aim_pitch = math.atan2(tz + 4.5 - (z + muzzle_h), dist_t)
            action["aim"] = [aim_yaw, clamp(aim_pitch, -0.6, 1.0)]
            mf.draw.line([x, y, z + muzzle_h], [pred_tx, pred_ty, tz + 4.5], "#ff2020")
            mf.draw.label(f"HideSpam: HP {hp:.0f} | Engaging #{int(target['id'])}")
        else:
            # Slowly scan / spam around
            self.aim_rotation_yaw += 0.08
            action["aim"] = [self.aim_rotation_yaw, 0.0]
            mf.draw.label(f"HideSpam: HP {hp:.0f} | Hiding & Spamming")

        if self.current_cover_target:
            mf.draw.point([self.current_cover_target[0], self.current_cover_target[1], z], "#00ffcc", 6)

        return action


if __name__ == "__main__":
    mf.run(Bot)
