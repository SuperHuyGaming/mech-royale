from __future__ import annotations
import math
TAU = 2.0 * math.pi
PI = math.pi

def wrap(a: float) -> float:
    return (a + PI) % TAU - PI

def adiff(a: float, b: float) -> float:
    return (a - b + PI) % TAU - PI

def bearing(ax: float, ay: float, bx: float, by: float) -> float:
    return math.atan2(by - ay, bx - ax)

def d2(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)

def unit(x: float, y: float):
    c = math.hypot(x, y)
    if c < 1e-09:
        return (0.0, 0.0)
    return (x / c, y / c)

def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v

def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t
