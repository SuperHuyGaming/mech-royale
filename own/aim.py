"""
Aiming, predictive ballistics, and laser trigger discipline.
"""
from __future__ import annotations
import math
from typing import Tuple


def angle_diff(a: float, b: float) -> float:
    """Signed shortest angular difference between two angles in radians [-pi, pi]."""
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def clamp(val: float, low: float, high: float) -> float:
    """Clamps value to range [low, high]."""
    return max(low, min(high, val))


def solve_gun_lead(
    muzzle: Tuple[float, float, float],
    target_pos: Tuple[float, float, float],
    target_vel: Tuple[float, float, float],
    bullet_speed: float = 252.0
) -> Tuple[float, float, Tuple[float, float, float]]:
    """
    Computes ballistic lead aim angles (aim_yaw, aim_pitch) and predicted position
    for the kinetic Arm Gun using a two-step fixed-point lead solver.
    """
    mx, my, mz = muzzle
    tx, ty, tz = target_pos
    tvx, tvy, tvz = target_vel

    dist_3d = math.hypot(tx - mx, ty - my, tz - mz)
    t_flight = dist_3d / max(1.0, bullet_speed)

    pred_x = tx + tvx * t_flight
    pred_y = ty + tvy * t_flight
    pred_z = tz + tvz * t_flight

    # Second iteration for refinement
    d2 = math.hypot(pred_x - mx, pred_y - my, pred_z - mz)
    t2 = d2 / max(1.0, bullet_speed)
    pred_x = tx + tvx * t2
    pred_y = ty + tvy * t2
    pred_z = tz + tvz * t2

    dx = pred_x - mx
    dy = pred_y - my
    dz = pred_z - mz
    d_xy = math.hypot(dx, dy)

    yaw = math.atan2(dy, dx)
    pitch = clamp(math.atan2(dz, max(1.0, d_xy)), -0.7, 1.3)
    return yaw, pitch, (pred_x, pred_y, pred_z)


def solve_laser_aim(
    muzzle: Tuple[float, float, float],
    target_pos: Tuple[float, float, float],
    target_vel: Tuple[float, float, float],
    settle_s: float = 0.08
) -> Tuple[float, float, Tuple[float, float, float]]:
    """
    Computes lead aim angles (aim_yaw, aim_pitch) for the hitscan laser.
    Short settle lead accounts for torso angular slew rate.
    """
    mx, my, mz = muzzle
    tx, ty, tz = target_pos
    tvx, tvy, tvz = target_vel

    pred_x = tx + tvx * settle_s
    pred_y = ty + tvy * settle_s
    pred_z = tz + tvz * settle_s

    dx = pred_x - mx
    dy = pred_y - my
    dz = pred_z - mz
    d_xy = math.hypot(dx, dy)

    yaw = math.atan2(dy, dx)
    pitch = clamp(math.atan2(dz, max(1.0, d_xy)), -0.7, 1.3)
    return yaw, pitch, (pred_x, pred_y, pred_z)


def is_aim_aligned(
    current_aim: Tuple[float, float],
    target_aim: Tuple[float, float],
    dist: float,
    target_radius: float = 2.5
) -> bool:
    """
    Returns True if current aim (yaw, pitch) is sufficiently aligned
    with target direction to guarantee hit.
    """
    cur_yaw, cur_pitch = current_aim
    tgt_yaw, tgt_pitch = target_aim

    yaw_err = abs(angle_diff(cur_yaw, tgt_yaw))
    pitch_err = abs(cur_pitch - tgt_pitch)

    angular_radius = math.atan2(target_radius, max(1.0, dist))
    yaw_tol = max(0.12, angular_radius)
    pitch_tol = max(0.15, angular_radius)

    return (yaw_err < yaw_tol) and (pitch_err < pitch_tol)
