"""
Huy's 7 Survival Plan implementation: stealth, camp anchoring, passive regen, and endgame.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple
from config import (
    CAMP_MIN_R_FRAC,
    CAMP_MAX_R_FRAC,
    CAMP_ARRIVE_DIST,
    SILENT_SPEED_LIMIT,
    SILENT_MOVE_CLAMP,
    SILENT_ENEMY_DIST,
    STANDOFF_FARM_DIST,
    DEFEND_PERIMETER_DIST,
    LETHAL_KILL_HP,
    RETREAT_HP_FRAC,
    RECOVER_HP_FRAC,
    ENDGAME_ALIVE_COUNT,
    ENDGAME_CIRCLE_RADIUS,
    UPGRADE_RANK,
)
from map_geom import has_line_of_sight, find_cover_spot_in_circle


class SurvivalController:
    """Orchestrates high-level survival mode, camping, stealth, and regen."""

    def __init__(self):
        self.camp_spot: Optional[Tuple[float, float]] = None
        self.is_anchored: bool = False
        self.last_cover_search_t: float = -100.0
        self.last_damage_taken_t: float = -100.0
        self.last_damage_dealt_t: float = -100.0
        self.recent_attacker_bearing: Optional[float] = None
        self.is_retreating: bool = False

    def process_events(self, events: List[Dict[str, Any]], t_now: float) -> None:
        """Processes match events for damage tracking and threat bearings."""
        for ev in events:
            etype = ev.get("type")
            if etype == "damaged":
                self.last_damage_taken_t = t_now
                bearing = ev.get("bearing")
                if bearing is not None:
                    self.recent_attacker_bearing = bearing
            elif etype == "hit":
                self.last_damage_dealt_t = t_now

    def update_retreat_state(self, hp: float, max_hp: float, n_visible_enemies: int) -> bool:
        """
        Manages tactical retreat state with hysteresis.
        Returns True if bot should be retreating and healing.
        """
        thresh = RETREAT_HP_FRAC
        if n_visible_enemies >= 3:
            thresh = 0.75
        elif n_visible_enemies >= 2:
            thresh = 0.60

        if not self.is_retreating:
            if hp < (max_hp * thresh):
                self.is_retreating = True
        else:
            if hp >= (max_hp * RECOVER_HP_FRAC):
                self.is_retreating = False

        return self.is_retreating

    def is_endgame(self, alive_count: int, zone_radius: float) -> bool:
        """Checks if match has entered aggressive endgame phase."""
        return (alive_count <= ENDGAME_ALIVE_COUNT) or (zone_radius <= ENDGAME_CIRCLE_RADIUS)

    def is_passive_regen_active(self, t_now: float) -> bool:
        """Returns True if the 5-second combat cooldown has elapsed for 45 HP/s passive regen."""
        return ((t_now - self.last_damage_taken_t) > 5.0) and ((t_now - self.last_damage_dealt_t) > 5.0)

    def can_stealth_fire(
        self,
        target_hp: float,
        dist: float,
        t_now: float,
        is_endgame: bool,
        is_facing_me: bool = False,
    ) -> bool:
        """
        Enforces stealth fire suppression to prevent 320m-420m sound broadcasts.
        Returns True when firing is tactically justified.
        """
        if is_endgame:
            return True

        # If recently attacked, return fire
        if (t_now - self.last_damage_taken_t) < 5.0:
            return True

        # Target can be quickly killed
        if target_hp < LETHAL_KILL_HP:
            return True

        # Enemy is within close/mid combat range
        if dist < 65.0:
            return True

        # Enemy is looking at us
        if is_facing_me:
            return True

        return False

    def is_enemy_nearby(
        self,
        my_pos: Tuple[float, float, float],
        visible_mechs: List[Dict[str, Any]],
        sounds: List[Dict[str, Any]],
        threshold_dist: float = SILENT_ENEMY_DIST
    ) -> bool:
        """Checks if any enemy mech is close enough to hear footsteps."""
        mx, my, _ = my_pos
        for m in visible_mechs:
            ex, ey, _ = m["pos"]
            if math.hypot(ex - mx, ey - my) <= threshold_dist:
                return True
        for s in sounds:
            if s.get("dist") == "near":
                return True
        return False

    def can_safely_farm(
        self,
        my_pos: Tuple[float, float, float],
        target_pos: Tuple[float, float, float],
        visible_mechs: List[Dict[str, Any]],
        sounds: List[Dict[str, Any]],
    ) -> bool:
        """
        Verifies Huy's Point 3: Safe Farming.
        Only farm when no enemy within 150m AND no enemy has LOS to target.
        """
        mx, my, _ = my_pos
        tx, ty, _ = target_pos

        for m in visible_mechs:
            ex, ey, _ = m["pos"]
            d_me = math.hypot(ex - mx, ey - my)
            d_tgt = math.hypot(ex - tx, ey - ty)
            if d_me < STANDOFF_FARM_DIST or d_tgt < STANDOFF_FARM_DIST:
                return False
            if has_line_of_sight(ex, ey, tx, ty):
                return False

        for s in sounds:
            if s.get("dist") in ("near", "mid"):
                return False

        return True

    def get_camp_spot(
        self,
        my_pos: Tuple[float, float, float],
        next_center: Tuple[float, float],
        next_radius: float,
        dead_side_ang: Optional[float],
        t_now: float,
    ) -> Optional[Tuple[float, float]]:
        """Returns or recalculates the safe outer-ring camp spot."""
        mx, my, _ = my_pos
        nx, ny = next_center

        # Check if existing camp spot is still valid inside next safe circle
        if self.camp_spot is not None:
            cx, cy = self.camp_spot
            if math.hypot(cx - nx, cy - ny) <= (next_radius * 0.85):
                if (t_now - self.last_cover_search_t) < 8.0:
                    return self.camp_spot

        self.camp_spot = find_cover_spot_in_circle(mx, my, nx, ny, next_radius, dead_side_ang)
        self.last_cover_search_t = t_now
        return self.camp_spot

    def pick_upgrade(self, choices: List[Dict[str, Any]]) -> int:
        """Selects upgrade choice strictly prioritizing defense/armor/regen/health."""
        best_idx = 0
        best_score = -999.0

        for i, ch in enumerate(choices):
            cid = ch.get("id", "").lower()
            desc = ch.get("desc", "").lower()
            score = 10.0

            for key, val in UPGRADE_RANK.items():
                if key in cid or key in desc:
                    score = max(score, float(val))

            if score > best_score:
                best_score = score
                best_idx = i

        return best_idx
