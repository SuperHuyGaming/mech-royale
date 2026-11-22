from __future__ import annotations
import math
G_DEFAULT = 22.0

def body_scale(cubes: float, size_per_cube: float=0.05) -> float:
    return 1.0 + size_per_cube * (cubes or 0)

def predict(pos, vel, dt, arena=None, g=G_DEFAULT, damp=1.0):
    e = pos[0] + vel[0] * dt * damp
    h = pos[1] + vel[1] * dt * damp
    i = pos[2]
    c = vel[2] if len(vel) > 2 else 0.0
    if abs(c) > 0.3:
        i = pos[2] + c * dt - 0.5 * g * dt * dt
        if arena is not None:
            b = arena.terrain(e, h)
            if i < b:
                i = b
    return (e, h, i)

def lead(shooter, tpos, tvel, speed, latency=0.05, head=3.0, aim_h=4.5, arena=None, damp=1.0, iters=4):
    (j, k, l) = shooter
    m = latency
    (b, c, e) = (tpos[0], tpos[1], tpos[2])
    for h in range(iters):
        (b, c, e) = predict(tpos, tvel, m, arena, damp=damp)
        i = math.sqrt((b - j) ** 2 + (c - k) ** 2 + (e + aim_h - l) ** 2)
        n = max(0.0, i - head) / speed
        m = latency + n
    return (b, c, e + aim_h, m)

def aim_to(shooter, px, py, pz):
    (e, h, i) = shooter
    (b, c) = (px - e, py - h)
    return (math.atan2(c, b), math.atan2(pz - i, math.hypot(b, c)))

def angular_radius(dist: float, radius: float) -> float:
    if dist < 0.001:
        return math.pi
    return math.atan2(radius, dist)

def ballistic_pitch(D, H, v, g, high=False):
    if D < 0.5:
        return None
    b = g * D * D / (2 * v * v)
    c = D * D - 4 * b * (H + b)
    if c < 0:
        return None
    e = math.sqrt(c)
    h = (D + e) / (2 * b) if high else (D - e) / (2 * b)
    return math.atan(h)

def mortar_solution(tube, target, v, g, head=3.0, high=False, min_pitch=-0.7, max_pitch=1.3):
    (p, q, s) = target
    (l, m, n) = tube
    w = math.atan2(q - m, p - l)
    c = math.hypot(p - l, q - m)
    h = s - n
    k = ballistic_pitch(c, h, v, g, high)
    if k is None:
        return None
    for i in range(2):
        b = c - head * math.cos(k)
        e = h - head * math.sin(k)
        j = ballistic_pitch(max(0.6, b), e, v, g, high)
        if j is None:
            return None
        k = j
    if k < min_pitch or k > max_pitch:
        return None
    b = c - head * math.cos(k)
    o = b / max(0.001, v * math.cos(k))
    return (w, k, o)

def shell_impact(pos, vel, g, arena, max_t=8.0, dt=0.05):
    (m, n, o) = pos
    (j, k, l) = vel
    h = 0.0
    while h < max_t:
        (b, c) = (m + j * dt, n + k * dt)
        e = o + l * dt - 0.5 * g * dt * dt
        l -= g * dt
        h += dt
        i = arena.col[arena.cell(b, c)] / 100.0
        if e <= i:
            return (b, c, max(e, i), h)
        (m, n, o) = (b, c, e)
    return (m, n, o, h)

def closest_approach(ppos, pvel, qx, qy, qz, horizon):
    (h, i, j) = (qx - ppos[0], qy - ppos[1], qz - ppos[2])
    l = pvel[0] ** 2 + pvel[1] ** 2 + pvel[2] ** 2
    if l < 1e-06:
        return (0.0, math.sqrt(h * h + i * i + j * j))
    k = (h * pvel[0] + i * pvel[1] + j * pvel[2]) / l
    k = max(0.0, min(horizon, k))
    b = h - pvel[0] * k
    c = i - pvel[1] * k
    e = j - pvel[2] * k
    return (k, math.sqrt(b * b + c * c + e * e))
