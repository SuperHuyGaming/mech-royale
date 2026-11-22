from __future__ import annotations
import gc
import math
import os
import random
import time
import zlib
from . import gunnery as gn
from .arena import Arena
from .geometry import adiff, bearing, clamp, d2, unit, wrap
from .nav import NavGrid
from .params import load as load_params
from .world import World
try:
    import mechfight as _mf
except Exception:
    _mf = None
_OPEN_DIRS = tuple(((math.cos(k * math.pi / 8.0), math.sin(k * math.pi / 8.0)) for k in range(16)))
HOLD_RANGES = (22.0, 35.0, 50.0, 70.0, 95.0, 125.0, 160.0, 200.0)
HOUSE_GUN_HIT = ((12.5, 0.81), (37.5, 0.7), (62.5, 0.5), (87.5, 0.43), (112.5, 0.32), (137.5, 0.21), (180.0, 0.14), (230.0, 0.09))

def house_gun_hit(d):
    a0 = HOUSE_GUN_HIT
    if d <= a0[0][0]:
        return a0[0][1]
    for ((f, l), (j, q)) in zip(a0, a0[1:]):
        if d <= j:
            return l + (q - l) * (d - f) / (j - f)
    return a0[-1][1]

class Intent:
    __slots__ = ('mode', 'goal', 'target', 'shoot', 'dist_pref', 'strafe', 'boost', 'face', 'pickup', 'note', 'evasive')

    def __init__(self, mode, goal=None, target=None, shoot=None, dist_pref=None, strafe=False, boost=False, face=None, pickup=None, note=''):
        (self.mode, self.goal, self.target, self.shoot) = (mode, goal, target, shoot)
        (self.dist_pref, self.strafe, self.boost, self.face) = (dist_pref, strafe, boost, face)
        (self.pickup, self.note) = (pickup, note)
        self.evasive = False

