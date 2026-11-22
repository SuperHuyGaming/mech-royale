"""
Collision avoidance, zone perimeter steering, silent speed clamping, and unsticking.
"""
from __future__ import annotations
import math
from typing import List, Optional, Tuple
from config import SILENT_MOVE_CLAMP
from map_geom import MAP_BUILDINGS, line_intersects_box


class Navigator:
    """Handles local steering, obstacle avoidance around building prisms, and boundary safety."""

    def __init__(self):
        self.pos_history: List[Tuple[float, float]] = []

    def compute_steer(
        self,
        my_pos: Tuple[float, float, float],
        dest_pos: Tuple[float, float],
        zone_center: Tuple[float, float],
        zone_radius: float,
        is_silent: bool = False,
        speed_mult: float = 1.0,
    ) -> Tuple[float, float]:
        """
        Calculates normalized [move_vx, move_vy] steering toward dest_pos.
        Includes building obstacle deflection and hard safe zone boundary projection.
        """
        mx, my, _ = my_pos
        dx = dest_pos[0] - mx
        dy = dest_pos[1] - my
        dist = math.hypot(dx, dy)

        if dist < 0.2:
            return 0.0, 0.0

        base_angle = math.atan2(dy, dx)
        best_angle = base_angle

        # Probe collision with buildings at 3.0m and 6.0m
        angles_to_test = [0.0, 0.35, -0.35, 0.70, -0.70, 1.05, -1.05, 1.40, -1.40]
        for offset in angles_to_test:
            test_ang = base_angle + offset
            cos_a = math.cos(test_ang)
            sin_a = math.sin(test_ang)

            p1_x = mx + cos_a * 3.0
            p1_y = my + sin_a * 3.0
            p2_x = mx + cos_a * 6.0
            p2_y = my + sin_a * 6.0

            collides = False
            for bx1, by1, bx2, by2 in MAP_BUILDINGS:
                # Add 3.5m collision buffer to building walls
                if (bx1 - 3.5 <= p1_x <= bx2 + 3.5) and (by1 - 3.5 <= p1_y <= by2 + 3.5):
                    collides = True
                    break
                if (bx1 - 3.5 <= p2_x <= bx2 + 3.5) and (by1 - 3.5 <= p2_y <= by2 + 3.5):
                    collides = True
                    break

            if not collides:
                best_angle = test_ang
                break

        move_vx = math.cos(best_angle) * speed_mult
        move_vy = math.sin(best_angle) * speed_mult

        # Hard Zone Boundary Projection
        zx, zy = zone_center
        to_z_x = mx - zx
        to_z_y = my - zy
        d_center = max(1.0, math.hypot(to_z_x, to_z_y))
        margin = zone_radius - d_center

        if margin < 25.0:
            ur_x = to_z_x / d_center
            ur_y = to_z_y / d_center
            # Zero out any outward velocity component
            v_out = move_vx * ur_x + move_vy * ur_y
            if v_out > 0.0:
                move_vx -= v_out * ur_x
                move_vy -= v_out * ur_y
            # Push inward away from toxic boundary
            inward_push = min(2.5, (25.0 - margin) / 10.0)
            move_vx -= ur_x * inward_push
            move_vy -= ur_y * inward_push

        # Speed Clamping & Normalization
        v_len = math.hypot(move_vx, move_vy)
        if v_len > 1e-4:
            max_norm = SILENT_MOVE_CLAMP if is_silent else 1.0
            norm = min(max_norm, v_len)
            return (move_vx / v_len) * norm, (move_vy / v_len) * norm

        return 0.0, 0.0

    def check_unstick(
        self,
        my_pos: Tuple[float, float, float],
        grounded: bool,
        is_trying_to_move: bool,
    ) -> Tuple[bool, float]:
        """
        Monitors position history. If bot is stuck against terrain/wall for 10 ticks:
        Returns (jump=True, deflection_offset).
        """
        mx, my, _ = my_pos
        self.pos_history.append((mx, my))
        if len(self.pos_history) > 10:
            self.pos_history.pop(0)

        if not is_trying_to_move or not grounded:
            return False, 0.0

        if len(self.pos_history) >= 10:
            old_x, old_y = self.pos_history[0]
            travel = math.hypot(mx - old_x, my - old_y)
            if travel < 0.20:
                return True, math.pi * 0.5  # Jump and deflect 90 degrees

        return False, 0.0
