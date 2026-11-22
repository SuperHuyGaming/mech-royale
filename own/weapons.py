"""
Weapon management, secondary weapon hierarchy, and lethal early laser release.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple
from config import WEAPON_RANK, LETHAL_KILL_HP, DEFEND_PERIMETER_DIST


class WeaponManager:
    """Manages weapon slots, pickups/drops, and intelligent trigger execution."""

    def __init__(self, pickup_radius: float = 4.0):
        self.pickup_radius = pickup_radius

    def evaluate_pickups(
        self,
        my_pos: Tuple[float, float, float],
        pickups: List[Dict[str, Any]],
        current_weapons: List[Optional[Dict[str, Any]]],
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Evaluates visible pickups. Enforces hard mortar ban (drop immediately).
        Returns (drop_slot, take_pickup_id).
        """
        drop_slot: Optional[int] = None
        take_id: Optional[int] = None

        has_secondary = len(current_weapons) > 1 and current_weapons[1] is not None
        sec_weapon = current_weapons[1] if has_secondary else None

        # Hard mortar ban: drop immediately if held
        if has_secondary and sec_weapon and sec_weapon.get("kind") == "mortar":
            drop_slot = 1
            has_secondary = False
            sec_weapon = None

        if not pickups:
            return drop_slot, take_id

        mx, my, mz = my_pos
        current_weight = 0
        if has_secondary and sec_weapon:
            current_weight = WEAPON_RANK.get(sec_weapon.get("kind", ""), 0)

        best_weight = current_weight
        best_pickup: Optional[Dict[str, Any]] = None

        for p in pickups:
            p_kind = p.get("kind", "")
            w = WEAPON_RANK.get(p_kind, -100)
            if w <= 0:
                continue

            px, py, pz = p.get("pos", [0, 0, 0])
            d_horiz = math.hypot(px - mx, py - my)
            d_vert = abs(pz - mz)

            if d_horiz <= self.pickup_radius and d_vert <= 4.0:
                if w > best_weight:
                    best_weight = w
                    best_pickup = p

        if best_pickup:
            take_id = best_pickup.get("id")
            if has_secondary:
                drop_slot = 1

        return drop_slot, take_id

    def compute_fire_action(
        self,
        current_weapons: List[Optional[Dict[str, Any]]],
        target_hp: float,
        dist: float,
        is_aligned: bool,
        has_los: bool,
        should_stealth_fire: bool,
        energy: float,
        yaw_err: float,
    ) -> Tuple[bool, bool]:
        """
        Computes boolean fire triggers [fire_slot0, fire_slot1].
        Implements lethal early laser release and trigger discipline.
        """
        fire_slot0 = False
        fire_slot1 = False

        gun = current_weapons[0] if len(current_weapons) > 0 else None
        has_secondary = len(current_weapons) > 1 and current_weapons[1] is not None
        sec = current_weapons[1] if has_secondary else None

        # Slot 0: Primary Arm Gun
        if gun and has_los and dist <= 230.0:
            cooldown = gun.get("cooldown", 0.0)
            if cooldown <= 0.02 and is_aligned and should_stealth_fire:
                fire_slot0 = True

        # Slot 1: Secondary Weapon (Laser / Lightning)
        if has_secondary and sec and has_los:
            sec_kind = sec.get("kind")
            if sec_kind == "laser" and dist <= 270.0:
                charge = sec.get("charge", 0.0)
                laser_dmg = charge * 420.0  # lethal threshold estimator

                # Early Lethal Release: if current charge damage kills target, release trigger!
                if charge >= 0.20 and laser_dmg >= target_hp and is_aligned:
                    fire_slot1 = False  # release trigger to fire lethal blast
                elif charge >= 0.70:
                    # Fully charged: release trigger when aligned
                    fire_slot1 = not is_aligned
                else:
                    # Keep charging if we want to fire or are preparing engagement
                    if should_stealth_fire or dist <= 160.0:
                        fire_slot1 = True
                    else:
                        fire_slot1 = False

            elif sec_kind == "lightning":
                if dist <= 44.0 and yaw_err < math.radians(20.0) and energy > 10.0 and should_stealth_fire:
                    fire_slot1 = True

        return fire_slot0, fire_slot1