class Brain:
    name = 'OSU Ironclad'

    def __init__(self, hello: dict):
        self.cfg = f = hello['config']
        self.P = load_params()
        self.slot = int(hello.get('slot', 0))
        self.rng = random.Random((hello.get('seed', 0) or 0) + 1000003 * int(self.P.get('rng_salt', 0) or 0))
        self.seed0 = hello.get('seed', 0) or 0
        self.seed_bucket = None
        self.sit_out = False
        j = (hello.get('map') or {}).get('dir', '/arena')
        if not os.path.exists(j) and os.path.exists('/arena'):
            j = '/arena'
        self.A = Arena(j, f.get('mech', {}))
        self.N = NavGrid(self.A)
        self.W = World(f, self.slot)
        self.mc = f.get('mech', {})
        self.wc = f.get('weapons', {})
        self.zc = f.get('zone', {})
        self.loot_reach = float(f.get('loot', {}).get('pickup_radius', 4.0))
        self.cube_reach = float(f.get('cubes', {}).get('pickup_radius', 3.5))
        self.size_per_cube = float(f.get('cubes', {}).get('size_per_cube', 0.05))
        self.dmg_per_cube = float(f.get('cubes', {}).get('damage_per_cube', 0.04))
        self.cube_cap = float(f.get('cubes', {}).get('stat_cap', 10))
        self.rear_mult = float(self.mc.get('rear_mult', 1.5))
        self.path = []
        self.path_goal = None
        self.path_t = -99.0
        self.search = None
        self.search_goal = None
        self.strafe_dir = 1 if self.rng.random() < 0.5 else -1
        self.strafe_until = 0.0
        self.fb_on = False
        self.ring = None
        self.ring_go = None
        self.ring_wait_until = -1.0
        self.spot_cache = None
        self.stuck_since = None
        self.unstick_until = -1.0
        self.unstick_dir = (0.0, 0.0)
        self.last_move = (0.0, 0.0)
        self.wd_key = None
        self.wd_best = 1000000000.0
        self.wd_t = 0.0
        self.wd_escapes = 0
        self.bad_goals = []
        self.bad_count = {}
        self.escape_until = -1.0
        self.escape_dir = (0.0, 0.0)
        self.target_id = None
        self.los_seen = {}
        self.evade = False
        self.retreating = False
        self.squad = False
        self.retreat_spot = None
        self.retreat_spot_t = -99.0
        self.gate = False
        self.gate_from = 0.0
        self.ghost_t = -99.0
        self.ghost_spot = (-99.0, None)
        self.snug = None
        self.snug_t = -99.0
        self.snug_done = False
        self.snug_since = (None, 0.0)
        self.open_cache = {}
        self.open_budget = 0
        self.laser_hold_start = None
        self.last_fire_t = -99.0
        self.phase_from = None
        self.hold = None
        self.hold_t = -99.0
        self.hold_key = None
        self.loot_goal = None
        self.blacklist = {}
        self.visited = set()
        self.explore_target = None
        self.explore_t = -99.0
        self.ticks = 0
        self.act_ms = 0.0
        gc.collect()
        try:
            gc.freeze()
        except AttributeError:
            pass
        gc.disable()

    def act(self, obs: dict) -> dict:
        if self.seed_bucket is None and self.P.get('seed_k'):
            self.seed_setup(obs)
        if self.sit_out:
            return {'move': [0.0, 0.0], 'boost': False, 'jump': False, 'aim': [obs['self']['aim'][0], 0.0], 'fire': [False, False]}
        try:
            return self._act(obs)
        except Exception as f:
            self.errors = getattr(self, 'errors', 0) + 1
            if self.errors <= 5 or self.errors % 200 == 0:
                import sys
                import traceback
                print(f"act error #{self.errors} at t={obs.get('t')}: {f!r}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            self.path = []
            self.search = None
            return self.safe_act(obs)

    def seed_setup(self, obs: dict):
        f = self.P
        (e0, g0) = (obs['self']['pos'][0], obs['self']['pos'][1])
        l = '%d:%d:%d' % (self.slot, int(round(e0 / 4.0)), int(round(g0 / 4.0)))
        self.seed_bucket = j = zlib.crc32(l.encode()) % max(1, int(f.get('seed_k', 0)))
        a0 = f.get('seed_play', -1)
        if isinstance(a0, (list, tuple)):
            self.sit_out = j not in a0
        else:
            self.sit_out = a0 is not None and int(a0) >= 0 and (j != int(a0))
        q = (f.get('seed_over') or {}).get(str(j))
        if q:
            f.update(q)
        b0 = (f.get('seed_salts') or {}).get(str(j))
        if b0 is not None:
            self.rng = random.Random(self.seed0 + 1000003 * int(b0))
            self.strafe_dir = 1 if self.rng.random() < 0.5 else -1

    def safe_act(self, obs: dict) -> dict:
        a0 = obs['self']
        (k0, l0, m0) = a0['pos']
        n0 = obs['zone']['next_center']
        (b0, e0) = unit(n0[0] - k0, n0[1] - l0)
        if d2(k0, l0, n0[0], n0[1]) < 0.4 * obs['zone']['next_radius']:
            b0 = e0 = 0.0
        try:
            self.pos = a0['pos']
            if b0 or e0:
                (b0, e0) = self.steer(b0, e0)
        except Exception:
            pass
        f = {'move': [b0, e0], 'boost': not a0.get('in_zone', True), 'aim': [math.atan2(e0, b0) if b0 or e0 else a0['aim'][0], 0.0]}
        j = None
        for q in obs.get('mechs') or []:
            if q.get('kind') != 'mech':
                continue
            l = math.dist(q['pos'][:2], (k0, l0))
            if j is None or l < j[0]:
                j = (l, q)
        if j is not None and j[0] < 200:
            (g0, i0, j0) = j[1]['pos']
            f['aim'] = [math.atan2(i0 - l0, g0 - k0), math.atan2(j0 + 4.5 - m0 - 7.0, max(1.0, j[0]))]
            f['fire'] = [True, True]
        if obs.get('offer'):
            f['pick'] = 0
        return f

    def _act(self, obs: dict) -> dict:
        p0 = time.perf_counter()
        self.tick_t0 = p0
        self.rays0 = self.A.rays
        self.ticks += 1
        f = self.W
        f.update(obs)
        self.obs = obs
        i0 = obs['self']
        self.me = i0
        self.t = o0 = obs['t']
        self.pos = i0['pos']
        self.hpf = i0['hp'] / max(1.0, i0['max_hp'])
        self.scale = gn.body_scale(i0.get('cubes', 0), self.size_per_cube)
        n0 = i0.get('stats') or {}
        self.walk = float(n0.get('walk_speed', self.mc.get('walk_speed', 11.7)))
        self.muzzle_h = float(n0.get('muzzle_height', self.mc.get('muzzle_height', 7.0) * self.scale))
        self.eye_h = float(n0.get('eye_height', self.mc.get('eye_height', 8.0) * self.scale))
        self.weap = i0.get('weapons') or [None, None]
        self.kinds = [w['kind'] if w else None for w in self.weap]
        self.squad = bool(self.P.get('squad_mode')) and (not f.is_ffa()) and (o0 >= self.P.get('squad_after_s', 30.0))
        j: dict = {}
        if obs.get('offer'):
            l0 = self.pick_upgrade(obs['offer'])
            if l0 is not None:
                j['pick'] = l0
        self.zone_update(obs['zone'])
        self.los_cache = {}
        e0 = self.decide()
        self.ring = None
        if self.squad and self.P.get('squad_ring'):
            e0 = self.ring_override(e0)
        if self.P.get('wd_on', True):
            self.watchdog(e0)
        self.return_fire(e0)
        (k0, q, g0) = self.pilot(e0)
        (l, b0, m0) = self.gunnery(e0)
        j0 = math.hypot(k0[0], k0[1])
        if j0 > 0.9999:
            k0 = (k0[0] * 0.9999 / j0, k0[1] * 0.9999 / j0)
        j['move'] = [round(k0[0], 4), round(k0[1], 4)]
        j['boost'] = bool(q)
        j['jump'] = bool(g0)
        j['aim'] = [round(l[0], 5), round(l[1], 5)]
        j['fire'] = b0
        if m0:
            j['reload'] = True
        self.loot_actions(e0, j)
        self.intent = e0
        a0 = (time.perf_counter() - p0) * 1000.0
        self.act_ms = a0
        if self.P.get('debug_draw') and _mf is not None:
            try:
                q0 = e0.target.id if e0.target is not None else '-'
                _mf.draw.label(f'{e0.mode} {q0} hp{self.hpf:.2f} {e0.note}'[:78])
            except Exception:
                pass
        if self.ticks % 150 == 0 and a0 < 8.0:
            gc.collect(0)
        return j

    def circle_at(self, z, t):
        (e0, b0) = (z['shrink_start'], z['shrink_end'])
        (f, l) = (z['center'], z['radius'])
        (j, q) = (z['next_center'], z['next_radius'])
        if self.phase_from is not None and self.phase_from[0] == e0:
            (f, l) = (self.phase_from[1], self.phase_from[2])
        if t <= e0:
            return (f, l)
        if t >= b0:
            return (j, q)
        a0 = (t - e0) / max(1e-06, b0 - e0)
        return ((f[0] + (j[0] - f[0]) * a0, f[1] + (j[1] - f[1]) * a0), l + (q - l) * a0)

    def zone_update(self, z):
        s0 = self.t
        if s0 < z['shrink_start']:
            self.phase_from = (z['shrink_start'], tuple(z['center']), z['radius'])
        (x0, y0) = (self.pos[0], self.pos[1])
        (q, o0) = (z['next_center'], z['next_radius'])
        self.zn = (q, o0)
        self.zcur = self.circle_at(z, s0)
        j0 = min(self.P.zone_margin, 0.3 * o0)
        a0 = d2(x0, y0, q[0], q[1])
        self.inside_next = a0 <= o0 - j0
        if a0 <= o0:
            u0 = 1000000000.0
        else:
            (q0, p0) = (z['shrink_start'], z['shrink_end'])
            (i0, e0) = (0.0, 1.0)
            (l, n0) = self.circle_at(z, q0)
            b0 = n0 - d2(x0, y0, l[0], l[1])
            if b0 <= 0:
                u0 = s0
            else:
                for f in range(18):
                    k0 = 0.5 * (i0 + e0)
                    v0 = q0 + (p0 - q0) * k0
                    (j, m0) = self.circle_at(z, v0)
                    if m0 - d2(x0, y0, j[0], j[1]) > 0:
                        i0 = k0
                    else:
                        e0 = k0
                u0 = q0 + (p0 - q0) * i0
        l0 = max(0.0, a0 - (o0 - j0))
        w0 = l0 / max(1.0, self.walk) * 1.3
        self.zone_slack = u0 - s0 - w0
        self.outside = not self.me.get('in_zone', True)
        self.zone_dps = z.get('dps', 0.0)
        g0 = (round(q[0], 1), round(q[1], 1), o0)
        if g0 != self.hold_key or s0 - self.hold_t > 4.0 or self.hold is None:
            self.hold_key = g0
            self.hold_t = s0
            self.hold = self.pick_hold(q, o0)

    def pick_hold(self, cn, rn):
        (m0, n0) = (self.pos[0], self.pos[1])
        if rn <= 1.0:
            b0 = self.N.nearest_free(cn[0], cn[1], 20)
            return self.N.pos(b0) if b0 is not None else (cn[0], cn[1])
        l = self.P.zone_hold_frac
        if rn <= 75:
            l = 0.3
        if rn <= 30:
            l = 0.15
        j = d2(m0, n0, cn[0], cn[1])
        (k0, l0) = unit(m0 - cn[0], n0 - cn[1])
        if k0 == 0 and l0 == 0:
            (k0, l0) = (1.0, 0.0)
        i0 = min(j, l * rn)
        f = self.N
        if j <= l * rn + 8 and f.is_free(m0, n0) and f.cover[f.node(m0, n0)]:
            return (m0, n0)
        (q, a0) = (cn[0] + k0 * i0, cn[1] + l0 * i0)
        j0 = max(3, min(12, int(0.12 * rn / f.k)))
        b0 = f.nearest_cover(q, a0, j0)
        if b0 is not None:
            (e0, g0) = f.pos(b0)
            if d2(e0, g0, cn[0], cn[1]) > rn - min(self.P.zone_margin, 0.3 * rn):
                b0 = None
        if b0 is None:
            b0 = f.nearest_free(q, a0, 15)
        if b0 is None:
            b0 = self.N.nearest_free(cn[0], cn[1], 25)
        return self.N.pos(b0) if b0 is not None else (cn[0], cn[1])

    def in_safe(self, px, py, slack_m=0.0):
        (j, a0) = self.zn
        (f, q) = self.zcur
        l = d2(px, py, f[0], f[1]) <= q - 2
        return l and (d2(px, py, j[0], j[1]) <= a0 - slack_m or self.zone_slack > 15)

    def los_to(self, tr, eye=False):
        j = (tr.id, eye)
        j0 = self.los_cache.get(j)
        if j0 is not None:
            return j0
        (k0, l0, m0) = self.pos
        i0 = m0 + (self.eye_h if eye else self.muzzle_h)
        l = getattr(tr, 'kind', 'mech') == 'mech'
        g0 = gn.body_scale(getattr(tr, 'cubes', 0), self.size_per_cube) if l else 1.0
        f = 9.0 * g0 if l else 3.5
        e0 = 2.5 * g0 if l else 1.8
        (q, a0, b0) = tr.pos
        j0 = self.exposed_point(k0, l0, i0, q, a0, b0, f, e0) is not None
        self.los_cache[j] = j0
        return j0

    def exposed_point(self, sx, sy, sz, px, py, pz, h, r):
        f = self.A
        (e0, g0) = unit(px - sx, py - sy)
        (j, l) = (-g0 * r * 0.75, e0 * r * 0.75)
        for (a0, b0, q) in ((0.0, 0.0, 0.5), (0.0, 0.0, 0.82), (j, l, 0.5), (-j, -l, 0.5), (j, l, 0.82), (-j, -l, 0.82)):
            if f.los(sx, sy, sz, px + a0, py + b0, pz + q * h):
                return (a0, b0, q * h)
        return None

    def their_los_to_point(self, tr, px, py, pz):
        (f, j, l) = tr.pos
        q = l + 8.0 * gn.body_scale(tr.cubes, self.size_per_cube)
        return self.exposed_point(f, j, q, px, py, pz, 9.0 * self.scale, 2.5 * self.scale) is not None

    def sq(self, key, default):
        if getattr(self, 'squad', False):
            f = self.P.get('squad_' + key)
            if f is not None:
                return f
        return self.P.get(key, default)

    def sqk(self, key, default):
        if getattr(self, 'squad', False) and self.t >= self.P.get('sqk_after_s', 30.0):
            f = self.P.get('squad_' + key)
            if f is not None:
                return f
        return default

    def ffa_on(self, key):
        return bool(self.P.get(key)) and self.W.is_ffa() and (self.W.ffa_sure() or not self.P.get('ffa_need_sure', True))

    def hit_by(self, tr, window):
        l = self.t
        if l - tr.last_hit_me < window:
            return True
        for (q, f, j, a0) in reversed(self.W.dmg_taken_log[-40:]):
            if l - q >= window:
                break
            if j == tr.id:
                return True
        return False

    def ffa_provoked(self, tr, d):
        if self.hit_by(tr, self.P.get('ffa_hit_s', 4.0)):
            return True
        if tr.charging and d < 130.0:
            f = bearing(tr.pos[0], tr.pos[1], self.pos[0], self.pos[1])
            return abs(adiff(tr.aim[0], f)) < 0.22
        return False

    def closing_on_us(self, tr, d):
        if d >= 180.0 or d < 1.0:
            return d < 1.0
        f = tr.vel or (0.0, 0.0, 0.0)
        return -((tr.pos[0] - self.pos[0]) * f[0] + (tr.pos[1] - self.pos[1]) * f[1]) / d > 3.0

    def ffa_open_ok(self, tr, d, adv, busy):
        f = self.P
        q = tr.hp / max(1.0, tr.max_hp)
        if d < f.get('ffa_gate_close_m', 60.0) and self.hpf > (0.3 if d < 45.0 else 0.45) and self.los_to(tr):
            l = bearing(tr.pos[0], tr.pos[1], self.pos[0], self.pos[1])
            if d < f.get('ffa_gate_bump_m', 30.0) or abs(adiff(tr.aim[0], l)) < 0.6:
                return 'close'
        if q < f.get('ffa_gate_weak', 0.5) and self.hpf >= f.get('ffa_gate_weak_hp', 0.6):
            return 'weak'
        if busy and q < 0.9 and (self.hpf >= f.get('ffa_gate_busy_hp', 0.75)) and (d < f.get('ffa_gate_busy_m', 170.0)) and (adv > 0.6):
            return 'busy'
        j = self.obs.get('alive', 16)
        if self.hpf >= 0.8 and adv > f.engage_ratio:
            if q < 0.8 and j <= f.get('ffa_gate_hurt_alive', 10):
                return 'hurt'
            if j <= f.get('ffa_gate_edge_alive', 6) and 'laser' in self.kinds and ('laser' not in (tr.weapons or [])):
                return 'edge'
        return None

    def ffa_loot_risky(self, px, py):
        f = self.P
        if d2(self.pos[0], self.pos[1], px, py) <= f.get('ffa_loot_near_m', 25.0):
            return False
        (j, l) = (f.get('ffa_loot_mech_m', 110.0), f.get('ffa_loot_mech_s', 20.0))
        for q in self.W.mechs.values():
            if self.t - q.last_seen < l and d2(q.pos[0], q.pos[1], px, py) < j:
                return True
        return False

    def openness(self, px, py):
        a0 = self.N
        n0 = a0.node(px, py)
        o0 = self.open_cache.get(n0)
        if o0 is not None:
            return o0
        if self.open_budget <= 0:
            return None
        self.open_budget -= 1
        (l0, m0, k0) = (a0.gx, a0.gy, a0.free)
        (q, j) = divmod(n0, l0)
        o0 = 0
        for (e0, g0) in _OPEN_DIRS:
            (i0, j0) = (float(j), float(q))
            for b0 in range(60):
                i0 += e0
                j0 += g0
                (f, l) = (int(i0 + 0.5), int(j0 + 0.5))
                if f < 0 or l < 0 or f >= l0 or (l >= m0) or (not k0[l * l0 + f]):
                    break
                o0 += 1
        self.open_cache[n0] = o0
        return o0

    def snug_spot(self):
        s0 = self.t
        (u0, v0) = (self.pos[0], self.pos[1])
        l0 = min(self.P.zone_margin, 0.3 * self.zn[1])
        b0 = self.snug
        if b0 is not None:
            if self.snug_since[0] != b0:
                self.snug_since = (b0, s0)
            g0 = d2(u0, v0, b0[0], b0[1]) > 6.0
            if g0 and s0 - self.snug_since[1] > 12.0:
                self.bad_goals.append((b0[0], b0[1], s0 + 60.0))
            if not g0:
                self.snug_since = (b0, s0)
            if not self.in_safe(b0[0], b0[1], l0) or self.goal_bad(b0[0], b0[1]):
                b0 = self.snug = None
        if b0 is not None and self.snug_done and (s0 - self.snug_t < 6.0):
            return b0
        (i0, j0) = self.hold
        f = self.N
        self.open_budget = 8
        a0 = [(i0, j0)]
        if b0 is not None:
            a0.append(b0)
        for p0 in (14.0, 28.0, 42.0):
            for k0 in range(8):
                j = k0 * (math.pi / 4.0) + p0 * 0.03
                a0.append((i0 + p0 * math.cos(j), j0 + p0 * math.sin(j)))
        (l, q, e0) = (None, 1e+18, True)
        for (n0, o0) in a0:
            if not f.is_free(n0, o0) or not f.cspace_free(n0, o0) or (not self.in_safe(n0, o0, l0)) or self.goal_bad(n0, o0):
                continue
            m0 = self.openness(n0, o0)
            if m0 is None:
                e0 = False
                continue
            q0 = m0 + 0.25 * d2(u0, v0, n0, o0)
            if b0 is not None and n0 == b0[0] and (o0 == b0[1]):
                q0 -= 25.0
            if q0 < q:
                (q, l) = (q0, (n0, o0))
        if l is not None:
            self.snug = l
        (self.snug_t, self.snug_done) = (s0, e0)
        return self.snug

    def cube_mult(self, cubes):
        return 1.0 + self.dmg_per_cube * min(cubes or 0, self.cube_cap)

    def weapon_dps(self, kind, d, stats=None, cubes=0, vs_mortar=False, energy=100.0, house=False):
        j0 = stats or self.wc.get(kind, {})
        a0 = self.cube_mult(cubes)
        if kind == 'gun':
            g0 = j0.get('range', 230)
            if d > g0:
                return 0.0
            f = max(0.05, j0.get('cooldown', 0.24))
            b0 = j0.get('mag', 12)
            i0 = b0 * f / (b0 * f + j0.get('reload', 2.4))
            q = clamp(1.05 - d / 240.0, 0.12, 0.95)
            if house:
                q = house_gun_hit(d)
            return j0.get('damage', 45) / f * i0 * q * a0
        if kind == 'laser':
            if d > j0.get('range', 280):
                return 0.0
            j = j0.get('charge', 0.75) + j0.get('cooldown', 1.65)
            l = j0.get('damage', 315) * (1.0 + (j0.get('cookoff', 0.6) if vs_mortar else 0.0))
            return l / j * (0.3 if house else 0.9) * a0
        if kind == 'lightning':
            if d > j0.get('range', 45) * 0.95:
                return 0.0
            e0 = 1.0 if energy > 20 else 0.4
            return j0.get('damage', 60) / max(0.05, j0.get('cooldown', 0.25)) * 0.95 * e0 * a0
        if kind == 'mortar':
            if d < 18 or d > 225:
                return 0.0
            return (j0.get('damage', 200) * 0.5 + j0.get('splash', 160) * 0.5) / j0.get('cooldown', 2.6) * 0.3 * a0
        return 0.0

    def behind(self, tr):
        f = bearing(tr.pos[0], tr.pos[1], self.pos[0], self.pos[1])
        return abs(adiff(tr.aim[0], f)) > 0.5 * math.radians(self.mc.get('front_arc_deg', 180.0))

    def exposed_to(self, tr):
        f = bearing(self.pos[0], self.pos[1], tr.pos[0], tr.pos[1])
        return abs(adiff(self.me['aim'][0], f)) > 0.5 * math.radians(self.mc.get('front_arc_deg', 180.0))

    def my_dps(self, d, tr=None):
        f = 0.0
        j = tr is not None and 'mortar' in (tr.weapons or [])
        for l in self.weap:
            if l:
                f += self.weapon_dps(l['kind'], d, l.get('stats'), self.me.get('cubes', 0), j, self.me.get('energy', 100))
        if tr is not None and getattr(tr, 'kind', 'mech') == 'mech' and self.behind(tr):
            f *= self.rear_mult
        return f

    def their_dps(self, tr, d, facing_them=True, ranging=False):
        q = 0.0
        l = 'mortar' in self.kinds
        f = self.squad and bool(self.P.get('squad_threat_cal')) and ('laser' in self.kinds if ranging else self.t >= self.P.get('sqk_after_s', 30.0))
        for j in tr.weapons or ['gun']:
            q += self.weapon_dps(j, d, None, tr.cubes, l, house=f)
        if not facing_them:
            q *= self.rear_mult
        return q

    def decide(self) -> Intent:
        W = self.W
        f = self.P
        t = self.t
        (x, y, z) = self.pos
        s1 = self.me
        enemies = []
        for tr in W.mechs.values():
            if not tr.listed:
                continue
            d = d2(x, y, tr.pos[0], tr.pos[1])
            if d > 260:
                continue
            enemies.append((d, tr))
        enemies.sort(key=lambda e: e[0])
        self.enemies = enemies
        a1 = self.ffa_on('ffa_gate') and self.obs.get('alive', 16) > f.get('ffa_gate_off_alive', 2)
        self.gate_from = 0.0
        if not a1 and f.get('ffa_gate') and (t < f.get('ffa_unknown_s', 0.0)) and (not W.is_ffa()):
            a1 = True
            self.gate_from = f.get('ffa_unknown_m', 0.0)
        self.gate = a1
        hostile = []
        for (d, tr) in enemies:
            o1 = self.los_to(tr, eye=True)
            i0 = bearing(tr.pos[0], tr.pos[1], x, y)
            q = abs(adiff(tr.aim[0], i0)) < 0.35
            b2 = t - tr.last_hit_me < 3.0
            if o1 and (q or b2 or d < 60):
                hostile.append((d, tr))
        self.hostile = hostile
        k1 = W.recent_damage(2.5)
        u1 = sum((1 for (d, tr) in hostile if d < 130))
        o2 = False
        if self.hpf < f.retreat_hp_frac + 0.1 and (hostile or k1 > 0):
            o2 = True
        if u1 >= 2 and self.hpf < 0.7:
            o2 = True
        if u1 >= 3 and self.hpf < 0.85:
            o2 = True
        if o2 and len(hostile) == 1:
            (d, tr) = hostile[0]
            w1 = self.my_dps(d, tr)
            k2 = self.their_dps(tr, d)
            if w1 > 1 and tr.hp / w1 < 0.55 * s1['hp'] / max(1.0, k2):
                o2 = False
        if self.squad and f.get('squad_no_retreat'):
            if hostile or t - W.last_damage_t < 2.0 or (f.get('squad_hyper') and enemies):
                o2 = False
                self.retreating = False
            elif self.hpf < f.get('squad_heal_below', 0.8):
                o2 = True
        if o2:
            self.retreating = True
        if self.retreating:
            a2 = t - W.last_damage_t > 6.0 and (not hostile)
            if self.hpf >= 0.85 or (a2 and self.hpf > 0.6 and (t - W.last_damage_t > 12)):
                self.retreating = False
        if f.get('squad_lose_rule') and self.squad and (t >= f.get('sqk_after_s', 30.0)):
            near = sum((1 for (d, tr) in enemies if d < 160.0))
            n1 = f.get('squad_lose_k', 1.0)
            p1 = near >= 2 and self.hpf < 0.55 * n1 or (near >= 3 and self.hpf < 0.8 * n1)
            u0 = W.mechs.get(self.target_id) if self.target_id is not None else None
            if u0 is None or not u0.listed:
                q2 = [tr for (d, tr) in enemies if d < 160.0 and tr.hp is not None]
                u0 = min(q2, key=lambda tr: tr.hp / max(1.0, tr.max_hp)) if q2 else None
            if u0 is not None and u0.listed and (u0.hp is not None):
                i2 = u0.hp / max(1.0, u0.max_hp)
                if self.hpf < 0.35 * n1 and i2 > self.hpf + 0.1:
                    p1 = True
                if i2 < 0.25 and self.hpf > 0.2 and (near <= 2):
                    p1 = False
            if p1:
                self.lose_t = t
            self.retreating = t - getattr(self, 'lose_t', -99.0) < f.get('squad_lose_pulse_s', 1.5)
        (target, l2) = (None, -1000000000.0)
        for (d, tr) in enemies:
            w1 = self.my_dps(d, tr)
            if w1 <= 0 and d > 60:
                continue
            o1 = self.los_to(tr)
            if o1:
                self.los_seen[tr.id] = t
            k2 = self.their_dps(tr, d)
            l1 = tr.hp / max(1.0, w1)
            c2 = -l1
            c2 += k2 / 100.0
            if t - tr.last_hit_me < 4:
                c2 += 3.0
            if a1 and d >= self.gate_from and self.hit_by(tr, 4.0):
                c2 += 6.0
            if tr.hp < 350:
                c2 += 3.0
            if self.squad and f.get('squad_finish') and (t >= f.get('sqk_after_s', 30.0)) and (tr.hp < f.get('squad_finish_hp', 0.42) * tr.max_hp) and (t - self.los_seen.get(tr.id, -99.0) < 3.0):
                c2 += f.get('squad_finish_bonus', 6.0)
            if not o1:
                c2 -= 6.0
            if tr.id == self.target_id:
                c2 += 1.5
            if c2 > l2:
                (target, l2) = (tr, c2)
        if self.squad and f.get('squad_smith_target') and (len(hostile) >= 2):
            (j0, n0, v0) = (None, -1.0, -1.0)
            for (d, tr) in hostile:
                if not self.los_to(tr):
                    continue
                w1 = self.my_dps(d, tr)
                if w1 <= 0:
                    continue
                n2 = self.their_dps(tr, d, not self.exposed_to(tr)) / max(0.5, tr.hp / w1)
                if tr.id == self.target_id:
                    v0 = n2
                if n2 > n0:
                    (j0, n0) = (tr, n2)
            if j0 is not None and (target is None or j0 is target or n0 > 1.25 * v0):
                target = j0
        self.target_id = target.id if target is not None else None
        if self.retreating and (hostile or t - W.last_damage_t < 5.0):
            f2 = self.cover_spot()
            p0 = None
            for (d, tr) in enemies:
                if d < 45 and self.los_to(tr):
                    p0 = tr
                    break
            e2 = p0 if p0 is not None else target if target is not None and self.los_to(target) else None
            y1 = True
            if a1 and target is not None and (p0 is None) and (d2(x, y, target.pos[0], target.pos[1]) >= self.gate_from):
                y1 = self.ffa_provoked(target, d2(x, y, target.pos[0], target.pos[1])) or self.hit_by(target, 8.0)
                if not y1:
                    e2 = None
            q0 = f.get('squad_cover_m', 0.0)
            if q0 and f2 is not None and self.squad and (t >= f.get('sqk_after_s', 30.0)) and (d2(x, y, f2[0], f2[1]) > q0):
                f2 = None
            e1 = f2
            v1 = 'cover'
            if f.get('squad_last_stand') and self.squad and (t >= f.get('sqk_after_s', 30.0)) and hostile:
                j1 = sum((self.their_dps(o, dd, not self.exposed_to(o)) for (dd, o) in hostile))
                m2 = s1['hp'] / max(1.0, j1)
                (j0, k0) = (None, 1000000000.0)
                for (dd, o) in hostile:
                    if o.hp is None or not self.los_to(o):
                        continue
                    w1 = self.my_dps(dd, o)
                    if w1 > 0 and o.hp / w1 < k0:
                        (j0, k0) = (o, o.hp / w1)
                if j0 is not None:
                    g2 = 1000000000.0 if f2 is None else d2(x, y, f2[0], f2[1]) / max(1.0, self.move_speed(True))
                    if k0 < f.get('squad_last_stand_k', 0.8) * m2 or g2 > f.get('squad_last_stand_reach', 0.7) * m2:
                        return Intent('fight', target=j0, shoot=j0, dist_pref=self.pref_range(j0), strafe=True, boost=False, note='laststand')
            if e1 is None and target is not None and y1 and self.los_to(target):
                return Intent('fight', target=target, shoot=target, dist_pref=self.pref_range(target), strafe=True, boost=False, note='cornered')
            if e1 is None:
                h2 = None
                if target is not None:
                    h2 = bearing(x, y, target.pos[0], target.pos[1])
                else:
                    h2 = W.threat_bearing(5.0)
                if h2 is not None:
                    (e0, g0) = (x - 30 * math.cos(h2), y - 30 * math.sin(h2))
                    (g1, i1) = self.hold
                    if not self.in_safe(e0, g0, -10):
                        (e0, g0) = ((e0 + g1) * 0.5, (g0 + i1) * 0.5)
                    e1 = (e0, g0)
                else:
                    e1 = self.hold
                v1 = 'run'
            return Intent('retreat', goal=e1, target=target, shoot=e2, strafe=False, boost=True, note=v1)
        if self.retreating:
            z0 = None
            if target is not None and target.hp < 260 and self.los_to(target):
                dd = d2(x, y, target.pos[0], target.pos[1])
                if target.hp / max(1.0, self.my_dps(dd, target)) < 0.5 * s1['hp'] / max(1.0, self.their_dps(target, dd)):
                    z0 = target
            if z0 is None:
                if self.outside or self.zone_slack < f.zone_lead_s:
                    return Intent('zone', goal=self.hold, target=target, boost=True, note='heal-zone')
                f2 = self.cover_spot()
                return Intent('heal', goal=f2 if f2 is not None else self.hold, target=target, note='heal')
        for (d, tr) in hostile:
            if not tr.charging or d > 285:
                continue
            if abs(adiff(tr.aim[0], bearing(tr.pos[0], tr.pos[1], x, y))) > max(0.05, math.atan2(4.0, max(d, 1.0))):
                continue
            if 'lightning' in self.kinds and d < 40:
                break
            f2 = self.cover_spot(only=[tr])
            if f2 is not None and d2(x, y, f2[0], f2[1]) <= 12.0:
                return Intent('dodge', goal=f2, target=tr, shoot=tr if self.los_to(tr) else None, boost=True, note='laser')
            break
        if target is None and t - W.last_damage_t < 2.0:
            m1 = [e for e in W.dmg_taken_log[-6:] if t - e[0] < 2.0 and e[3] not in ('zone', None)]
            if m1:
                h2 = W.threat_bearing(2.0)
                if h2 is not None:
                    z1 = (x + 120.0 * math.cos(h2), y + 120.0 * math.sin(h2))
                    f2 = None if self.outside or self.zone_slack < f.zone_lead_s else self.cover_spot(pseudo=z1)
                    return Intent('alert', goal=f2 if f2 is not None else self.hold, face=h2, boost=f2 is not None, note='unseen')
        y0 = None
        z0 = False
        if target is not None:
            d = d2(x, y, target.pos[0], target.pos[1])
            w1 = self.my_dps(d, target)
            k2 = self.their_dps(target, d)
            t1 = target.hp / max(1.0, w1)
            j2 = s1['hp'] / max(1.0, k2 + sum((self.their_dps(o, dd, not self.exposed_to(o)) for (dd, o) in hostile if o is not target)) * 0.7)
            a0 = t - target.last_hit_me < 4.0 or any((o is target for (dd, o) in hostile))
            r2 = None
            b1 = a1 and d >= self.gate_from
            if b1:
                a0 = self.ffa_provoked(target, d)
                r2 = 'prov' if a0 else None
            j = j2 / max(0.1, t1)
            q2 = target.hp < 0.45 * target.max_hp
            m0 = t - target.fighting_t < 3.0
            x1 = not W.is_ffa()
            l = f.aggression * ((f.get('squad_aggr', 1.25) if t >= f.get('sqk_after_s', 30.0) else 1.25) if x1 else 1.0)
            f1 = self.close_weapon(25.0)
            self.evade = False
            l0 = t - self.los_seen.get(target.id, -99.0) > 2.0 and t - target.last_hit_me > 3.0
            z0 = self.squad and bool(f.get('squad_finish')) and (t >= f.get('sqk_after_s', 30.0)) and (target.hp < f.get('squad_finish_hp', 0.42) * target.max_hp) and (self.hpf > f.get('squad_finish_my_hp', 0.45)) and (sum((1 for (dd, o) in hostile if o is not target)) <= f.get('squad_finish_others', 1)) and (t - self.los_seen.get(target.id, -99.0) < f.get('squad_finish_s', 6.0))
            if f1 is not None and (not (a0 and d < 60)):
                y0 = None
            elif self.squad and f.get('squad_hyper'):
                y0 = target
            elif z0:
                y0 = target
            elif l0 and d > 30:
                y0 = None
            elif a0 and (j >= 0.75 or d < 40 or (self.squad and f.get('squad_fight_back') and (t >= f.get('sqk_after_s', 30.0)))):
                y0 = target
            elif a0:
                self.evade = True
            elif b1:
                r2 = self.ffa_open_ok(target, d, j, m0)
                if r2 is not None:
                    y0 = target
            elif j * l > f.engage_ratio:
                if not (self.squad and f.get('squad_reactive')) or self.closing_on_us(target, d):
                    y0 = target
            elif q2 and self.hpf > 0.4 or (m0 and self.hpf > 0.5 and (j > 0.6)):
                y0 = target
            elif d < 45 and self.hpf > 0.3:
                y0 = target
        s2 = self.outside or self.zone_slack < f.zone_lead_s
        if y0 is None and target is not None and getattr(self, 'evade', False):
            f2 = self.cover_spot()
            if f2 is None and self.los_to(target):
                y0 = target
        if y0 is None and target is not None and getattr(self, 'evade', False):
            f2 = self.cover_spot()
            e1 = f2 if f2 is not None else self.hold
            if s2 and f2 is not None and (not self.in_safe(f2[0], f2[1], 0)):
                e1 = self.hold
            e2 = target if self.los_to(target) else None
            return Intent('evade', goal=e1, target=target, shoot=e2, strafe=f2 is None, boost=True, dist_pref=200.0, note='cover' if f2 else 'open')
        if y0 is not None:
            w0 = self.pref_range(y0)
            e1 = None
            o0 = False
            if z0 and y0 is target:
                w0 = min(w0, f.get('squad_finish_range', 80.0))
            if s2:
                e1 = self.hold
            elif self.squad and f.get('squad_hyper') and (not self.los_to(y0)):
                e1 = (y0.pos[0], y0.pos[1])
            elif z0 and y0 is target and (not self.los_to(y0)):
                e1 = (y0.pos[0], y0.pos[1])
                o0 = s1.get('energy', 0) > 45
            if e1 is None and self.sq('fight_cube_m', 0) > 0 and (self.hpf > self.sq('fight_cube_hp', 0.5)):
                s0 = self.near_cube(self.sq('fight_cube_m', 0))
                if s0 is not None:
                    e1 = (s0.pos[0], s0.pos[1])
            return Intent('fight', goal=e1, target=y0, shoot=y0, dist_pref=w0, strafe=True, boost=s2 or self.outside or o0, note=f"{(r2 + ' ' if a1 and r2 else '')}{('fin ' if z0 and y0 is target else '')}d{int(d2(x, y, y0.pos[0], y0.pos[1]))}>{int(w0)}")
        b0 = target
        if s2:
            return Intent('zone', goal=self.hold, target=b0, shoot=None, boost=self.outside or self.zone_slack < 0, note=f'slack{int(self.zone_slack)}')
        if self.ffa_on('ffa_hide') and enemies:
            hm = f.get('ffa_hide_m', 170.0)
            near = [tr for (dd, tr) in enemies if dd < hm]
            if near and self.close_weapon(20.0) is None and (self.near_cube(12.0) is None):
                p2 = [tr for tr in near if self.their_los_to_point(tr, x, y, z)]
                if p2:
                    f2 = self.cover_spot(only=near)
                    self.ghost_spot = (t, f2)
                    if f2 is not None and d2(x, y, f2[0], f2[1]) <= f.get('ffa_hide_reach_m', 45.0):
                        self.ghost_t = t
                        return Intent('hold', goal=f2, target=p2[0], note='ghost')
                elif t - self.ghost_t < f.get('ffa_hide_wait_s', 6.0):
                    return Intent('hold', goal=None, target=near[0], note='ghost-wait')
        x0 = self.drop_goal()
        if x0 is not None:
            if x0.target is None:
                x0.target = b0
            return x0
        q1 = self.loot_goal_pick()
        if q1 is not None:
            if q1.target is None and b0 is not None:
                q1.target = b0
            return q1
        v1 = 'hold'
        e1 = self.hold
        if b0 is not None:
            f2 = self.ghost_spot[1] if self.ghost_spot[0] == t else self.cover_spot(only=[b0])
            if f2 is not None:
                e1 = f2
                v1 = 'hide'
        elif self.ffa_on('ffa_snug'):
            f2 = self.snug_spot()
            if f2 is not None:
                e1 = f2
                v1 = 'snug'
        return Intent('hold', goal=e1, target=b0, note=v1)

    def ring_override(self, intent: Intent) -> Intent:
        f = self.P
        if intent.mode in ('zone', 'dodge', 'alert') or self.outside or self.zone_slack < f.zone_lead_s:
            return intent
        (o0, p0) = (self.pos[0], self.pos[1])
        j = None
        for a0 in self.obs.get('drops') or []:
            (b0, e0) = (a0['pos'][0], a0['pos'][1])
            q = d2(o0, p0, b0, e0)
            if q > f.get('squad_ring_m', 320.0):
                continue
            if (a0.get('lands_in', 0) or 0) > q / max(1.0, self.walk) + f.get('squad_ring_early_s', 8.0):
                continue
            if not self.in_safe(b0, e0, 0) and self.zone_slack < 20:
                continue
            if self.goal_bad(b0, e0, 8.0):
                continue
            if a0.get('holder') not in (None, self.slot):
                continue
            if j is None or q < j[0]:
                j = (q, b0, e0, float(a0.get('radius', 8.0)))
        if j is None:
            return intent
        (q, b0, e0, k0) = j
        (m0, i0) = (self.t, self.me['hp'])
        if q > f.get('squad_ring_near_m', 60.0):
            g0 = self.ring_go
            if g0 is not None and (d2(g0[0], g0[1], b0, e0) > 5.0 or m0 - g0[2] > 45.0):
                g0 = self.ring_go = None
            if g0 is not None and g0[3] - i0 > f.get('squad_ring_abort_hp', 300.0):
                self.ring_go = None
                self.ring_wait_until = m0 + f.get('squad_ring_wait_s', 6.0)
                return intent
            if g0 is None:
                if m0 < self.ring_wait_until:
                    return intent
                if self.hostile or m0 - self.W.last_damage_t < f.get('squad_ring_quiet_s', 2.0):
                    return intent
                if self.hpf >= f.get('squad_ring_hp', 0.65):
                    return intent
                self.ring_go = (b0, e0, m0, i0)
        elif self.hpf >= f.get('squad_ring_near_hp', 0.85) and (self.hostile or m0 - self.W.last_damage_t < 2.0):
            return intent
        self.ring = (b0, e0, k0)
        j0 = q < k0 * 0.6
        l = q > 40 and self.me.get('energy', 0) > 40
        n0 = intent.target
        if intent.mode in ('fight', 'evade') and n0 is not None:
            l0 = intent.shoot if intent.shoot is not None else n0 if self.los_to(n0) else None
            return Intent('fight', goal=(b0, e0), target=n0, shoot=l0, dist_pref=None, strafe=True, boost=l, note='ring-cap' if j0 else f'ring{int(q)}')
        return Intent('drop', goal=(b0, e0), target=n0, shoot=intent.shoot, boost=l, note='ring-cap' if j0 else f'ring{int(q)}')

    def return_fire(self, intent: Intent):
        t = self.t
        (g0, i0) = (self.pos[0], self.pos[1])
        intent.evasive = bool(self.hostile) or t - self.W.last_damage_t < 1.5
        if intent.shoot is not None or intent.mode in ('heal',):
            return
        (j, f) = (None, 1000000000.0)
        q = getattr(self, 'gate', False)
        for (l, e0) in self.hostile:
            if q and l >= max(self.P.get('ffa_gate_bump_m', 30.0), self.gate_from) and (not self.ffa_provoked(e0, l)) and (self.ffa_open_ok(e0, l, 1.0, False) != 'close'):
                continue
            if self.my_dps(l, e0) > 0 and self.los_to(e0) and (l < f):
                (j, f) = (e0, l)
        if j is None:
            a0 = {e[2] for e in self.W.dmg_taken_log[-8:] if t - e[0] < 3.0 and e[3] == 'npc'}
            for b0 in self.W.npcs.values():
                if b0.id in a0 and t - b0.last_seen < 0.3:
                    l = d2(g0, i0, b0.pos[0], b0.pos[1])
                    if l < 120 and self.los_to(b0) and (l < f):
                        (j, f) = (b0, l)
        if j is not None:
            intent.shoot = j
            if intent.target is None:
                intent.target = j

    def pref_range(self, tr):
        if self.squad and self.P.get('squad_range') and (self.t >= self.P.get('sqk_after_s', 30.0)) and ('lightning' not in self.kinds):
            e0 = self.P.get('squad_range_laser', 120.0) if 'laser' in self.kinds else self.P.get('squad_range_gun', 85.0)
            if 'lightning' in (tr.weapons or []):
                e0 = max(e0, 64.0)
            return e0
        b0 = max(1.0, self.me['hp'])
        g0 = max(1.0, tr.hp)
        (k0, l0) = (self.pos[0], self.pos[1])
        q = d2(k0, l0, tr.pos[0], tr.pos[1])
        (j, l) = (q, -1000000000.0)
        for e0 in HOLD_RANGES:
            a0 = self.my_dps(e0, tr)
            if a0 <= 0:
                continue
            i0 = self.their_dps(tr, e0, ranging=True)
            f = a0 / g0 / (i0 / b0 + 0.001)
            j0 = math.log(f + 1e-06) - abs(e0 - q) * 0.0015
            if j0 > l:
                (l, j) = (j0, e0)
        if tr.hp < 320 and self.hpf > 0.5 and ('lightning' in self.kinds):
            j = min(j, 35.0)
        return j

    def cover_spot(self, only=None, pseudo=None):
        t = self.t
        if self.retreat_spot is not None and t - self.retreat_spot_t < 0.8 and (only is None) and (pseudo is None):
            return self.retreat_spot
        if only is not None:
            o0 = tuple((tr.id for tr in only))
            b0 = getattr(self, '_hide_cache', None)
            if b0 is not None and b0[0] == o0 and (t - b0[1] < 0.6):
                return b0[2]
        if pseudo is not None:
            b0 = getattr(self, '_pseudo_cache', None)
            if b0 is not None and t - b0[1] < 0.8 and (d2(b0[0][0], b0[0][1], pseudo[0], pseudo[1]) < 40):
                return b0[2]
        (x, y, f1) = self.pos
        e1 = time.perf_counter()
        y0 = self.A.rays + int(self.P.get('cover_ray_budget', 150))
        l0 = float(self.P.get('cover_guard_s', 0.015))
        if only is not None:
            eyes = [(tr.pos[0], tr.pos[1], tr.pos[2] + 8.0 * gn.body_scale(tr.cubes, self.size_per_cube)) for tr in only]
        else:
            eyes = [(tr.pos[0], tr.pos[1], tr.pos[2] + 8.0 * gn.body_scale(tr.cubes, self.size_per_cube)) for tr in self.W.mechs.values() if t - tr.last_seen < 6.0 and d2(x, y, tr.pos[0], tr.pos[1]) < 230]
        if pseudo is not None:
            eyes.append((pseudo[0], pseudo[1], self.A.terrain(pseudo[0], pseudo[1]) + 8.0))
        if not eyes:
            return None
        f = self.A
        j = self.N
        a1 = sum((e[0] for e in eyes)) / len(eyes)
        b1 = sum((e[1] for e in eyes)) / len(eyes)
        q = bearing(a1, b1, x, y)
        e0 = []
        for w0 in (8.0, 14.0, 22.0, 32.0, 45.0, 60.0, 78.0, 95.0):
            for n0 in range(16):
                p0 = (n0 - 7.5) * (math.pi / 8.0)
                l = q + p0
                e0.append((w0 * 0.6 + abs(p0) * 9.0, w0, l))
        e0.sort()
        a0 = None
        k0 = None
        (m0, x0) = (9.0 * self.scale, 2.5 * self.scale)
        for (q0, w0, l) in e0:
            if self.A.rays > y0 or time.perf_counter() - e1 > l0:
                break
            (s0, u0) = (x + w0 * math.cos(l), y + w0 * math.sin(l))
            if not j.cspace_free(s0, u0):
                continue
            if not self.in_safe(s0, u0, 0) and (self.outside or self.zone_slack < 20):
                continue
            v0 = f.terrain(s0, u0)
            z0 = False
            for (g0, i0, j0) in eyes:
                if f.los(g0, i0, j0, s0, u0, v0 + 0.5 * m0):
                    z0 = True
                    break
            if z0:
                continue
            for (g0, i0, j0) in eyes:
                if self.exposed_point(g0, i0, j0, s0, u0, v0, m0, x0) is not None:
                    z0 = True
                    break
            if z0:
                continue
            if j.segment_free(x, y, s0, u0):
                a0 = (s0, u0)
                break
            if k0 is None:
                k0 = (s0, u0)
        if a0 is None:
            a0 = k0
        if only is None and pseudo is None:
            self.retreat_spot = a0
            self.retreat_spot_t = t
        elif only is not None:
            self._hide_cache = (tuple((tr.id for tr in only)), t, a0)
        else:
            self._pseudo_cache = (pseudo, t, a0)
        return a0

    def drop_goal(self):
        f = self.P
        if not f.drop_go:
            return None
        l = self.ffa_on('ffa_loot')
        (o0, p0) = (self.pos[0], self.pos[1])
        j = None
        for e0 in self.obs.get('drops') or []:
            (g0, i0) = (e0['pos'][0], e0['pos'][1])
            a0 = d2(o0, p0, g0, i0)
            if a0 > f.drop_max_dist:
                continue
            j0 = a0 / max(1.0, self.walk)
            if e0.get('lands_in', 0) > j0 + 6:
                continue
            if not self.in_safe(g0, i0, 0) and self.zone_slack < 20:
                continue
            if self.goal_bad(g0, i0, 8.0):
                continue
            q = False
            for (b0, n0) in self.enemies:
                if d2(n0.pos[0], n0.pos[1], g0, i0) < 65 or b0 < a0:
                    q = True
            if e0.get('holder') not in (None, self.slot):
                q = True
            if q:
                continue
            if l and self.ffa_loot_risky(g0, i0):
                continue
            if self.hpf < f.drop_hp_frac:
                continue
            if j is None or a0 < j[0]:
                j = (a0, e0)
        if j is None:
            return None
        (a0, e0) = j
        m0 = e0.get('radius', 8.0)
        (k0, l0) = (e0['pos'][0], e0['pos'][1])
        if a0 < m0 * 0.6:
            return Intent('drop', goal=(k0, l0), note=f"cap{e0.get('progress', 0):.1f}")
        return Intent('drop', goal=(k0, l0), boost=a0 > 40 and self.me.get('energy', 0) > 40, note='drop')

    def weapon_value(self, kind):
        j = self.P.weapon_value.get(kind, 0.0)
        f = self.P.get('gun_laser_always') or (self.P.get('gun_laser_unless_ffa') and (not self.W.is_ffa())) or (getattr(self, 'squad', False) and self.P.get('squad_gun_laser'))
        if kind == 'lightning' and f:
            return self.P.get('squad_lightning_value', 4.0)
        if kind == 'lightning' and self.zn[1] <= self.P.late_radius:
            j += self.P.late_lightning_bonus
        return j

    def loadout_gain(self, kind):
        kinds = self.kinds
        if kind in kinds:
            return (0.0, None)
        if None in kinds:
            return (self.weapon_value(kind), None)
        f = [self.weapon_value(k) for k in kinds]
        l = 0 if f[0] <= f[1] else 1
        j = self.weap[l]
        if j and j.get('kind') == 'laser' and ((j.get('charge') or 0) > 0):
            return (0.0, None)
        return (self.weapon_value(kind) - f[l], l)

    def near_cube(self, radius):
        if self.me.get('cubes', 0) >= 21:
            return None
        (a0, b0, e0) = self.pos
        (j, f) = (None, radius)
        for q in self.W.cubes.values():
            l = d2(a0, b0, q.pos[0], q.pos[1])
            if l > f or abs(q.pos[2] - e0) > 5:
                continue
            if not self.N.cspace_free(q.pos[0], q.pos[1]) and (not self.N.is_free(q.pos[0], q.pos[1])):
                continue
            if not self.in_safe(q.pos[0], q.pos[1], 0):
                continue
            (j, f) = (q, l)
        return j

    def close_weapon(self, radius):
        (b0, e0, g0) = self.pos
        f = None
        for q in self.W.pickups.values():
            if self.blacklist.get(q.id, -1) > self.t:
                continue
            if abs(q.pos[2] - g0) > 4:
                continue
            j = d2(b0, e0, q.pos[0], q.pos[1])
            if j > radius:
                continue
            (l, a0) = self.loadout_gain(q.kind)
            if l >= max(5.0, self.P.swap_margin) and (f is None or j < f[0]):
                f = (j, q)
        return f[1] if f else None

    def loot_goal_pick(self):
        f = self.P
        j = self.W
        (x, y, z) = self.pos
        u0 = self.t
        (hx, hy) = self.hold
        base = d2(x, y, hx, hy)
        q0 = min(self.zone_slack, 120.0)
        q = max(0.0, q0 - 6.0) * self.walk * 0.5
        b0 = 0.25 if self.zn[1] > 150 else 0.45
        l = None
        a0 = self.ffa_on('ffa_loot')

        def detour(px, py):
            return d2(x, y, px, py) + d2(px, py, hx, hy) - base

        def reachable(px, py, pz):
            return abs(pz - z) <= 5 or self.N.is_free(px, py)
        for n0 in j.pickups.values():
            (l0, s0) = self.loadout_gain(n0.kind)
            if l0 < f.swap_margin and (not (s0 is None and l0 > 0)):
                continue
            if self.blacklist.get(n0.id, -1) > u0 or not reachable(*n0.pos) or self.goal_bad(n0.pos[0], n0.pos[1]):
                continue
            if not self.N.is_free(n0.pos[0], n0.pos[1]) and (not self.N.cspace_free(n0.pos[0], n0.pos[1])):
                continue
            i0 = detour(n0.pos[0], n0.pos[1])
            if i0 > q + 15:
                continue
            if a0 and self.ffa_loot_risky(n0.pos[0], n0.pos[1]):
                continue
            p0 = 12.0 * l0 - b0 * i0
            if p0 > 0 and (l is None or p0 > l[0]):
                l = (p0, Intent('loot', goal=(n0.pos[0], n0.pos[1]), pickup=n0, note=f'{n0.kind}'))
        e0 = self.me.get('cubes', 0)
        if e0 < 21:
            v0 = 60.0 if e0 < 10 else 12.0
            for n0 in j.cubes.values():
                if not reachable(*n0.pos) or (not self.N.cspace_free(n0.pos[0], n0.pos[1]) and (not self.N.is_free(n0.pos[0], n0.pos[1]))):
                    continue
                if self.goal_bad(n0.pos[0], n0.pos[1]):
                    continue
                i0 = detour(n0.pos[0], n0.pos[1])
                if i0 > q + 15:
                    continue
                if a0 and d2(x, y, n0.pos[0], n0.pos[1]) > f.get('ffa_loot_near_m', 25.0) and (i0 > f.get('ffa_cube_detour_m', 60.0) or self.ffa_loot_risky(n0.pos[0], n0.pos[1])):
                    continue
                p0 = v0 - b0 * i0
                if p0 > 0 and (l is None or p0 > l[0]):
                    l = (p0, Intent('loot', goal=(n0.pos[0], n0.pos[1]), note='cube'))
        if l is not None:
            n0 = l[1]
            n0.shoot = self.farm_target(walking=True)
            return n0
        k0 = self.farm_target(walking=False)
        if k0 is not None:
            o0 = 'npc' if getattr(k0, 'kind', '') == 'npc' else 'crate'
            m0 = None
            if o0 == 'npc':
                g0 = d2(x, y, k0.pos[0], k0.pos[1])
                if g0 > 95:
                    m0 = (k0.pos[0], k0.pos[1])
            return Intent('farm', goal=m0, shoot=k0, target=k0 if o0 == 'npc' else None, dist_pref=75.0 if o0 == 'npc' else None, strafe=o0 == 'npc', note=o0)
        j0 = self.explore_goal(q)
        if j0 is not None:
            return Intent('explore', goal=j0, note='explore')
        return None

    def explore_goal(self, budget_m):
        if budget_m < 80 or (self.me.get('cubes', 0) >= 10 and 'laser' in self.kinds):
            return None
        if 'laser' in self.kinds and self.ffa_on('ffa_hide'):
            return None
        (u0, v0) = (self.pos[0], self.pos[1])
        s0 = self.visited
        e0 = 40.0
        (q, a0) = (int(math.floor(u0 / e0)), int(math.floor(v0 / e0)))
        for j0 in (-1, 0, 1):
            for k0 in (-1, 0, 1):
                s0.add((q + j0, a0 + k0))
        g0 = self.explore_target
        if g0 is not None and (int(math.floor(g0[0] / e0)), int(math.floor(g0[1] / e0))) not in s0 and (self.t - self.explore_t < 25) and (not self.goal_bad(g0[0], g0[1], 20.0)):
            return g0
        (b0, q0) = self.zn
        (l, p0) = self.zcur
        (j, f) = (None, 1e+18)
        for j0 in range(-6, 7):
            for k0 in range(-6, 7):
                l0 = (q + j0, a0 + k0)
                if l0 in s0:
                    continue
                (n0, o0) = ((l0[0] + 0.5) * e0, (l0[1] + 0.5) * e0)
                i0 = math.hypot(n0 - u0, o0 - v0)
                if i0 > min(budget_m * 0.5, 260):
                    continue
                if d2(n0, o0, l[0], l[1]) > p0 - 25:
                    continue
                if self.zone_slack < 60 and d2(n0, o0, b0[0], b0[1]) > q0:
                    continue
                if self.goal_bad(n0, o0, 20.0):
                    continue
                m0 = self.N.nearest_free(n0, o0, 6)
                if m0 is None:
                    s0.add(l0)
                    continue
                if i0 < f:
                    (f, j) = (i0, self.N.pos(m0))
        self.explore_target = j
        self.explore_t = self.t
        return j

    def farm_target(self, walking):
        f = self.P
        if self.enemies:
            g0 = [d for (d, tr) in self.enemies if d < 200]
            if g0:
                return None
        (m0, n0, o0) = self.pos
        (q, l) = (None, 1000000000.0)
        if self.hpf < self.sq('farm_regen_hp', 0.85) and self.t - self.W.last_damage_t < 20:
            return None
        if self.ffa_on('ffa_quiet'):
            (i0, j0) = (f.get('ffa_quiet_m', 250.0), f.get('ffa_quiet_s', 8.0))
            for tr in self.W.mechs.values():
                if self.t - tr.last_seen < j0 and d2(m0, n0, tr.pos[0], tr.pos[1]) < i0:
                    return None
            for (k0, j, l0, e0, a0) in self.W.threats:
                if self.t - k0 < 5.0 and a0 == 'near' and (e0 in ('gun', 'laser', 'laser_charge', 'lightning', 'mortar')):
                    return None
        if self.me.get('cubes', 0) < 14:
            for b0 in self.W.crates.values():
                d = d2(m0, n0, b0.pos[0], b0.pos[1])
                if d > f.crate_shoot_range or d < 4:
                    continue
                if self.t - b0.last_seen > 0.3:
                    continue
                if not self.in_safe(b0.pos[0], b0.pos[1], -25):
                    continue
                if not self.A.los(m0, n0, o0 + self.muzzle_h, b0.pos[0], b0.pos[1], b0.pos[2] + 1.2):
                    continue
                if d < l:
                    (q, l) = (b0, d)
        if f.npc_hunt and self.hpf > self.sq('npc_min_hp_frac', 0.8) and (self.me.get('cubes', 0) < 12):
            for tr in self.W.npcs.values():
                if self.t - tr.last_seen > 0.25:
                    continue
                d = d2(m0, n0, tr.pos[0], tr.pos[1])
                if d > self.sq('npc_hunt_range', 140.0):
                    continue
                if not self.in_safe(tr.pos[0], tr.pos[1], -25):
                    continue
                if not self.los_to(tr):
                    continue
                if d * 0.8 < l:
                    (q, l) = (tr, d * 0.8)
        return q

    def goal_bad(self, px, py, r=6.0):
        l = self.t
        for (f, j, q) in self.bad_goals:
            if q > l and abs(f - px) < r and (abs(j - py) < r):
                return True
        return False

    def watchdog(self, intent: Intent):
        a0 = intent.goal
        if a0 is None or intent.mode in ('fight', 'evade', 'farm', 'dodge', 'alert'):
            self.wd_key = None
            return
        t = self.t
        f = self.P
        q = d2(self.pos[0], self.pos[1], a0[0], a0[1])
        e0 = (intent.mode, int(a0[0] // 8), int(a0[1] // 8))
        if e0 != self.wd_key:
            (self.wd_key, self.wd_best, self.wd_t, self.wd_escapes) = (e0, q, t, 0)
            return
        if q < self.wd_best - 1.5 or t < self.escape_until:
            self.wd_best = min(self.wd_best, q)
            self.wd_t = t
            return
        if q <= (0.0 if intent.mode in ('loot', 'explore') else 5.0):
            self.wd_t = t
            return
        if t - self.wd_t < f.get('wd_stall_s', 4.0):
            return
        self.bad_goals = [b for b in self.bad_goals if b[2] > t]
        if intent.mode in ('loot', 'explore', 'drop'):
            b = (int(a0[0] // 8), int(a0[1] // 8))
            g0 = self.bad_count[b] = self.bad_count.get(b, 0) + 1
            l = f.get('wd_ban_s', 60.0) * 2 ** min(g0 - 1, 4)
            self.bad_goals.append((a0[0], a0[1], t + l))
            if intent.pickup is not None:
                self.blacklist[intent.pickup.id] = t + l
            if intent.mode == 'explore':
                self.explore_target = None
            self.wd_key = None
            return
        b0 = self.wd_escapes
        j = math.atan2(a0[1] - self.pos[1], a0[0] - self.pos[0]) + (0.0, 0.9, -0.9, 1.8, -1.8)[b0 % 5]
        self.escape_dir = (math.cos(j), math.sin(j))
        self.escape_until = t + f.get('wd_escape_s', 1.5)
        self.wd_escapes = b0 + 1
        self.wd_t = t
        self.path = []
        self.search = None
        if intent.mode in ('retreat', 'heal'):
            self.retreat_spot = None

    def plan_to(self, goal):
        (o0, p0) = (self.pos[0], self.pos[1])
        (b0, e0) = goal
        n0 = self.t
        f = self.N
        if self.ticks % 2 == 0 or not self.path or self.path_goal is None or (d2(b0, e0, self.path_goal[0], self.path_goal[1]) > 3.0):
            if f.segment_free(o0, p0, b0, e0):
                self.path = [(b0, e0)]
                self.path_goal = (b0, e0)
                self.path_t = n0
                self.search = None
                return
        m0 = self.path_goal is None or d2(b0, e0, self.path_goal[0], self.path_goal[1]) > 8.0 or n0 - self.path_t > 5.0 or (not self.path)
        if self.search is not None and d2(b0, e0, self.search_goal[0], self.search_goal[1]) > 20.0:
            self.search = None
        if m0 and self.search is None:
            self.search = f.astar_iter(o0, p0, b0, e0, chunk=250)
            self.search_goal = (b0, e0)
        if self.search is not None:
            j = self.P
            g0 = int(j.get('astar_chunks_path', 5) if self.path else j.get('astar_chunks_nopath', 7))
            q = self.A.rays - self.rays0 - int(j.get('astar_free_rays', 160))
            if q > 0:
                g0 -= (q + 29) // 30
            a0 = float(j.get('tick_guard_s', 0.02))
            for l in range(max(0, g0)):
                if time.perf_counter() - self.tick_t0 > a0:
                    break
                k0 = next(self.search, ([(b0, e0)], False))
                if k0 is not None:
                    (j0, i0) = k0
                    self.search = None
                    l0 = f.smooth(j0, o0, p0)
                    self.path = l0[1:] if len(l0) > 1 else [self.search_goal]
                    self.path_goal = self.search_goal
                    self.path_t = n0
                    break
        if not self.path:
            self.path = [(b0, e0)]
            self.path_goal = (b0, e0)
            self.path_t = n0

    def follow(self):
        (b0, e0) = (self.pos[0], self.pos[1])
        while len(self.path) > 1 and d2(b0, e0, self.path[0][0], self.path[0][1]) < 2.5:
            self.path.pop(0)
        if len(self.path) > 1 and self.ticks % 2 == 0 and self.N.segment_free(b0, e0, self.path[1][0], self.path[1][1]):
            self.path.pop(0)
        if not self.path:
            return (0.0, 0.0, 0.0)
        (q, a0) = self.path[0]
        f = d2(b0, e0, q, a0)
        (j, l) = unit(q - b0, a0 - e0)
        return (j, l, f)

    def pilot(self, intent: Intent):
        f = self.P
        b2 = self.t
        (i2, j2, k2) = self.pos
        g1 = self.me
        i1 = j1 = 0.0
        a0 = bool(intent.boost)
        a1 = False
        if intent.goal is not None:
            self.plan_to(intent.goal)
            (s0, u0, l0) = self.follow()
            n0 = len(self.path) <= 1
            if n0 and l0 < 1.5:
                s0 = u0 = 0.0
            elif n0 and l0 < 6:
                y1 = l0 / 6.0
                (s0, u0) = (s0 * y1, u0 * y1)
            (i1, j1) = (s0, u0)
        c2 = intent.target
        if intent.evasive and (not intent.strafe) and (i1 or j1) and (c2 is not None):
            if b2 >= self.strafe_until:
                if self.rng.random() < 0.75:
                    self.strafe_dir = -self.strafe_dir
                self.strafe_until = b2 + self.rng.uniform(f.strafe_min_s, f.strafe_max_s)
            q = bearing(i2, j2, c2.pos[0], c2.pos[1])
            (o1, p1) = (-math.sin(q) * self.strafe_dir, math.cos(q) * self.strafe_dir)
            (i1, j1) = unit(i1 + o1 * 0.8, j1 + p1 * 0.8)
        if intent.strafe and c2 is not None:
            q = bearing(i2, j2, c2.pos[0], c2.pos[1])
            j0 = d2(i2, j2, c2.pos[0], c2.pos[1])
            if not a0 and self.fight_boost():
                a0 = True
            if b2 >= self.strafe_until:
                if self.rng.random() < 0.75:
                    self.strafe_dir = -self.strafe_dir
                self.strafe_until = b2 + self.rng.uniform(f.strafe_min_s, f.strafe_max_s)
            (o1, p1) = (-math.sin(q) * self.strafe_dir, math.cos(q) * self.strafe_dir)
            s1 = 0.0
            if intent.dist_pref is not None:
                s1 = clamp((j0 - intent.dist_pref) / 25.0, -1.0, 1.0)
            (w1, x1) = (math.cos(q) * s1, math.sin(q) * s1)
            if intent.goal is not None:
                h2 = 0.9
                (g0, i0) = (i1 * h2 + o1 * 0.6 + w1 * 0.3, j1 * h2 + p1 * 0.6 + x1 * 0.3)
                (m1, n1) = (i1 * h2 - o1 * 0.6 + w1 * 0.3, j1 * h2 - p1 * 0.6 + x1 * 0.3)
            else:
                (g0, i0) = (o1 * 0.75 + w1 * 0.9, p1 * 0.75 + x1 * 0.9)
                (m1, n1) = (-o1 * 0.75 + w1 * 0.9, -p1 * 0.75 + x1 * 0.9)
            (i1, j1) = unit(g0, i0)
            if self.squad and f.get('squad_spot'):
                z1 = self.fight_spot(c2, intent.dist_pref, (i1, j1), unit(m1, n1))
                if z1 is not None:
                    (w0, x0, o0, f1) = z1
                    if o0:
                        self.strafe_dir = -self.strafe_dir
                        self.strafe_until = b2 + self.rng.uniform(f.strafe_min_s, f.strafe_max_s)
                        (m1, n1) = (g0, i0)
                    (i1, j1) = (w0 * f1, x0 * f1)
            if self.P.get('dodge_pred') or self.sqk('dodge_pred', False):
                a2 = self.move_speed(a0)
                e1 = self.bullet_miss(i1, j1, a2)
                v0 = self.mc.get('radius', 2.5) * self.scale + 0.6
                if e1 < v0:
                    (p0, q0) = unit(m1, n1)
                    j = self.bullet_miss(p0, q0, a2)
                    if j >= v0 or j > e1 + 1.0:
                        self.strafe_dir = -self.strafe_dir
                        self.strafe_until = b2 + self.rng.uniform(f.strafe_min_s, f.strafe_max_s)
                        (i1, j1) = (p0, q0)
            elif self.bullet_inbound():
                self.strafe_dir = -self.strafe_dir
                self.strafe_until = b2 + self.rng.uniform(f.strafe_min_s, f.strafe_max_s)
        if self.P.get('dodge_all') or self.sqk('dodge_all', False):
            (i1, j1) = self.bullet_filter(i1, j1, a0)
        if self.ring is not None:
            (t1, u1, v1) = self.ring
            k0 = d2(i2, j2, t1, u1)
            if k0 < v1 * 0.85:
                a0 = False
                if d2(i2 + i1 * 3.0, j2 + j1 * 3.0, t1, u1) > v1 * 0.55:
                    if k0 > 1.0:
                        b1 = min(1.0, k0 / 4.0)
                        (i1, j1) = ((t1 - i2) / k0 * b1, (u1 - j2) / k0 * b1)
                    else:
                        i1 = j1 = 0.0
                else:
                    (i1, j1) = (i1 * 0.5, j1 * 0.5)
        m0 = self.mortar_dodge()
        if m0 is not None:
            (i1, j1) = m0
            a0 = g1.get('energy', 0) > 10
        g2 = self.P.get('wd_on', True)
        if not self.outside and (i1 or j1) and (not (g2 and (intent.mode == 'zone' or b2 < self.escape_until))):
            (b0, q1) = self.zcur
            (k1, l1) = (i2 + i1 * 6, j2 + j1 * 6)
            if d2(k1, l1, b0[0], b0[1]) > q1 - 1.5 and d2(i2, j2, b0[0], b0[1]) > q1 - 10:
                (y0, z0) = unit(b0[0] - i2, b0[1] - j2)
                (i1, j1) = unit(i1 + y0 * 1.2, j1 + z0 * 1.2)
        if i1 or j1:
            (i1, j1) = self.steer(i1, j1)
        a2 = math.hypot(g1['vel'][0], g1['vel'][1])
        e0 = math.hypot(i1, j1) > 0.5
        if e0 and a2 < 1.5 and g1.get('grounded', True):
            if self.stuck_since is None:
                self.stuck_since = b2
            elif b2 - self.stuck_since > 0.6:
                self.unstick_until = b2 + 0.9
                l = math.atan2(j1, i1) + self.rng.choice((-1, 1)) * self.rng.uniform(0.6, 1.6)
                self.unstick_dir = (math.cos(l), math.sin(l))
                self.stuck_since = None
                self.path = []
                self.search = None
        else:
            self.stuck_since = None
        if b2 < self.unstick_until:
            a1 = g1.get('grounded', False)
            a0 = g1.get('energy', 0) > 15
            (e2, f2) = self.unstick_dir
            (i1, j1) = unit(i1 * 0.4 + e2, j1 * 0.4 + f2)
        if g2 and b2 < self.escape_until:
            a1 = g1.get('grounded', False)
            a0 = g1.get('energy', 0) > 15
            (i1, j1) = self.escape_dir
        if a0 and g1.get('energy', 0) < 22 and (not self.outside):
            a0 = False
        if intent.mode == 'hold' and (not self.enemies) and (i1 or j1):
            pass
        self.last_move = (i1, j1)
        return ((i1, j1), a0, a1)

    def fight_spot(self, tr, dist_pref, keep, alt):
        P = self.P
        m0 = self.t
        (p0, q0, s0) = self.pos
        others = [o for (dd, o) in self.enemies if o is not tr and dd < P.get('squad_spot_m', 170.0)][:2]
        if not others:
            self.spot_cache = None
            return None
        e0 = self.spot_cache
        if e0 is not None and e0[1] == tr.id and (m0 - e0[0] < 0.25):
            return e0[2]
        (A, f) = (self.A, self.N)
        j = P.get('squad_spot_r', 7.0)
        eyes = [(o.pos[0], o.pos[1], o.pos[2] + 8.0 * gn.body_scale(o.cubes, self.size_per_cube)) for o in others]
        (tx, ty) = (tr.pos[0], tr.pos[1])
        tz = tr.pos[2] + 4.5 * gn.body_scale(tr.cubes, self.size_per_cube)
        hb = 4.5 * self.scale

        def score(px, py, pz):
            q = 0.0
            if dist_pref is not None:
                q -= 1.1 * abs(d2(px, py, tx, ty) - dist_pref)
            if A.los(px, py, pz + self.muzzle_h, tx, ty, tz):
                q += 45.0
            for (f, j, l) in eyes:
                if A.los(f, j, l, px, py, pz + hb):
                    q -= 30.0
            return q
        q = bearing(p0, q0, tx, ty)
        (a0, b0) = (None, score(p0, q0, s0) - 12.0)
        (l0, k0) = (None, -9.0)
        for i0 in range(8):
            l = q + i0 * (math.pi / 4)
            (n0, o0) = (math.cos(l), math.sin(l))
            (px, py) = (p0 + n0 * j, q0 + o0 * j)
            if not f.cspace_free(px, py) or not f.segment_free(p0, q0, px, py) or (not self.in_safe(px, py, 0)):
                continue
            g0 = n0 * keep[0] + o0 * keep[1]
            s = score(px, py, A.terrain(px, py)) + 9.0 * g0
            if g0 > k0:
                (l0, k0) = (s, g0)
            if s > b0:
                (a0, b0) = ((n0, o0), s)
        j0 = None
        if l0 is None or b0 > l0 + P.get('squad_spot_gain', 10.0):
            if a0 is None:
                j0 = (keep[0], keep[1], False, 0.3)
            else:
                j0 = (a0[0], a0[1], a0[0] * alt[0] + a0[1] * alt[1] > a0[0] * keep[0] + a0[1] * keep[1], 1.0)
        self.spot_cache = (m0, tr.id, j0)
        return j0

    def fight_boost(self):
        f = self.P
        q = f.get('fight_boost_e', 0.0)
        if not q or not (self.squad or f.get('fight_boost_ffa')):
            self.fb_on = False
            return False
        if not (getattr(self, 'hostile', None) or self.t - self.W.last_damage_t < 2.0):
            self.fb_on = False
            return False
        l = self.me.get('energy', 0)
        if l >= q:
            self.fb_on = True
        elif l < f.get('fight_boost_stop_e', 26.0):
            self.fb_on = False
        if self.fb_on:
            return True
        j = f.get('fight_boost_charge_e', 0.0)
        return bool(j) and l >= j and any((w and w.get('kind') == 'laser' and ((w.get('charge') or 0) > 0) for w in self.weap))

    def steer(self, mx, my):
        (e0, g0) = (self.pos[0], self.pos[1])
        f = self.N
        if f.cspace_free(e0 + mx * 3.0, g0 + my * 3.0) and f.cspace_free(e0 + mx * 1.5, g0 + my * 1.5):
            return (mx, my)
        l = math.atan2(my, mx)
        for b0 in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
            j = l + b0 * 0.45
            (q, a0) = (math.cos(j), math.sin(j))
            if f.cspace_free(e0 + q * 3.0, g0 + a0 * 3.0) and f.cspace_free(e0 + q * 1.5, g0 + a0 * 1.5):
                return (q, a0)
        return (mx, my)

    def bullet_filter(self, mvx, mvy, boost):
        if not self.obs.get('projectiles'):
            return (mvx, mvy)
        i0 = bool(mvx or mvy)
        j0 = self.move_speed(boost)
        e0 = self.bullet_miss(mvx, mvy, j0 if i0 else 0.0)
        a0 = self.mc.get('radius', 2.5) * self.scale + 0.6
        if e0 >= a0:
            return (mvx, mvy)
        j = math.atan2(mvy, mvx) if i0 else 0.0
        (l, q) = (None, e0 + 1.0)
        for b0 in (1, -1, 2, -2, 3, -3, 4):
            f = j + b0 * (math.pi / 4)
            (k0, l0) = (math.cos(f), math.sin(f))
            g0 = self.bullet_miss(k0, l0, j0)
            if g0 >= a0 + 0.75:
                return (k0, l0)
            if g0 > q:
                (l, q) = ((k0, l0), g0)
        return l if l is not None else (mvx, mvy)

    def move_speed(self, boost):
        f = self.walk
        if boost and self.me.get('energy', 0) >= 22:
            j = self.me.get('stats') or {}
            f = float(j.get('boost_speed', self.mc.get('boost_speed', 19.5)))
        if any((w and w.get('kind') == 'laser' and ((w.get('charge') or 0) > 0) for w in self.weap)):
            f *= float(self.mc.get('charge_move_mult', 0.5))
        return f

    def bullet_miss(self, ux, uy, spd):
        q = 99.0
        s0 = self.obs.get('projectiles')
        if not s0:
            return q
        (f1, g1, i1) = self.pos
        (z0, a1) = (self.me['vel'][0], self.me['vel'][1])
        m0 = self.me.get('grounded', True)
        l = float(self.mc.get('ground_accel', 58.5) if m0 else self.mc.get('air_accel', 12.0))
        (b1, e1) = (ux * spd, uy * spd)
        (i0, j0) = (b1 - z0, e1 - a1)
        g0 = math.hypot(i0, j0)
        f = g0 / l if l > 0 else 0.0
        (n0, o0) = (0.5 * l * i0 / g0, 0.5 * l * j0 / g0) if g0 > 1e-06 else (0.0, 0.0)
        (k0, l0) = (f1 + z0 * f + n0 * f * f, g1 + a1 * f + o0 * f * f)
        x0 = i1 + self.mc.get('height', 9.0) * self.scale + 0.3
        for q0 in s0:
            if q0.get('kind') not in ('gun', 'npc'):
                continue
            (a0, b0) = (q0['pos'], q0['vel'])
            y0 = b0[0] * b0[0] + b0[1] * b0[1]
            if y0 < 1.0:
                continue
            w0 = ((f1 - a0[0]) * b0[0] + (g1 - a0[1]) * b0[1]) / y0
            if w0 <= 0.0 or w0 > 1.0:
                continue
            u0 = v0 = 0.0
            for j in range(3):
                if w0 <= f:
                    (u0, v0) = (f1 + z0 * w0 + n0 * w0 * w0, g1 + a1 * w0 + o0 * w0 * w0)
                else:
                    (u0, v0) = (k0 + b1 * (w0 - f), l0 + e1 * (w0 - f))
                w0 = max(0.0, ((u0 - a0[0]) * b0[0] + (v0 - a0[1]) * b0[1]) / y0)
            if w0 <= f:
                (u0, v0) = (f1 + z0 * w0 + n0 * w0 * w0, g1 + a1 * w0 + o0 * w0 * w0)
            else:
                (u0, v0) = (k0 + b1 * (w0 - f), l0 + e1 * (w0 - f))
            e0 = a0[2] + b0[2] * w0
            if e0 < i1 - 0.3 or e0 > x0:
                continue
            p0 = math.hypot(a0[0] + b0[0] * w0 - u0, a0[1] + b0[1] * w0 - v0)
            if p0 < q:
                q = p0
        return q

    def bullet_inbound(self):
        (a0, b0, e0) = self.pos
        f = e0 + 4.5 * self.scale
        for l in self.obs.get('projectiles') or []:
            if l.get('kind') not in ('gun', 'npc'):
                continue
            (q, j) = gn.closest_approach(l['pos'], l['vel'], a0, b0, f, 0.6)
            if q > 0.04 and j < 2.5 * self.scale + 0.8:
                return True
        return False

    def mortar_dodge(self):
        (l0, m0, n0) = self.pos
        l = self.wc.get('mortar', {}).get('gravity', 22.0)
        k0 = self.wc.get('mortar', {}).get('splash_radius', 7.0)
        for g0 in self.obs.get('projectiles') or []:
            if g0.get('kind') != 'mortar':
                continue
            (a0, b0, e0, q) = gn.shell_impact(g0['pos'], g0['vel'], l, self.A)
            i0 = l0 + self.me['vel'][0] * q
            j0 = m0 + self.me['vel'][1] * q
            if d2(i0, j0, a0, b0) < k0 + 3.0 * self.scale + 1.0 and q < 5.0:
                (f, j) = unit(l0 - a0, m0 - b0)
                if f == 0 and j == 0:
                    (f, j) = (1.0, 0.0)
                return (f, j)
        return None

    def lethal_now(self, lw, charge, full, tgt):
        f = self.P
        if not (f.get('squad_lethal_laser') and getattr(self, 'squad', False) and (self.t >= f.get('sqk_after_s', 30.0))):
            return False
        q = dict(self.wc.get('laser', {}))
        q.update(lw.get('stats') or {})
        if charge < q.get('min_charge', 0.15):
            return False
        l = q.get('min_frac', 0.35)
        j = q.get('damage', 315.0) * (l + (1.0 - l) * min(1.0, charge / max(full, 1e-06)))
        j *= self.cube_mult(self.me.get('cubes', 0))
        if 'mortar' in (getattr(tgt, 'weapons', None) or []):
            j *= 1.0 + q.get('cookoff', 0.6)
        return j * f.get('lethal_k', 0.7) >= max(1.0, getattr(tgt, 'hp', 1000000000.0) or 1000000000.0)

    def gunnery(self, intent: Intent):
        f2 = self.t
        j1 = self.me
        (p2, q2, s2) = self.pos
        (n2, o2) = (j1['vel'][0], j1['vel'][1])
        k0 = [False, False]
        u1 = False
        y1 = (p2 + n2 * 0.05, q2 + o2 * 0.05, s2 + self.muzzle_h)
        h2 = intent.shoot
        f = None
        y0 = next((i for (i, w) in enumerate(self.weap) if w and w['kind'] == 'laser'), None)
        n0 = next((i for (i, w) in enumerate(self.weap) if w and w['kind'] == 'gun'), None)
        a1 = next((i for (i, w) in enumerate(self.weap) if w and w['kind'] == 'lightning'), None)
        k1 = next((i for (i, w) in enumerate(self.weap) if w and w['kind'] == 'mortar'), None)
        (b0, a0) = (j1['aim'][0], j1['aim'][1])
        if h2 is not None:
            v0 = getattr(h2, 'kind', '') == 'mech'
            w0 = getattr(h2, 'kind', '') == 'npc'
            k2 = h2.pos
            m2 = getattr(h2, 'vel', None) or [0.0, 0.0, 0.0]
            if v0:
                x1 = gn.body_scale(h2.cubes, self.size_per_cube)
                (p0, t1) = (9.0 * x1, 2.5 * x1)
            elif w0:
                (p0, t1) = (3.5, 1.8)
            else:
                (p0, t1) = (2.4, 1.4)
            e0 = d2(p2, q2, k2[0], k2[1])
            g0 = self.exposed_point(y1[0], y1[1], y1[2], k2[0], k2[1], k2[2], p0, t1)
            b1 = g0 is not None
            j = 0.5 * p0
            if g0 is not None:
                j = g0[2]
                if g0[0] or g0[1]:
                    k2 = [k2[0] + g0[0] * 0.8, k2[1] + g0[1] * 0.8, k2[2]]
                    t1 *= 0.5
            m0 = None
            if n0 is not None:
                o0 = self.weap[n0]
                c2 = o0['stats'].get('speed', 252.0)
                z0 = 0.05 + min(0.05, max(0.0, o0.get('cooldown', 0.0)))
                (p1, q1, s1, g2) = gn.lead(y1, k2, m2, c2, z0, 3.0 * self.scale, j, self.A)
                m0 = gn.aim_to(y1, p1, q1, s1)
            e1 = None
            if y0 is not None:
                (p1, q1, s1) = gn.predict(k2, m2, 0.075, self.A)
                e1 = gn.aim_to(y1, p1, q1, s1 + j)
            f1 = self.weap[y0] if y0 is not None else None
            x0 = False
            if f1 is not None:
                l = f1.get('charge') or 0.0
                l0 = f1['stats'].get('charge', 0.75)
                w1 = f1['stats'].get('range', 280.0)
                u0 = b1 and e0 <= w1
                if l > 0:
                    if self.laser_hold_start is None:
                        self.laser_hold_start = f2 - l
                    q0 = f2 - self.laser_hold_start
                    if v0 and u0 and (l < l0 - 0.051) and self.lethal_now(f1, l, l0, h2):
                        x0 = True
                        j0 = abs(adiff(b0, e1[0]))
                        i0 = abs(a0 - e1[1])
                        i2 = gn.angular_radius(max(e0, 1.0), t1) * self.P.laser_aim_tol
                        j2 = gn.angular_radius(max(e0, 1.0), 0.45 * p0)
                        if j0 <= i2 and i0 <= j2:
                            k0[y0] = False
                            self.laser_hold_start = None
                        else:
                            k0[y0] = True
                    elif l >= l0 - 0.051 and u0:
                        x0 = True
                        j0 = abs(adiff(b0, e1[0]))
                        i0 = abs(a0 - e1[1])
                        i2 = gn.angular_radius(max(e0, 1.0), t1) * self.P.laser_aim_tol
                        j2 = gn.angular_radius(max(e0, 1.0), 0.45 * p0)
                        if j0 <= i2 and i0 <= j2:
                            k0[y0] = False
                            self.laser_hold_start = None
                        else:
                            k0[y0] = True
                    elif q0 > l0 + 1.6:
                        k0[y0] = False
                        self.laser_hold_start = None
                    else:
                        k0[y0] = True
                        if l >= l0 - 0.12 and u0:
                            x0 = True
                else:
                    self.laser_hold_start = None
                    if f1.get('cooldown', 0) <= 0.1 and (u0 or (e0 < 120 and v0)) and (v0 or w0 or e0 < 200):
                        if not (intent.mode == 'retreat' and (not v0)):
                            k0[y0] = True
            if x0 and e1 is not None:
                f = e1
            elif m0 is not None:
                f = m0
            elif e1 is not None:
                f = e1
            else:
                f = gn.aim_to(y1, k2[0], k2[1], k2[2] + j)
            if n0 is not None and m0 is not None and b1:
                o0 = self.weap[n0]
                if e0 <= o0['stats'].get('range', 230.0) * 0.98 and (o0.get('reload', 0) or 0) <= 0:
                    j0 = abs(adiff(b0, m0[0]))
                    i0 = abs(a0 - m0[1])
                    i2 = gn.angular_radius(max(e0, 1.0), t1) * self.P.gun_aim_tol + 0.004
                    j2 = gn.angular_radius(max(e0, 1.0), 0.45 * p0) + 0.004
                    z1 = self.P.get('gun_slop', 0.02)
                    if j0 <= i2 + z1 and i0 <= j2 + z1 and (not x0):
                        k0[n0] = True
                    elif e0 < 25:
                        k0[n0] = j0 < 0.3
            if a1 is not None and b1:
                g1 = self.weap[a1]
                e2 = g1['stats']
                q = math.radians(e2.get('cone_deg', 30.0)) * 0.5
                if e0 <= e2.get('range', 45.0) * 0.97 and abs(adiff(b0, bearing(p2, q2, k2[0], k2[1]))) < q * 0.9:
                    v1 = self.P.lightning_energy_reserve
                    if j1.get('energy', 0) > v1 or (v0 and h2.hp < 200):
                        if v0 or w0 or e0 < 30:
                            k0[a1] = True
            if k1 is not None:
                m1 = self.weap[k1]
                if m1.get('cooldown', 0) <= 0.05 and e0 >= self.P.mortar_min_range:
                    a2 = math.hypot(m2[0], m2[1]) < self.P.mortar_max_target_speed or not v0
                    if (a2 or not b1) and (f is None or not k0[n0 if n0 is not None else 0]):
                        e2 = m1['stats']
                        l2 = (p2, q2, s2 + (self.mc.get('height', 9.0) + 0.5) * self.scale)
                        l1 = None
                        for s0 in (False, True):
                            (p1, q1, s1) = gn.predict(k2, m2, 1.5, self.A, damp=0.6)
                            b2 = gn.mortar_solution(l2, (p1, q1, s1 + 0.5), e2.get('speed', 72.0), e2.get('gravity', 22.0), 3.0 * self.scale, s0, self.mc.get('min_pitch', -0.7), self.mc.get('max_pitch', 1.3))
                            if b2 is None:
                                continue
                            (r2, n1, g2) = b2
                            (p1, q1, s1) = gn.predict(k2, m2, g2, self.A, damp=0.6)
                            b2 = gn.mortar_solution(l2, (p1, q1, s1 + 0.5), e2.get('speed', 72.0), e2.get('gravity', 22.0), 3.0 * self.scale, s0, self.mc.get('min_pitch', -0.7), self.mc.get('max_pitch', 1.3))
                            if b2 is not None and (s0 or b1 or True):
                                l1 = b2
                                break
                        if l1 is not None and (not x0) and (not any(k0)):
                            f = (l1[0], l1[1])
                            if abs(adiff(b0, l1[0])) < 0.03 and abs(a0 - l1[1]) < 0.03:
                                k0[k1] = True
        else:
            if y0 is not None:
                f1 = self.weap[y0]
                if (f1.get('charge') or 0) > 0:
                    if self.laser_hold_start is None:
                        self.laser_hold_start = f2 - (f1.get('charge') or 0)
                    if f2 - self.laser_hold_start < f1['stats'].get('charge', 0.75) + 1.5 and intent.target is not None:
                        k0[y0] = True
                    else:
                        self.laser_hold_start = None
            f = self.scan_aim(intent)
            if n0 is not None:
                o0 = self.weap[n0]
                i1 = o0['stats'].get('mag', 12)
                if (o0.get('ammo', i1) or 0) < i1 * self.sq('reload_below_frac', 0.6) and (o0.get('reload', 0) or 0) <= 0 and (not self.enemies):
                    u1 = True
        if f is None:
            f = (b0, 0.0)
        o1 = clamp(f[1], self.mc.get('min_pitch', -0.7), self.mc.get('max_pitch', 1.3))
        if any(k0):
            self.last_fire_t = f2
        return ((wrap(f[0]), o1), k0, u1)

    def scan_aim(self, intent: Intent):
        (o0, p0) = (self.pos[0], self.pos[1])
        m0 = self.t
        if intent.target is not None:
            n0 = intent.target
            return (bearing(o0, p0, n0.pos[0], n0.pos[1]), 0.0)
        if intent.face is not None:
            return (intent.face + 0.35 * math.sin(2 * math.pi * m0 / 1.6), 0.0)
        g0 = self.W.threat_bearing(4.0)
        j = 0.9
        if g0 is None:
            l = None
            for n0 in self.W.mechs.values():
                f = m0 - n0.last_seen
                if f < 12:
                    b0 = d2(o0, p0, n0.pos[0], n0.pos[1])
                    if l is None or b0 < l[0]:
                        l = (b0, n0)
            if l is not None:
                g0 = bearing(o0, p0, l[1].pos[0], l[1].pos[1])
                j = 0.6
        if g0 is None:
            (a0, l0) = self.zn
            i0 = self.last_move
            if math.hypot(i0[0], i0[1]) > 0.3:
                j0 = math.atan2(i0[1], i0[0])
            else:
                j0 = None
            e0 = d2(o0, p0, a0[0], a0[1])
            if e0 > 30:
                q = bearing(o0, p0, a0[0], a0[1])
                g0 = q if j0 is None else math.atan2(math.sin(q) + math.sin(j0), math.cos(q) + math.cos(j0))
            elif j0 is not None:
                g0 = j0
            else:
                g0 = self.me['aim'][0]
            j = 1.25 if intent.mode not in ('explore', 'loot') else math.pi
        k0 = self.P.scan_period_s
        return (wrap(g0 + j * math.sin(2 * math.pi * m0 / k0 + self.slot)), 0.0)

    def loot_actions(self, intent: Intent, action: dict):
        (b0, e0, g0) = self.pos
        f = None
        for q in self.obs.get('pickups') or []:
            if d2(b0, e0, q['pos'][0], q['pos'][1]) > self.loot_reach - 0.3 or abs(q['pos'][2] - g0) > 3.8:
                continue
            (l, a0) = self.loadout_gain(q.get('kind'))
            if l <= 0 or (a0 is not None and l < self.P.swap_margin):
                continue
            if self.blacklist.get(q['id'], -1) > self.t:
                continue
            if f is None or l > f[0]:
                f = (l, q, a0)
        if f is not None:
            (l, q, a0) = f
            action['take'] = q['id']
            if a0 is not None:
                action['drop'] = a0
            self.blacklist[q['id']] = self.t + 1.0
        for j in self.obs.get('events') or []:
            if j.get('type') == 'take_failed':
                self.blacklist[j.get('id')] = self.t + (20.0 if j.get('reason') in ('gone', 'have') else 2.0)

    def pick_upgrade(self, offer: dict):
        q = offer.get('choices') or []
        if not q:
            return None
        have = self.me.get('upgrades') or []
        e0 = self.P.upgrade_value
        if getattr(self, 'squad', False) and self.P.get('squad_dmg_upgrades'):
            e0 = dict(e0, **self.P.get('squad_upgrade_value', {}))
        (f, j) = (0, -1000000000.0)
        for (a0, l) in enumerate(q):
            cid = l.get('id', '')
            g0 = e0.get(cid, 2.0)
            b0 = sum((1 for u in have if u == cid))
            g0 *= 0.85 ** b0
            if cid.startswith('laser') and 'laser' not in self.kinds:
                g0 *= 0.3
            if cid.startswith('gun') and 'gun' not in self.kinds:
                g0 *= 0.3
            if cid.startswith('lightning') and 'lightning' not in self.kinds:
                g0 *= 0.3
            if cid.startswith('mortar') and 'mortar' not in self.kinds:
                g0 *= 0.3
            if g0 > j:
                (j, f) = (g0, a0)
        return f
