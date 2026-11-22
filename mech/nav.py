from __future__ import annotations
import heapq
import math
from .arena import Arena, CM
SQ2 = math.sqrt(2.0)

def _dilate_rows(rows, nbits, radius):
    z = (1 << nbits) - 1
    a = int(math.floor(radius))
    f0 = {}
    for l in range(-a, a + 1):
        f0[l] = int(math.floor(math.sqrt(max(0.0, radius * radius - l * l))))
    b0 = sorted(set(f0.values()))
    p = {}
    for e0 in b0:
        o = []
        for d0 in rows:
            e = d0
            for v in range(1, e0 + 1):
                e |= d0 << v | d0 >> v
            o.append(e & z)
        p[e0] = o
    a0 = len(rows)
    c0 = []
    for q in range(a0):
        e = 0
        for (l, e0) in f0.items():
            u = q + l
            if 0 <= u < a0:
                e |= p[e0][u]
        c0.append(e)
    return c0

class NavGrid:

    def __init__(self, arena: Arena, cell: float=2.0, margin: float=0.4):
        self.A = arena
        self.cs = cell
        a = arena
        (s0, t0) = (a.nx, a.ny)
        e0 = a.ground
        (z, q, u0) = (a.col, a.bld, a.over)
        a1 = a.step_up * CM
        i0 = a.mech_h * CM
        z0 = a.max_slope * 2 * CM
        rows = []
        for m0 in range(t0):
            r = 0
            p = m0 * s0
            n0 = p - s0 if m0 > 0 else p
            o0 = p + s0 if m0 < t0 - 1 else p
            for j0 in range(s0):
                v = p + j0
                f0 = e0[v]
                u = False
                if q[v] and z[v] - f0 > a1:
                    u = True
                else:
                    q0 = u0.get(v)
                    if q0:
                        for (o, d1) in q0:
                            if o < f0 + i0 and d1 > f0 + a1:
                                u = True
                                break
                    if not u:
                        k0 = v - 1 if j0 > 0 else v
                        l0 = v + 1 if j0 < s0 - 1 else v
                        b1 = e0[l0] - e0[k0]
                        c1 = e0[o0 + j0] - e0[n0 + j0]
                        if b1 * b1 + c1 * c1 > z0 * z0:
                            u = True
                if u:
                    r |= 1 << j0
            rows.append(r)
        d0 = (1 << s0) - 1
        rows[0] = d0
        rows[-1] = d0
        edge = 1 | 1 << s0 - 1
        rows = [r | edge for r in rows]
        self.obst_rows = rows
        v0 = a.mech_r + margin
        b0 = _dilate_rows(rows, s0, v0)
        self.cspace_rows = b0
        p0 = int(round(cell))
        self.k = p0
        self.gx = (s0 + p0 - 1) // p0
        self.gy = (t0 + p0 - 1) // p0
        (g0, h0) = (self.gx, self.gy)
        c0 = bytearray(g0 * h0)
        for l in range(h0):
            r = b0[min(t0 - 1, l * p0)]
            w0 = l * g0
            for e in range(g0):
                if not r >> min(s0 - 1, e * p0) & 1:
                    c0[w0 + e] = 1
        self.free = c0
        self.cost = None
        r0 = _dilate_rows(rows, s0, 14.0)
        a0 = bytearray(g0 * h0)
        for l in range(h0):
            r = r0[min(t0 - 1, l * p0)]
            w0 = l * g0
            for e in range(g0):
                if c0[w0 + e] and r >> min(s0 - 1, e * p0) & 1:
                    a0[w0 + e] = 1
        self.cover = a0

    def node(self, x: float, y: float) -> int:
        a = int(round((x - self.A.x0) / self.k))
        e = int(round((y - self.A.y0) / self.k))
        a = 0 if a < 0 else self.gx - 1 if a >= self.gx else a
        e = 0 if e < 0 else self.gy - 1 if e >= self.gy else e
        return e * self.gx + a

    def pos(self, n: int):
        (e, a) = divmod(n, self.gx)
        return (self.A.x0 + a * self.k, self.A.y0 + e * self.k)

    def nearest_cover(self, x: float, y: float, max_r: int=12):
        a0 = self.node(x, y)
        if self.cover[a0]:
            return a0
        (o, e) = divmod(a0, self.gx)
        for b0 in range(1, max_r + 1):
            (q, p) = (None, 1e+18)
            for z in range(-b0, b0 + 1):
                for v in (-b0, b0) if abs(z) != b0 else range(-b0, b0 + 1):
                    (a, l) = (e + v, o + z)
                    if 0 <= a < self.gx and 0 <= l < self.gy and self.cover[l * self.gx + a]:
                        u = v * v + z * z
                        if u < p:
                            (p, q) = (u, l * self.gx + a)
            if q is not None:
                return q
        return self.nearest_free(x, y, max_r)

    def is_free(self, x: float, y: float) -> bool:
        return bool(self.free[self.node(x, y)])

    def cspace_free(self, x: float, y: float) -> bool:
        a = self.A
        e = int(x - a.x0 + 0.5)
        l = int(y - a.y0 + 0.5)
        if not (0 <= e < a.nx and 0 <= l < a.ny):
            return False
        return not self.cspace_rows[l] >> e & 1

    def nearest_free(self, x: float, y: float, max_r: int=12):
        a0 = self.node(x, y)
        if self.free[a0]:
            return a0
        (o, e) = divmod(a0, self.gx)
        (q, p) = (None, 1e+18)
        for b0 in range(1, max_r + 1):
            for z in range(-b0, b0 + 1):
                for v in (-b0, b0) if abs(z) != b0 else range(-b0, b0 + 1):
                    (a, l) = (e + v, o + z)
                    if 0 <= a < self.gx and 0 <= l < self.gy and self.free[l * self.gx + a]:
                        u = v * v + z * z
                        if u < p:
                            (p, q) = (u, l * self.gx + a)
            if q is not None:
                return q
        return None

    def segment_free(self, ax, ay, bx, by) -> bool:
        e = math.hypot(bx - ax, by - ay)
        p = int(e / 0.8) + 1
        q = self.cspace_rows
        a = self.A
        for u in range(p + 1):
            v = u / p
            l = int(ax + (bx - ax) * v - a.x0 + 0.5)
            o = int(ay + (by - ay) * v - a.y0 + 0.5)
            if not (0 <= l < a.nx and 0 <= o < a.ny) or q[o] >> l & 1:
                return False
        return True

    def astar(self, sx, sy, tx, ty, budget=60000, goal_radius=0.0):
        c1 = self.nearest_free(sx, sy)
        d1 = self.nearest_free(tx, ty)
        if c1 is None or d1 is None:
            return None
        r0 = self.gx
        m0 = self.free
        b0 = self.cost
        (q, p) = divmod(d1, r0)
        p0 = (goal_radius / self.k) ** 2
        z0 = [(0.0, 0.0, c1)]
        q0 = {c1: 0.0}
        a1 = {c1: -1}
        a0 = set()
        (u, v) = (c1, 1e+18)
        w0 = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, SQ2), (1, -1, SQ2), (-1, 1, SQ2), (-1, -1, SQ2))
        j0 = 0
        l0 = None
        while z0:
            (k0, o0, v0) = heapq.heappop(z0)
            if v0 in a0:
                continue
            a0.add(v0)
            (l, a) = divmod(v0, r0)
            c0 = abs(a - p)
            d0 = abs(l - q)
            if v0 == d1 or (p0 and c0 * c0 + d0 * d0 <= p0):
                l0 = v0
                break
            s0 = c0 + d0 + (SQ2 - 2) * (c0 if c0 < d0 else d0)
            if s0 < v:
                (v, u) = (s0, v0)
            j0 += 1
            if j0 > budget:
                break
            for (e0, f0, z) in w0:
                (e, o) = (a + e0, l + f0)
                if e < 0 or o < 0 or e >= r0 or (o >= self.gy):
                    continue
                u0 = o * r0 + e
                if not m0[u0] or u0 in a0:
                    continue
                if e0 and f0 and (not (m0[l * r0 + e] and m0[o * r0 + a])):
                    continue
                n0 = o0 + z
                if b0 is not None:
                    n0 += b0[u0] * 0.1
                if n0 < q0.get(u0, 1e+18):
                    q0[u0] = n0
                    a1[u0] = v0
                    g0 = abs(e - p)
                    h0 = abs(o - q)
                    t0 = g0 + h0 + (SQ2 - 2) * (g0 if g0 < h0 else h0)
                    heapq.heappush(z0, (n0 + t0 * 1.001, n0, u0))
        i0 = l0 if l0 is not None else u
        b1 = []
        v0 = i0
        while v0 != -1:
            b1.append(self.pos(v0))
            v0 = a1[v0]
        b1.reverse()
        if l0 is not None and goal_radius <= 0:
            b1[-1] = (tx, ty) if self.cspace_free(tx, ty) else b1[-1]
        return (self.smooth(b1, sx, sy), l0 is not None)

    def astar_iter(self, sx, sy, tx, ty, chunk=600):
        b1 = self.nearest_free(sx, sy)
        c1 = self.nearest_free(tx, ty)
        if b1 is None or c1 is None:
            yield ([(tx, ty)], False)
            return
        (p0, q0) = (self.gx, self.gy)
        l0 = self.free
        (q, p) = divmod(c1, p0)
        w0 = [(0.0, 0.0, b1)]
        o0 = {b1: 0.0}
        z0 = {b1: -1}
        a0 = set()
        (u, v) = (b1, 1e+18)
        v0 = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, SQ2), (1, -1, SQ2), (-1, 1, SQ2), (-1, -1, SQ2))
        k0 = None
        i0 = 0
        d1 = 0
        while w0:
            (j0, n0, u0) = heapq.heappop(w0)
            if u0 in a0:
                continue
            a0.add(u0)
            if u0 == c1:
                k0 = u0
                break
            (l, a) = divmod(u0, p0)
            b0 = abs(a - p)
            c0 = abs(l - q)
            r0 = b0 + c0 + (SQ2 - 2) * (b0 if b0 < c0 else c0)
            if r0 < v:
                (v, u) = (r0, u0)
            i0 += 1
            d1 += 1
            if d1 > 120000:
                break
            if i0 >= chunk:
                i0 = 0
                yield None
            for (d0, e0, z) in v0:
                (e, o) = (a + d0, l + e0)
                if e < 0 or o < 0 or e >= p0 or (o >= q0):
                    continue
                t0 = o * p0 + e
                if not l0[t0] or t0 in a0:
                    continue
                if d0 and e0 and (not (l0[l * p0 + e] and l0[o * p0 + a])):
                    continue
                m0 = n0 + z
                if m0 < o0.get(t0, 1e+18):
                    o0[t0] = m0
                    z0[t0] = u0
                    f0 = abs(e - p)
                    g0 = abs(o - q)
                    s0 = f0 + g0 + (SQ2 - 2) * (f0 if f0 < g0 else g0)
                    heapq.heappush(w0, (m0 + s0 * 1.001, m0, t0))
        h0 = k0 if k0 is not None else u
        a1 = []
        u0 = h0
        while u0 != -1:
            a1.append(self.pos(u0))
            u0 = z0[u0]
        a1.reverse()
        if k0 is not None and self.cspace_free(tx, ty):
            a1[-1] = (tx, ty)
        yield (a1, k0 is not None)

    def smooth(self, path, sx, sy):
        if not path:
            return path
        u = [(sx, sy)] + list(path[1:]) if self.cspace_free(sx, sy) else list(path)
        q = [u[0]]
        a = 0
        p = len(u)
        while a < p - 1:
            e = a + 1
            l = a + 3
            o = min(p - 1, a + 40)
            while l <= o and self.segment_free(u[a][0], u[a][1], u[l][0], u[l][1]):
                e = l
                l += 3
            if e < o and e + 1 <= o and self.segment_free(u[a][0], u[a][1], u[min(o, e + 1)][0], u[min(o, e + 1)][1]):
                e = min(o, e + 1)
            q.append(u[e])
            a = e
        return q
