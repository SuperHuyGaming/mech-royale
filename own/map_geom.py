"""
Geometry, building line-of-sight checks, and cover spot generation for Mech Royale.
"""
from __future__ import annotations
import math
import os
import json
from typing import Any, Dict, List, Optional, Set, Tuple

GRID_CELL: float = 80.0
MAP_BUILDINGS: List[Tuple[float, float, float, float]] = []
BUILDING_GRID: Dict[Tuple[int, int], List[Tuple[float, float, float, float]]] = {}
MAP_LOADED: bool = False


def load_map_geometry(hello: Optional[Dict[str, Any]] = None) -> None:
    """Loads 2D building bounding boxes into spatial hash grid from world.json."""
    global MAP_BUILDINGS, BUILDING_GRID, MAP_LOADED
    if MAP_LOADED:
        return

    candidates: List[str] = []
    if hello and isinstance(hello, dict):
        map_dir = hello.get("map", {}).get("dir")
        if map_dir:
            candidates.append(os.path.join(map_dir, "v2", "world.json"))
            candidates.append(os.path.join(map_dir, "world.json"))

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend([
        "/arena/v2/world.json",
        "/arena/world.json",
        os.path.join(base_dir, "..", "mech_phase", "kit", "mech", "mechfight-kit", "arena", "v2", "world.json"),
        os.path.join(base_dir, "mechfight-kit", "arena", "v2", "world.json"),
        os.path.join(base_dir, "..", "arena", "v2", "world.json"),
    ])

    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    prisms = data.get("prisms", [])
                    for prism in prisms:
                        ring = prism.get("ring", [])
                        xs = ring[0::2]
                        ys = ring[1::2]
                        if xs and ys:
                            bx1, by1 = min(xs), min(ys)
                            bx2, by2 = max(xs), max(ys)
                            box = (bx1, by1, bx2, by2)
                            MAP_BUILDINGS.append(box)

                            min_gx = int(math.floor(bx1 / GRID_CELL))
                            max_gx = int(math.floor(bx2 / GRID_CELL))
                            min_gy = int(math.floor(by1 / GRID_CELL))
                            max_gy = int(math.floor(by2 / GRID_CELL))
                            for gx in range(min_gx, max_gx + 1):
                                for gy in range(min_gy, max_gy + 1):
                                    BUILDING_GRID.setdefault((gx, gy), []).append(box)
                MAP_LOADED = True
                break
            except Exception:
                pass


def line_intersects_box(
    x1: float, y1: float, x2: float, y2: float,
    bx1: float, by1: float, bx2: float, by2: float
) -> bool:
    """Liang-Barsky 2D line segment clipping test against an axis-aligned box."""
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


def has_line_of_sight(x1: float, y1: float, x2: float, y2: float) -> bool:
    """Checks whether the line segment from (x1, y1) to (x2, y2) intersects any building."""
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
            boxes = BUILDING_GRID.get((gx, gy))
            if not boxes:
                continue
            for box in boxes:
                if box not in tested:
                    tested.add(box)
                    if line_intersects_box(x1, y1, x2, y2, box[0], box[1], box[2], box[3]):
                        return False
    return True


def find_cover_spot_in_circle(
    my_x: float, my_y: float,
    nx: float, ny: float, nr: float,
    dead_side_ang: Optional[float] = None
) -> Optional[Tuple[float, float]]:
    """
    Finds a safe cover location within the outer ring [0.55 * nr, 0.88 * nr]
    providing orthogonal building cover with > 3.6m clearance from all walls.
    """
    if not MAP_BUILDINGS:
        return None

    clearance = 4.2
    candidates: List[Tuple[float, Tuple[float, float]]] = []

    for bx1, by1, bx2, by2 in MAP_BUILDINGS:
        bcx = (bx1 + bx2) * 0.5
        bcy = (by1 + by2) * 0.5
        d_next = math.hypot(bcx - nx, bcy - ny)
        if d_next > (nr * 0.90) or d_next < (nr * 0.25):
            continue

        # Four faces of the building
        face_spots = [
            (bcx, by1 - clearance),
            (bcx, by2 + clearance),
            (bx1 - clearance, bcy),
            (bx2 + clearance, bcy),
        ]

        for sx, sy in face_spots:
            # Must stay inside safe circle with margin
            if math.hypot(sx - nx, sy - ny) > (nr * 0.84):
                continue

            # Ensure >= 3.6m clearance from ALL buildings to prevent sticking
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
                score += math.cos(spot_ang - dead_side_ang) * 60.0

            candidates.append((score, (sx, sy)))

    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        return candidates[0][1]

    return None
