from __future__ import annotations
import array
import json
import math
import os
import sys
NONE = -32768
CM = 100.0

class Arena:

    def __init__(self, map_dir: str, mech_cfg: dict | None=None):
        mech_cfg = mech_cfg or {}
        self.step_up = float(mech_cfg.get('step_up', 1.2))
        self.mech_h = float(mech_cfg.get('height', 9.0))
        self.mech_r = float(mech_cfg.get('radius', 2.5))
        self.max_slope = math.tan(math.radians(float(mech_cfg.get('max_slope_deg', 50.0))))
        with open(os.path.join(map_dir, 'v2', 'world.json')) as d:
            l = json.load(d)
        h = l['terrain']
        (self.x0, self.y0) = (float(h['minX']), float(h['minY']))
        (self.nx, self.ny) = (int(h['nx']), int(h['ny']))
        self.scale = float(h.get('scale', 0.01))
        a = l['bounds']
        (self.bx0, self.bx1, self.by0, self.by1) = (a['minX'], a['maxX'], a['minY'], a['maxY'])
        e = array.array('h')
        with open(os.path.join(map_dir, 'v2', h.get('file', 'terrain.i16')), 'rb') as d:
            e.frombytes(d.read())
        if sys.byteorder != 'little':
            e.byteswap()
        self.ground = e
        self.n = self.nx * self.ny
        self.col = array.array('h', e)
        self.bld = bytearray(self.n)
        self.over: dict[int, list] = {}
        self.rays = 0
        self.prisms = l['prisms']
        self.trees = self._load_trees(l.get('trees') or {})
        self._raster()

    def _load_trees(self, tr):
        o = []
        fields = tr.get('fields') or []
        e = tr.get('data') or []
        l = len(fields)
        if not l:
            return o
        h = {f: i for (i, f) in enumerate(fields)}
        for v in range(0, len(e) - l + 1, l):
            u = e[v:v + l]
            (a0, b0) = (u[h['x']], u[h['y']])
            a = u[h['crownX']] if 'crownX' in h and u[h['crownX']] is not None else a0
            d = u[h['crownY']] if 'crownY' in h and u[h['crownY']] is not None else b0
            o.append((a0, b0, u[h['z']], u[h['height']], u[h['crownR']], u[h['crownBase']], u[h['trunkR']], a, d))
        return o

    def _raster(self):
        (o0, c1, e1) = (self.nx, self.x0, self.y0)
        (a0, p0, c0, l) = (self.col, self.over, self.ground, self.bld)
        for q0 in self.prisms:
            h = int(round(q0['base'] * CM))
            b1 = int(round(q0['top'] * CM))
            w0 = [q0['ring']] + list(q0.get('holes') or [])
            f1 = q0['ring'][1::2]
            h0 = max(0, int(math.floor(min(f1) - e1)))
            k0 = min(self.ny - 1, int(math.ceil(max(f1) - e1)))
            for g0 in range(h0, k0 + 1):
                r0 = e1 + g0
                d1 = []
                for v0 in w0:
                    m0 = len(v0) // 2
                    for s0 in range(m0):
                        (a, d) = (v0[2 * s0], v0[2 * s0 + 1])
                        u0 = (s0 + 1) % m0
                        (o, u) = (v0[2 * u0], v0[2 * u0 + 1])
                        if (d > r0) != (u > r0):
                            d1.append(a + (r0 - d) * (o - a) / (u - d))
                d1.sort()
                z0 = g0 * o0
                for s0 in range(0, len(d1) - 1, 2):
                    e0 = max(0, int(math.ceil(d1[s0] - c1)))
                    f0 = min(o0 - 1, int(math.floor(d1[s0 + 1] - c1)))
                    for d0 in range(e0, f0 + 1):
                        v = z0 + d0
                        b0 = c0[v]
                        if h <= max(a0[v], b0) + 5:
                            if b1 > a0[v]:
                                a0[v] = b1
                                l[v] = 1
                        else:
                            l0 = p0.get(v)
                            if l0 is None:
                                p0[v] = [(h, b1)]
                            else:
                                l0.append((h, b1))
        for (v, l0) in list(p0.items()):
            l0.sort()
            n0 = []
            for (e, a1) in l0:
                if e <= self.col[v] + 5:
                    if a1 > self.col[v]:
                        self.col[v] = a1
                        self.bld[v] = 1
                    continue
                if n0 and e <= n0[-1][1] + 5:
                    if a1 > n0[-1][1]:
                        n0[-1] = (n0[-1][0], a1)
                else:
                    n0.append((e, a1))
            if n0:
                p0[v] = n0
            else:
                del p0[v]

    def cell(self, x: float, y: float) -> int:
        a = int(x - self.x0 + 0.5)
        d = int(y - self.y0 + 0.5)
        if a < 0:
            a = 0
        elif a >= self.nx:
            a = self.nx - 1
        if d < 0:
            d = 0
        elif d >= self.ny:
            d = self.ny - 1
        return d * self.nx + a

    def terrain(self, x: float, y: float) -> float:
        d = x - self.x0
        e = y - self.y0
        a0 = int(d)
        b0 = int(e)
        if a0 < 0:
            (a0, d) = (0, 0.0)
        if b0 < 0:
            (b0, e) = (0, 0.0)
        if a0 >= self.nx - 1:
            (a0, d) = (self.nx - 2, float(self.nx - 1))
        if b0 >= self.ny - 1:
            (b0, e) = (self.ny - 2, float(self.ny - 1))
        c0 = d - a0
        d0 = e - b0
        h = self.ground
        a = b0 * self.nx + a0
        (l, u) = (h[a], h[a + 1])
        (o, v) = (h[a + self.nx], h[a + self.nx + 1])
        return ((l * (1 - c0) + u * c0) * (1 - d0) + (o * (1 - c0) + v * c0) * d0) * self.scale

    def column_top(self, x: float, y: float) -> float:
        return self.col[self.cell(x, y)] / CM

    def floor_at(self, x: float, y: float, z: float) -> float:
        d = self.cell(x, y)
        o = z + self.step_up
        h = self.terrain(x, y)
        if self.bld[d]:
            e = self.col[d] / CM
            if h < e <= o:
                h = e
        l = self.over.get(d)
        if l:
            for (a, u) in l:
                v = u / CM
                if h < v <= o:
                    h = v
        return h

    def solid_at(self, x: float, y: float, z: float) -> bool:
        d = self.cell(x, y)
        l = z * CM
        if l < self.col[d]:
            return True
        e = self.over.get(d)
        if e:
            for (a, h) in e:
                if a <= l <= h:
                    return True
        return False

    def los(self, ax, ay, az, bx, by, bz, terrain=True) -> bool:
        self.rays += 1
        (d1, e1, l0) = (self.x0, self.y0, self.nx)
        c0 = ax - d1 + 0.5
        d0 = ay - e1 + 0.5
        u = bx - ax
        v = by - ay
        a0 = bz - az
        e0 = int(math.floor(c0))
        g0 = int(math.floor(d0))
        f0 = int(math.floor(bx - d1 + 0.5))
        h0 = int(math.floor(by - e1 + 0.5))
        if u > 0:
            (q0, w0, a1) = (1, 1.0 / u, (e0 + 1 - c0) / u)
        elif u < 0:
            (q0, w0, a1) = (-1, -1.0 / u, (c0 - e0) / -u)
        else:
            (q0, w0, a1) = (0, 1e+30, 1e+30)
        if v > 0:
            (r0, z0, b1) = (1, 1.0 / v, (g0 + 1 - d0) / v)
        elif v < 0:
            (r0, z0, b1) = (-1, -1.0 / v, (d0 - g0) / -v)
        else:
            (r0, z0, b1) = (0, 1e+30, 1e+30)
        (o, o0, h) = (self.col, self.over, self.bld)
        (m0, n0) = (self.nx, self.ny)
        u0 = 0.0
        p0 = abs(f0 - e0) + abs(h0 - g0) + 1
        d = az * CM
        b0 = a0 * CM
        for a in range(p0):
            v0 = a1 if a1 < b1 else b1
            if v0 > 1.0:
                v0 = 1.0
            if 0 <= e0 < m0 and 0 <= g0 < n0:
                l = g0 * l0 + e0
                f1 = d + b0 * u0
                g1 = d + b0 * v0
                k1 = f1 if f1 < g1 else g1
                c1 = o[l]
                if not terrain and (not h[l]):
                    c1 = NONE
                if k1 < c1:
                    return False
                k0 = o0.get(l)
                if k0:
                    h1 = f1 if f1 > g1 else g1
                    for (e, s0) in k0:
                        if k1 <= s0 and h1 >= e:
                            return False
            else:
                return False
            if a1 < b1:
                u0 = a1
                a1 += w0
                e0 += q0
            else:
                u0 = b1
                b1 += z0
                g0 += r0
        return True

    def ray_hit(self, ax, ay, az, bx, by, bz):
        (a1, b1, k0) = (self.x0, self.y0, self.nx)
        b0 = ax - a1 + 0.5
        c0 = ay - b1 + 0.5
        o = bx - ax
        u = by - ay
        v = bz - az
        d0 = int(math.floor(b0))
        f0 = int(math.floor(c0))
        e0 = int(math.floor(bx - a1 + 0.5))
        g0 = int(math.floor(by - b1 + 0.5))
        if o > 0:
            (n0, s0, v0) = (1, 1.0 / o, (d0 + 1 - b0) / o)
        elif o < 0:
            (n0, s0, v0) = (-1, -1.0 / o, (b0 - d0) / -o)
        else:
            (n0, s0, v0) = (0, 1e+30, 1e+30)
        if u > 0:
            (o0, u0, w0) = (1, 1.0 / u, (f0 + 1 - c0) / u)
        elif u < 0:
            (o0, u0, w0) = (-1, -1.0 / u, (c0 - f0) / -u)
        else:
            (o0, u0, w0) = (0, 1e+30, 1e+30)
        (l, l0) = (self.col, self.over)
        q0 = 0.0
        m0 = abs(e0 - d0) + abs(g0 - f0) + 1
        d = az * CM
        a0 = v * CM
        for a in range(m0):
            r0 = v0 if v0 < w0 else w0
            if r0 > 1.0:
                r0 = 1.0
            if not (0 <= d0 < self.nx and 0 <= f0 < self.ny):
                return q0
            h = f0 * k0 + d0
            c1 = d + a0 * q0
            d1 = d + a0 * r0
            z0 = l[h]
            if c1 < z0:
                return q0
            if d1 < z0:
                return q0 + (r0 - q0) * (c1 - z0) / (c1 - d1)
            h0 = l0.get(h)
            if h0:
                f1 = c1 if c1 < d1 else d1
                e1 = c1 if c1 > d1 else d1
                for (e, p0) in h0:
                    if f1 <= p0 and e1 >= e:
                        return q0
            if r0 >= 1.0:
                break
            if v0 < w0:
                q0 = v0
                v0 += s0
                d0 += n0
            else:
                q0 = w0
                w0 += u0
                f0 += o0
        return None

    def inside_map(self, x: float, y: float, margin: float=0.0) -> bool:
        return self.bx0 + margin <= x <= self.bx1 - margin and self.by0 + margin <= y <= self.by1 - margin
