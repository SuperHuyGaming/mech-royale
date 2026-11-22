from __future__ import annotations
import math
from .geometry import adiff, bearing, d2

def _seg_dist(px, py, ax, ay, bx, by):
    (g, h) = (bx - ax, by - ay)
    d = g * g + h * h
    if d < 1e-09:
        return math.hypot(px - ax, py - ay)
    j = max(0.0, min(1.0, ((px - ax) * g + (py - ay) * h) / d))
    return math.hypot(px - ax - j * g, py - ay - j * h)

class Track:
    __slots__ = ('id', 'kind', 'pos', 'vel', 'aim', 'hp', 'max_hp', 'cubes', 'weapons', 'charging', 'last_seen', 'first_seen', 'listed', 'dmg_to_me', 'dmg_from_me', 'last_hit_me', 'last_hit_by_me', 'fighting_t', 'hp_prev', 'hp_drop_t', 'acc', 'vel_prev', 't_prev')

    def __init__(self, a: dict, t: float):
        self.id = a['id']
        self.kind = a.get('kind', 'mech')
        self.first_seen = t
        self.dmg_to_me = 0.0
        self.dmg_from_me = 0.0
        self.last_hit_me = -99.0
        self.last_hit_by_me = -99.0
        self.fighting_t = -99.0
        self.hp_prev = None
        self.hp_drop_t = -99.0
        self.acc = (0.0, 0.0)
        self.vel_prev = None
        self.t_prev = None
        self.charging = False
        self.weapons = []
        self.cubes = 0
        self.update(a, t)

    def update(self, a: dict, t: float):
        m = a.get('vel') or [0.0, 0.0, 0.0]
        if self.vel_prev is not None and self.t_prev is not None and (t - self.t_prev > 0.001) and (t - self.t_prev < 0.35):
            h = t - self.t_prev
            d = (m[0] - self.vel_prev[0]) / h
            g = (m[1] - self.vel_prev[1]) / h
            l = 0.5
            self.acc = (self.acc[0] * (1 - l) + d * l, self.acc[1] * (1 - l) + g * l)
        else:
            self.acc = (0.0, 0.0)
        self.vel_prev = m
        self.t_prev = t
        self.pos = a['pos']
        self.vel = m
        self.aim = a.get('aim') or [0.0, 0.0]
        j = a.get('hp')
        if j is not None:
            if self.hp_prev is not None and j < self.hp_prev - 1:
                self.hp_drop_t = t
            self.hp_prev = j
        self.hp = j if j is not None else self.hp if hasattr(self, 'hp') else 1400.0
        self.max_hp = a.get('max_hp') or (self.max_hp if hasattr(self, 'max_hp') else 1400.0)
        if 'cubes' in a:
            self.cubes = a.get('cubes') or 0
        if 'weapons' in a:
            self.weapons = a.get('weapons') or []
        self.charging = bool(a.get('charging', False))
        self.last_seen = t
        self.listed = True

class Item:
    __slots__ = ('id', 'kind', 'pos', 'hp', 'last_seen')

    def __init__(self, id_, kind, pos, hp, t):
        (self.id, self.kind, self.pos, self.hp, self.last_seen) = (id_, kind, pos, hp, t)

