"""Static map model: 1 m surface raster, line of sight, 4 m navigation grid, A*.

Everything here is built once from /arena (v2/world.json + v2/terrain.i16) at hello time.
Standard library only.
"""
from __future__ import annotations

import heapq
import json
import math
import os
import sys
import time
from array import array

NAV = 4  # navigation cell size in meters


class World:
    def __init__(self, map_dir: str):
        t0 = time.perf_counter()
        with open(os.path.join(map_dir, "v2", "world.json")) as f:
            w = json.load(f)
        ter = w["terrain"]
        self.nx = int(ter["nx"])
        self.ny = int(ter["ny"])
        self.minx = float(ter["minX"])
        self.miny = float(ter["minY"])
        self.step = float(ter.get("step", 1))
        scale = float(ter.get("scale", 0.01))
        b = w["bounds"]
        self.bminx, self.bmaxx, self.bminy, self.bmaxy = b["minX"], b["maxX"], b["minY"], b["maxY"]

        raw = array("h")
        with open(os.path.join(map_dir, "v2", ter.get("file", "terrain.i16")), "rb") as f:
            raw.frombytes(f.read())
        if sys.byteorder == "big":
            raw.byteswap()
        n = self.nx * self.ny
        # ground height per 1 m vertex
        self.ground = array("f", (v * scale for v in raw[:n]))
        # surface = ground, raised to building tops where a ground-attached building stands
        self.surf = array("f", self.ground)
        self.isbld = bytearray(n)
        self._raster(w.get("prisms", []))
        self._build_nav()
        self.build_s = time.perf_counter() - t0

    # ---------- raster ----------
    def _raster(self, prisms):
        nx, ny, minx, miny = self.nx, self.ny, self.minx, self.miny
        ground, surf, isb = self.ground, self.surf, self.isbld
        for p in prisms:
            ring = p.get("ring") or []
            if len(ring) < 6:
                continue
            base, top = float(p["base"]), float(p["top"])
            polys = [ring] + [h for h in (p.get("holes") or []) if len(h) >= 6]
            edges = []
            ys = ring[1::2]
            for poly in polys:
                m = len(poly) // 2
                for k in range(m):
                    x0, y0 = poly[2 * k], poly[2 * k + 1]
                    x1, y1 = poly[2 * ((k + 1) % m)], poly[2 * ((k + 1) % m) + 1]
                    if y0 != y1:
                        edges.append((x0, y0, x1, y1))
            j0 = max(0, int(math.ceil(min(ys) - miny)))
            j1 = min(ny - 1, int(math.floor(max(ys) - miny)))
            for j in range(j0, j1 + 1):
                yc = miny + j
                xs = []
                for x0, y0, x1, y1 in edges:
                    if (y0 <= yc < y1) or (y1 <= yc < y0):
                        xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
                if len(xs) < 2:
                    continue
                xs.sort()
                row = j * nx
                for k in range(0, len(xs) - 1, 2):
                    i0 = max(0, int(math.ceil(xs[k] - minx)))
                    i1 = min(nx - 1, int(math.floor(xs[k + 1] - minx)))
                    for i in range(i0, i1 + 1):
                        idx = row + i
                        g = ground[idx]
                        # elevated slabs (bridges, overhangs) that clear a mech: walk and shoot under
                        if base > g + 3.0:
                            continue
                        if top > surf[idx]:
                            surf[idx] = top
                        if top > g + 1.0:
                            isb[idx] = 1

    def idx(self, x: float, y: float) -> int:
        i = int(x - self.minx + 0.5)
        j = int(y - self.miny + 0.5)
        if i < 0:
            i = 0
        elif i >= self.nx:
            i = self.nx - 1
        if j < 0:
            j = 0
        elif j >= self.ny:
            j = self.ny - 1
        return j * self.nx + i

    def surface(self, x: float, y: float) -> float:
        return self.surf[self.idx(x, y)]

    def ground_at(self, x: float, y: float) -> float:
        return self.ground[self.idx(x, y)]

    def inside(self, x: float, y: float, margin: float = 6.0) -> bool:
        return self.bminx + margin < x < self.bmaxx - margin and self.bminy + margin < y < self.bmaxy - margin

    def los(self, a, b, stepm: float = 1.5, clear: float = 0.3) -> bool:
        """True if the straight segment a->b (3D) clears terrain and buildings."""
        ax, ay, az = a
        bx, by, bz = b
        dx, dy, dz = bx - ax, by - ay, bz - az
        d = math.hypot(dx, dy)
        n = int(d / stepm)
        if n < 1:
            return True
        surf, nx, ny, minx, miny = self.surf, self.nx, self.ny, self.minx, self.miny
        inv = 1.0 / n
        for k in range(1, n):
            f = k * inv
            x = ax + dx * f
            y = ay + dy * f
            i = int(x - minx + 0.5)
            j = int(y - miny + 0.5)
            if i < 0 or j < 0 or i >= nx or j >= ny:
                return False
            if surf[j * nx + i] > az + dz * f - clear:
                return False
        return True

    # ---------- navigation ----------
    def _build_nav(self):
        c = NAV
        self.gw = int(math.ceil((self.nx - 1) / c))
        self.gh = int(math.ceil((self.ny - 1) / c))
        gw, gh = self.gw, self.gh
        h = array("f", [0.0]) * (gw * gh)
        bld = bytearray(gw * gh)
        nx, ny, surf, isb = self.nx, self.ny, self.surf, self.isbld
        for gj in range(gh):
            j0 = gj * c
            j1 = min(ny - 1, j0 + c)
            for gi in range(gw):
                i0 = gi * c
                i1 = min(nx - 1, i0 + c)
                mx = -1e9
                anyb = 0
                for j in range(j0, j1 + 1):
                    row = j * nx
                    seg = surf[row + i0: row + i1 + 1]
                    m = max(seg)
                    if m > mx:
                        mx = m
                    if not anyb and any(isb[row + i0: row + i1 + 1]):
                        anyb = 1
                h[gj * gw + gi] = mx
                bld[gj * gw + gi] = anyb
        self.nh = h
        self.nbld = bld
        # keep the outer ring of cells out of the plan (map edge is a wall)
        self.nblock = bytearray(gw * gh)
        for gi in range(gw):
            self.nblock[gi] = 1
            self.nblock[(gh - 1) * gw + gi] = 1
        for gj in range(gh):
            self.nblock[gj * gw] = 1
            self.nblock[gj * gw + gw - 1] = 1

    def cell(self, x: float, y: float):
        gi = int((x - self.minx) / NAV)
        gj = int((y - self.miny) / NAV)
        gi = min(max(gi, 0), self.gw - 1)
        gj = min(max(gj, 0), self.gh - 1)
        return gi, gj

    def cell_center(self, gi: int, gj: int):
        return self.minx + (gi + 0.5) * NAV, self.miny + (gj + 0.5) * NAV

    def cell_h(self, gi, gj):
        return self.nh[gj * self.gw + gi]

    def astar(self, start, goal, max_up: float = 1.6, jet_up: float = 0.0, budget: int = 3000,
              avoid=None, circle=None, start_h=None, deadline_ms: float = 8.0):
        """A* on the 4 m grid from world point start to goal. Steps up more than max_up need a jet
        (allowed up to jet_up meters, at extra cost). avoid: list of (x, y, r, cost) soft penalties.
        circle: (cx, cy, r) cells outside it cost extra. Returns (waypoints, reached): waypoints are
        world (x, y, h); the path ends at the best node found if the budget or deadline runs out."""
        t_end = time.perf_counter() + deadline_ms / 1000.0
        gw, gh, nh, nblock = self.gw, self.gh, self.nh, self.nblock
        si, sj = self.cell(start[0], start[1])
        ti, tj = self.cell(goal[0], goal[1])
        s = sj * gw + si
        t = tj * gw + ti
        if s == t:
            return [(goal[0], goal[1], nh[t])], True
        C = float(NAV)
        D2 = 1.41421356 * C
        K = D2 - 2 * C
        # soft penalties per cell, precomputed for this call
        pen = None
        if avoid or circle:
            pen = {}
            minx, miny = self.minx, self.miny
            for ax, ay, ar, ac in (avoid or ()):
                ci, cj = self.cell(ax, ay)
                rr = int(ar / C) + 1
                for dj in range(-rr, rr + 1):
                    for di in range(-rr, rr + 1):
                        if (di * di + dj * dj) * C * C <= ar * ar:
                            ni, nj = ci + di, cj + dj
                            if 0 <= ni < gw and 0 <= nj < gh:
                                kk = nj * gw + ni
                                pen[kk] = pen.get(kk, 0.0) + ac
            circ = circle
        else:
            circ = None
        g = {s: 0.0}
        parent = {s: -1}
        dx0, dy0 = abs(si - ti), abs(sj - tj)
        h0 = (dx0 + dy0) * C + K * (dx0 if dx0 < dy0 else dy0)
        openh = [(h0, 0.0, s)]
        best, besth = s, h0
        nbrs = ((1, 0, C), (-1, 0, C), (0, 1, C), (0, -1, C),
                (1, 1, D2), (1, -1, D2), (-1, 1, D2), (-1, -1, D2))
        exp = 0
        heappop, heappush = heapq.heappop, heapq.heappush
        cr2 = None
        if circ:
            ccx, ccy, cr = circ
            cr2 = cr * cr
        while openh and exp < budget:
            f, gk, k = heappop(openh)
            if gk > g[k]:
                continue
            exp += 1
            if k == t:
                best = t
                break
            if (exp & 63) == 0 and time.perf_counter() > t_end:
                break
            hk = f - gk
            if hk < besth:
                besth, best = hk, k
            ki = k % gw
            kj = k // gw
            hk0 = nh[k] if (k != s or start_h is None) else start_h
            for di, dj, w in nbrs:
                ni = ki + di
                nj = kj + dj
                if ni < 0 or nj < 0 or ni >= gw or nj >= gh:
                    continue
                nk = nj * gw + ni
                if nblock[nk]:
                    continue
                if di and dj:
                    if nh[kj * gw + ni] - hk0 > max_up or nh[nj * gw + ki] - hk0 > max_up:
                        continue
                up = nh[nk] - hk0
                cost = w
                if up > max_up:
                    if up > jet_up:
                        continue
                    cost += 6.0 + up * 2.0
                if pen is not None:
                    pv = pen.get(nk)
                    if pv:
                        cost += pv
                    if cr2 is not None:
                        px = self.minx + (ni + 0.5) * C
                        py = self.miny + (nj + 0.5) * C
                        if (px - ccx) ** 2 + (py - ccy) ** 2 > cr2:
                            cost += 3.0 * C
                ng = gk + cost
                og = g.get(nk)
                if og is None or ng < og:
                    g[nk] = ng
                    parent[nk] = k
                    ddx = ni - ti
                    if ddx < 0:
                        ddx = -ddx
                    ddy = nj - tj
                    if ddy < 0:
                        ddy = -ddy
                    heappush(openh, (ng + (ddx + ddy) * C + K * (ddx if ddx < ddy else ddy), ng, nk))
        path = []
        k = best
        while k != -1:
            x, y = self.cell_center(k % gw, k // gw)
            path.append((x, y, nh[k]))
            k = parent[k]
        path.reverse()
        if best == t:
            path[-1] = (goal[0], goal[1], nh[t])
        return path, best == t

    def walkable_point(self, x, y, ref_h, max_up=1.6):
        gi, gj = self.cell(x, y)
        k = gj * self.gw + gi
        return not self.nblock[k] and self.nh[k] - ref_h <= max_up

    def nearest_open(self, x, y, radius=40.0):
        """Nearest ground-level (non building) nav cell to (x, y)."""
        gi, gj = self.cell(x, y)
        r = int(radius / NAV)
        best = None
        bd = 1e18
        for dj in range(-r, r + 1):
            for di in range(-r, r + 1):
                ni, nj = gi + di, gj + dj
                if ni < 1 or nj < 1 or ni >= self.gw - 1 or nj >= self.gh - 1:
                    continue
                k = nj * self.gw + ni
                if self.nbld[k]:
                    continue
                d = di * di + dj * dj
                if d < bd:
                    bd = d
                    best = (ni, nj)
        if best is None:
            return x, y
        return self.cell_center(*best)