class World:

    def __init__(self, cfg: dict, my_id: int):
        self.cfg = cfg
        self.me_id = my_id
        self.t = 0.0
        self.mechs: dict[int, Track] = {}
        self.npcs: dict[int, Track] = {}
        self.pickups: dict[int, Item] = {}
        self.crates: dict[int, Item] = {}
        self.cubes: dict[int, Item] = {}
        self.threats = []
        self.my_kills = 0
        self.deaths_not_mine = 0
        self.prev_alive = None
        self.ffa_score = 0.0
        self.other_death_ts = []
        self.dmg_taken_log = []
        self.dmg_dealt_log = []
        self.last_damage_t = -99.0
        self.last_dealt_t = -99.0
        self.rear_hits = 0
        self.self_hist = []

    def update(self, obs: dict):
        t = obs['t']
        self.t = t
        c0 = obs['self']
        (mx, my, e0) = c0['pos']
        for e in obs.get('events') or []:
            s0 = e.get('type')
            if s0 == 'damaged':
                j = e.get('amount', 0.0)
                self.dmg_taken_log.append((t, j, e.get('by'), e.get('weapon')))
                self.last_damage_t = t
                if e.get('rear'):
                    self.rear_hits += 1
                l = e.get('bearing')
                if l is not None:
                    self.threats.append((t, l, 1.0 + j / 100.0, e.get('weapon'), None))
                m = e.get('by')
                if m is not None and m in self.mechs:
                    r0 = self.mechs[m]
                    r0.dmg_to_me += j
                    r0.last_hit_me = t
            elif s0 == 'hit':
                q0 = e.get('target')
                j = e.get('amount', 0.0)
                self.dmg_dealt_log.append((t, j, q0, e.get('weapon')))
                self.last_dealt_t = t
                if q0 in self.mechs:
                    self.mechs[q0].dmg_from_me += j
                    self.mechs[q0].last_hit_by_me = t
            elif s0 == 'kill':
                self.my_kills += 1
                self.mechs.pop(e.get('target'), None)
            elif s0 == 'took' or s0 == 'dropped':
                self.pickups.pop(e.get('id'), None)
                if s0 == 'dropped':
                    self.pickups[e['id']] = Item(e['id'], e.get('kind'), [mx, my, e0], None, t)
            elif s0 == 'take_failed':
                if e.get('reason') in ('gone', 'have'):
                    self.pickups.pop(e.get('id'), None)
        h = obs.get('alive', 16)
        if self.prev_alive is not None and h < self.prev_alive:
            q = self.prev_alive - h
            d0 = sum((1 for e in obs.get('events') or [] if e.get('type') == 'kill'))
            h0 = max(0, q - d0)
            if h0:
                self.deaths_not_mine += h0
                self.other_death_ts.extend([t] * h0)
                self.ffa_score += h0 * (1.0 if t < 30 else 0.4 if t < 60 else 0.15)
        self.prev_alive = h
        b0 = set()
        for d in obs.get('mechs') or []:
            g = d['id']
            if d.get('kind') == 'npc':
                r0 = self.npcs.get(g)
                if r0 is None:
                    self.npcs[g] = Track(d, t)
                else:
                    r0.update(d, t)
                continue
            if g == self.me_id:
                continue
            b0.add(g)
            r0 = self.mechs.get(g)
            if r0 is None:
                self.mechs[g] = r0 = Track(d, t)
            else:
                r0.update(d, t)
        for (g, r0) in self.mechs.items():
            if g not in b0:
                r0.listed = False
        for (g, r0) in list(self.npcs.items()):
            if t - r0.last_seen > 30:
                del self.npcs[g]
        for (g, r0) in list(self.mechs.items()):
            if t - r0.last_seen > 25:
                del self.mechs[g]
        for l in obs.get('beams') or []:
            m = l.get('by')
            path = l.get('path') or []
            if m is None or len(path) < 2:
                continue
            (r, v) = (path[-1][0], path[-1][1])
            f0 = any((_seg_dist(mx, my, path[i][0], path[i][1], path[i + 1][0], path[i + 1][1]) <= 12.0 for i in range(len(path) - 1)))
            if not f0:
                r0 = self.mechs.get(m)
                if r0 is not None:
                    r0.fighting_t = t
                for g0 in self.mechs.values():
                    if g0.id != m and d2(g0.pos[0], g0.pos[1], r, v) < 8:
                        g0.fighting_t = t
                        if t < 150 and g0.listed and (g0.hp_drop_t == t) and (t - g0.last_hit_by_me > 0.25):
                            self.ffa_score += 0.25
            else:
                self.threats.append((t, bearing(mx, my, path[0][0], path[0][1]), 2.0, l.get('kind'), None))
        for r0 in self.mechs.values():
            if r0.listed and r0.hp_drop_t == t and (t - r0.last_hit_by_me > 0.25):
                r0.fighting_t = t
        for k0 in obs.get('sounds') or []:
            a0 = k0.get('kind')
            if a0 in ('gun', 'laser', 'laser_charge', 'lightning', 'mortar', 'explosion', 'boost', 'step', 'jet', 'reload'):
                t0 = {'near': 1.5, 'mid': 0.8, 'far': 0.3}.get(k0.get('dist'), 0.3)
                self.threats.append((t - k0.get('age', 0.0), k0.get('bearing', 0.0), t0, a0, k0.get('dist')))
        self.threats = [x for x in self.threats if t - x[0] < 6.0][-40:]
        n0 = set()
        for i0 in obs.get('pickups') or []:
            n0.add(i0['id'])
            z = self.pickups.get(i0['id'])
            if z is None:
                self.pickups[i0['id']] = Item(i0['id'], i0.get('kind'), i0['pos'], None, t)
            else:
                (z.pos, z.last_seen) = (i0['pos'], t)
        m0 = set()
        for n in obs.get('crates') or []:
            m0.add(n['id'])
            z = self.crates.get(n['id'])
            if z is None:
                self.crates[n['id']] = Item(n['id'], 'crate', n['pos'], n.get('hp'), t)
            else:
                (z.pos, z.hp, z.last_seen) = (n['pos'], n.get('hp'), t)
        o0 = set()
        for n in obs.get('cubes') or []:
            o0.add(n['id'])
            z = self.cubes.get(n['id'])
            if z is None:
                self.cubes[n['id']] = Item(n['id'], 'cube', n['pos'], None, t)
            else:
                (z.pos, z.last_seen) = (n['pos'], t)
        for (p0, l0, j0) in ((self.pickups, n0, 40.0), (self.crates, m0, 40.0), (self.cubes, o0, 40.0)):
            for (y, z) in list(p0.items()):
                if y in l0:
                    continue
                if d2(z.pos[0], z.pos[1], mx, my) < j0 and t - z.last_seen > 0.25:
                    del p0[y]
                elif t - z.last_seen > 90:
                    del p0[y]
        self.self_hist.append((t, mx, my, e0))
        if len(self.self_hist) > 40:
            self.self_hist = self.self_hist[-40:]

    def visible_enemies(self):
        return [tr for tr in self.mechs.values() if tr.listed]

    def recent_damage(self, window: float) -> float:
        t = self.t
        return sum((a for (tt, a, by, w) in self.dmg_taken_log[-60:] if t - tt <= window and w != 'zone'))

    def threat_bearing(self, window: float=4.0):
        l = m = 0.0
        n = self.t
        for (q, d, r, j, g) in self.threats:
            if n - q <= window:
                h = r * (1.0 - (n - q) / (window + 1e-06))
                l += math.cos(d) * h
                m += math.sin(d) * h
        if l * l + m * m < 0.04:
            return None
        return math.atan2(m, l)

    def is_ffa(self) -> bool:
        return self.ffa_score >= 1.0

    def ffa_sure(self) -> bool:
        d = self.other_death_ts
        if len(d) < 2:
            return False
        return d[1] < 40.0 or (len(d) >= 4 and d[3] < 75.0)
