"""Mech Royale bot (mechfight/1). Survival first: stay ahead of the zone, fight from cover,
face attackers, retreat to regenerate, farm cubes and loot when it is safe.

Standard library only. Reads JSON lines on stdin, writes actions on stdout, logs to stderr.
"""
from __future__ import annotations

import json
import math
import os
import queue
import random
import sys
import threading
import time
import traceback

try:
    from geo import World
except Exception:  # pragma: no cover
    World = None

PI = math.pi
TAU = 2 * math.pi

UPGRADE_PRIORITY = [
    "lightning_range", "laser_damage", "gun_mag", "armor", "plating", "gun_rate", "gun_damage",
    "laser_charge", "lightning_efficiency", "nanites", "gyros", "servos", "capacitor",
    "reactor", "jets", "gun_velocity", "sensors", "lightning_chain", "mortar_reload",
    "mortar_velocity", "mortar_splash", "laser_pierce",
]
UPGRADE_PRIORITY_B = [   # what the top teams' bots pick most often (public showdown stream, Sat 9 AM)
    "plating", "armor", "nanites", "gun_rate", "gun_damage", "laser_damage", "laser_charge", "gun_mag",
    "servos", "lightning_range", "lightning_efficiency", "reactor", "gyros", "capacitor", "gun_velocity",
    "jets", "sensors", "lightning_chain", "mortar_reload", "mortar_velocity", "mortar_splash", "laser_pierce",
]
WEAPON_VALUE = {"laser": 3.0, "lightning": 2.6, "mortar": 1.5, "gun": 1.0}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def wrap(a):
    return (a + PI) % TAU - PI


def dist2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def norm2(x, y):
    d = math.hypot(x, y)
    if d < 1e-9:
        return 0.0, 0.0
    return x / d, y / d


class Bot:
    name = "hardy-mech"
    # tunables (compared in tools/sim.py)
    RETREAT_HP = 0.0       # retreat below this HP fraction (0: never; running away took the most damage and scored worse)
    OUTNUM_HP = 0.0        # with 2+ threats close, retreat below this (0: never)
    RECOVER_HP = 0.6       # out of combat below this: hide and heal
    RECOVER_TO = 0.92      # ...until back above this
    ENGAGE_RANGE = 150.0   # don't open fire beyond this unless they hit us first
    RELOAD_COVER = 1.0     # duck toward cover while the gun reloads and nothing else can shoot
    OUTNUM_RANGE = 210.0   # threats within this range (with line of sight) count toward "outnumbered"
    FARM_NPC_RANGE = 160.0   # shoot NPC drones within this range when no mech is in sight
    FARM_CRATE_RANGE = 150.0
    RECOVER_CUBE_R = 60.0    # while recovering, still walk to cubes this close
    FARM_APPROACH = 1.0      # walk toward a drone/crate we are farming
    ZAP_RESUME = 7.0         # after draining energy with lightning, wait until this much is back (burst firing tested no better)
    DROP_GO = 0.0            # walk to supply drops (3 cubes and a full heal) even during a fight
    FINISH_W = 0.12          # target choice: weight on low enemy HP (higher: finish wounded mechs first)
    FINISH_CHASE = 0.25      # chase an enemy out of sight when its HP fraction is below this
    AIMBOT = 0.0             # smarter lead (acceleration, strafe-aware) and no gun shots with a poor hit chance
    AIM_HITP = 0.5           # with AIMBOT: minimum body-width / uncertainty ratio for a gun shot
    DODGE = 0.0              # sidestep incoming bullets and laser lines (reads obs.projectiles and enemy aim)
    GF_GUN = 1               # 1: scale the gun lead by a table measured from recorded house bots (2: table from top-16 bots; used automatically in free-for-all)
    CAMP = 0                 # 1: conservative free-for-all bot: treat every match as a free-for-all, never start a fight, hide and stay quiet
    CAMP_LOOT_S = 35.0       # with CAMP: seconds at the start when it may still break crates and grab a weapon
    CAMP_END = 3             # with CAMP: start fighting normally once this few mechs are left
    UPG_SMART = 0            # 1: never spend an upgrade on a weapon we are not holding; laser upgrades first when we hold one
    LASER_SEEK = 0           # 1: keep going for a laser even while holding lightning (lightning-first scored 3.81)
    HULL = 0                 # 1: fight from behind low cover: muzzle (7 m up) clears it, our body center is hidden from the enemy's gun
    WINNABLE = 0.0           # >0: back off from a mech unless our strength is this many times theirs (1.2 = no 50/50 fights)
    FFA_PICK_STRONG = 0      # 1: in a free-for-all, shoot the stronger of two mechs that are busy with each other
    PROBE_HIDE_AFTER = -1    # probe bots: after this many kills, stop shooting mechs and only hide (0: hide from the start; -1: off)
    LIGHT_FIRST = 0          # 1: prefer lightning over laser (cannot miss, about 240 damage/s inside 45 m, shorts lasers)
    LIGHT_RUSH = 0           # 1: with lightning in hand, close straight in on the target instead of circling at range
    SEED_SALT = 0            # added to the random seed: same bot, different dice (measures luck)
    UPG_B = 0                # 1: upgrade order copied from the top teams (plating, armor, nanites, gun rate first)
    LASER_PREF_LO = 45.0     # preferred fighting range when holding a laser (top teams: about 70 to 95 m)
    LASER_PREF_HI = 140.0
    VMAP = 0.0               # fight positioning by a value map (cover from other threats, line of fire, range, walls)
    LASER_TOL = 1.5          # release the laser only when the beam would pass within this many meters of the target's center line
    GUN_TOL = 2.2            # fire the gun when the aim error is within this many meters (times target size)
    PRECHARGE = 1.0          # start charging the laser while the target is briefly behind cover
    FAR_IGNORE = 130.0       # before fights start, keep farming and ignore mechs farther than this that are not shooting us (0: off)
    LOADOUT_LL = 0.0         # aim for laser + lightning and drop the gun (neither reloads; lightning cannot miss)
    RETREAT_N = 99           # retreat when this many threats are close (99: never)
    CUBE_MAGNET = 60.0       # during a fight, walk over cubes this close (kills drop them; each adds HP and damage)
    AIM_LATENCY = 0.06       # seconds between an observation and the shot it causes
    ARM_FIRST = 1.0          # without a laser or lightning, walk to a known one even during a fight
    ARM_RANGE = 300.0
    ARM_LASER_BONUS = 40.0   # prefer a laser over lightning unless it is this much farther (top bots' lasers almost never miss)
    REAR_BONUS = 80.0        # prefer enemies facing away from us (rear hits do x1.5)
    PEEK_HIDE_AMMO = 0       # duck back early when the gun is down to this many rounds (0: only during reloads; early hiding tested worse)
    BURST_HIDE = 9.0         # duck back after losing this fraction of max HP in 1.2 s (9: off; tested worse)
    CHASE = 0.0              # chase enemies out of sight (0: hold and let them come)
    AMBUSH = 1.0             # when hunters were seen recently, hold a covered spot instead of roaming
    FFA_DETECT = 1.0         # switch to free-for-all play when others die without our kills
    FFA_ENGAGE = 80.0        # in a free-for-all, leave non-threatening mechs beyond this alone
    GUN_PREF_LO = 35.0       # preferred fighting range with the gun
    GUN_PREF_HI = 95.0
    PICKUP_RANGE = 200.0    # walk to visible weapon pickups this close
    EXPLORE = 1.0          # with no second weapon and nobody around, roam the safe area looking for one

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.me_id = hello.get("slot")
        random.seed(hello.get("seed", 0) + self.SEED_SALT)
        self.world = None
        mdir = (hello.get("map") or {}).get("dir") or "/arena"
        for cand in (mdir, "/arena", os.path.join(os.path.dirname(os.path.abspath(__file__)), "arena")):
            try:
                if World and os.path.exists(os.path.join(cand, "v2", "world.json")):
                    self.world = World(cand)
                    log(f"world from {cand} in {self.world.build_s:.2f}s nav {self.world.gw}x{self.world.gh}")
                    break
            except Exception:
                log("world load failed:", traceback.format_exc())
        w = self.cfg["weapons"]
        self.gun_cfg = w["gun"]
        self.mortar_cfg = w["mortar"]
        self.laser_cfg = w["laser"]
        self.light_cfg = w["lightning"]
        self.pickup_r = self.cfg["loot"]["pickup_radius"]
        self.cube_r = self.cfg["cubes"]["pickup_radius"]
        self.size_per_cube = self.cfg["cubes"].get("size_per_cube", 0.05)
        # memory
        self.seen = {}          # id -> dict(pos, vel, t, hp, max_hp, weapons, cubes)
        self.dmg = []           # (t, amount, bearing, by)
        self.attackers = {}     # id -> last t it hit us
        self.path = []
        self.path_goal = None
        self.path_t = -99.0
        self.path_i = 0
        self.hist = []          # (t, x, y)
        self.stuck_until = -1.0
        self.strafe = 1.0
        self.strafe_t = 0.0
        self.vh = {}            # target id -> recent (t, vx, vy)
        self.pv = {}            # target id -> (t, vx, vy) at the previous observation
        self.dodges = 0
        self.mode = "start"
        self.cover = None
        self.cover_t = -99.0
        self.last_take = {}
        self.bad_pickups = {}
        self.climb = False
        self.loot_goal = None
        self.t = 0.0
        self.prev_hp = None
        self.focus = None
        self.kills = 0
        self._vm = None
        self._vm_t = -99.0
        self._hs = None
        self._hs_t = -99.0
        self.zap_rest = False
        self.kill_sites = []    # (t, x, y, z) where our kills dropped cubes
        self.known = {}         # weapon pickups seen: id -> (kind, pos)
        self.start_yaw = None
        self.hide_until = -99.0
        self.hide_end = -99.0
        self.peek_pt = None
        self.peek_t = -99.0
        self.peek_side = 1.0
        self.last_hp = {}
        self.ffa_votes = 0
        self.ffa = False        # set once mechs die that we did not kill (qualifier/final are free-for-alls)
        self.look_t = -99.0
        self.look_yaw = None

    # ------------------------------------------------------------------ helpers
    def scale(self, cubes):
        return 1.0 + self.size_per_cube * (cubes or 0)

    def zone_now(self, z, t):
        cx, cy = z["center"]
        r = z["radius"]
        nx_, ny_ = z["next_center"]
        nr = z["next_radius"]
        s0, s1 = z["shrink_start"], z["shrink_end"]
        if t <= s0 or s1 <= s0:
            return cx, cy, r
        f = min(1.0, (t - s0) / (s1 - s0))
        return cx + (nx_ - cx) * f, cy + (ny_ - cy) * f, r + (nr - r) * f

    def safe_point(self, z, x, y):
        """Point inside the next circle closest to us, with margin."""
        ncx, ncy = z["next_center"]
        nr = z["next_radius"]
        margin = max(10.0, 0.18 * nr)
        rr = max(0.0, nr - margin)
        d = math.hypot(x - ncx, y - ncy)
        if d <= rr:
            return x, y, True
        ux, uy = norm2(x - ncx, y - ncy)
        return ncx + ux * rr, ncy + uy * rr, False

    def walk_ok(self, x0, y0, h0, x1, y1, max_up=1.6):
        w = self.world
        if w is None:
            return True
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(d / 2.0))
        for k in range(1, n + 1):
            f = k / n
            x = x0 + (x1 - x0) * f
            y = y0 + (y1 - y0) * f
            if not w.inside(x, y, 8.0):
                return False
            gi, gj = w.cell(x, y)
            if w.nh[gj * w.gw + gi] - h0 > max_up:
                return False
        return True

    def los(self, a, b):
        if self.world is None:
            return True
        return self.world.los(a, b)

    # ------------------------------------------------------------------ main
    def act(self, obs):
        t0 = time.perf_counter()
        self.t = t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        vx, vy = me["vel"][0], me["vel"][1]
        hp, mhp = me["hp"], me["max_hp"]
        hpf = hp / max(1.0, mhp)
        energy = me["energy"]
        st = me.get("stats", {})
        myscale = self.scale(me.get("cubes", 0))
        eye = z + st.get("eye_height", 8 * myscale)
        muzzle_h = st.get("muzzle_height", 7 * myscale)
        act = {"tick": obs["tick"]}
        dbg = []

        # ---- upgrades
        off = obs.get("offer")
        if off and off.get("choices"):
            best, bi = 1e9, 0
            for i, c in enumerate(off["choices"]):
                try:
                    r = (UPGRADE_PRIORITY_B if self.UPG_B else UPGRADE_PRIORITY).index(c["id"])
                except ValueError:
                    r = 100
                if self.UPG_SMART:
                    held = {wp["kind"] for wp in obs["self"]["weapons"] if wp}
                    cid = c["id"]
                    for wk in ("laser", "lightning", "mortar"):
                        if cid.startswith(wk + "_"):
                            if wk not in held:
                                r += 60
                            elif wk == "laser":
                                r = -2 if cid == "laser_damage" else -1
                if r < best:
                    best, bi = r, i
            act["pick"] = bi

        # ---- events
        for ev in obs.get("events", []):
            et = ev.get("type")
            if et == "damaged":
                self.dmg.append((t, ev.get("amount", 0), ev.get("bearing"), ev.get("by"), ev.get("weapon")))
                if ev.get("by") is not None:
                    self.attackers[ev["by"]] = t
            elif et == "kill":
                self.kills += 1
                kp = self.seen.get(ev.get("target"))
                if kp:
                    self.kill_sites.append((t, kp["pos"][0], kp["pos"][1], kp["pos"][2]))
            elif et == "take_failed":
                self.bad_pickups[ev.get("id")] = t
        self.dmg = [d for d in self.dmg if t - d[0] < 6.0]
        recent_dmg = sum(d[1] for d in self.dmg if t - d[0] < 2.0 and d[4] != "zone")

        # ---- remember weapon pickups (they never despawn; drop ones we can see are gone)
        if self.start_yaw is None:
            self.start_yaw = me["aim"][0]
        vis_ids = set()
        for p in obs.get("pickups", []):
            self.known[p["id"]] = (p["kind"], p["pos"])
            vis_ids.add(p["id"])
        for pid in list(self.known):
            if pid in vis_ids:
                continue
            kp = self.known[pid][1]
            if math.hypot(kp[0] - x, kp[1] - y) < 40 and self.los((x, y, z + 8), (kp[0], kp[1], kp[2] + 1.0)):
                del self.known[pid]
            elif pid in self.bad_pickups:
                del self.known[pid]

        # ---- world memory
        enemies = []
        npcs = []
        for m in obs.get("mechs", []):
            if m["kind"] == "mech":
                if m["id"] == me["id"]:
                    continue
                self.seen[m["id"]] = {"pos": m["pos"], "vel": m["vel"], "t": t, "hp": m.get("hp", 1400),
                                      "max_hp": m.get("max_hp", 1400), "weapons": m.get("weapons", []),
                                      "cubes": m.get("cubes", 0), "aim": m.get("aim"), "charging": m.get("charging")}
                enemies.append(m)
            else:
                npcs.append(m)
        for k in list(self.seen):
            if t - self.seen[k]["t"] > 20:
                del self.seen[k]

        # ---- zone
        zone = obs["zone"]
        zcx, zcy, zr = self.zone_now(zone, t)
        zr_soon_x, zr_soon_y, zr_soon = self.zone_now(zone, t + 6.0)
        d_zone_now = math.hypot(x - zcx, y - zcy)
        d_soon = math.hypot(x - zr_soon_x, y - zr_soon_y)
        sx, sy, inside_next = self.safe_point(zone, x, y)
        time_left = zone["shrink_end"] - t
        dist_to_safe = math.hypot(sx - x, sy - y)
        travel = dist_to_safe * 1.35 / max(6.0, st.get("walk_speed", 11.7))
        zone_urgent = (not me["in_zone"]) or d_soon > zr_soon - 6.0 or (not inside_next and travel > time_left - 12.0)
        late_game = zone["next_radius"] <= 70

        # ---- weapons
        weapons = me["weapons"]
        gun = laser = light = mortar = None
        slots = {}
        for i, wp in enumerate(weapons):
            if wp is None:
                continue
            slots[wp["kind"]] = i
            if wp["kind"] == "gun":
                gun = wp
            elif wp["kind"] == "laser":
                laser = wp
            elif wp["kind"] == "lightning":
                light = wp
            elif wp["kind"] == "mortar":
                mortar = wp
        fire = [False, False]

        # ---- threat assessment
        my_pos3 = (x, y, z)
        muzzle = (x, y, z + muzzle_h)
        vis = []
        for m in enemies:
            p = m["pos"]
            sc = self.scale(m.get("cubes", 0))
            c = (p[0], p[1], p[2] + 4.5 * sc)
            d = math.dist(muzzle, c)
            clear = self.los(muzzle, c) or self.los(muzzle, (p[0], p[1], p[2] + 8 * sc))
            vis.append((m, d, clear, sc))
        threats_n = len(vis)
        close_threats = [v for v in vis if v[1] < self.OUTNUM_RANGE and (v[2] or v[1] < 60)]

        # target choice
        # free-for-all detection: in practice the squad never kills its own
        # (the squad never hurts its own: a mech losing a gun-sized chunk of HP that we did not cause means FFA)
        vis_all_near = any(v_[1] < 80 for v_ in vis)
        if self.CAMP:
            self.ffa = True
        if not self.ffa and self.FFA_DETECT:
            my_hits = {}
            for ev in obs.get("events", []):
                if ev.get("type") == "hit":
                    my_hits[ev.get("target")] = my_hits.get(ev.get("target"), 0) + ev.get("amount", 0)
            cur = {}
            for m_ in enemies:
                cur[m_["id"]] = m_.get("hp", 0)
                prev = self.last_hp.get(m_["id"])
                if prev is not None and prev[1] > t - 0.25:
                    drop = prev[0] - m_.get("hp", 0) - my_hits.get(m_["id"], 0) * 1.6
                    if drop >= 40:
                        self.ffa_votes += 1
            self.last_hp = {k: (v, t) for k, v in cur.items()}
            unexplained = 16 - obs.get("alive", 16) - self.kills
            if t < 120 and ((self.ffa_votes >= 2 and unexplained >= 1) or unexplained >= 3):
                self.ffa = True
                log(f"free-for-all detected at t={t:.1f}")
        if self.ffa:
            # survival first: only fight what threatens us, is close, or is nearly dead
            keep = []
            for v_ in vis:
                m_, d_, clear_, sc_ = v_
                hit_us = m_["id"] in self.attackers and t - self.attackers[m_["id"]] < 6
                weak = m_.get("hp", 1400) / max(1, m_.get("max_hp", 1400)) < 0.3 and d_ < 150 and hpf > 0.5
                if self.CAMP and obs.get("alive", 16) > self.CAMP_END:
                    # never start a fight: answer only what is shooting us, charging at us, or right on top of us
                    ea_ = m_.get("aim")
                    onus = False
                    if ea_:
                        onus = abs(wrap(ea_[0] - math.atan2(y - m_["pos"][1], x - m_["pos"][0]))) < 0.2
                    if hit_us or (d_ < 30 and onus) or (m_.get("charging") and onus and d_ < 120) or d_ < 14:
                        keep.append(v_)
                    continue
                if hit_us or d_ < self.FFA_ENGAGE or weak:
                    keep.append(v_)
            vis = keep
        elif self.FAR_IGNORE:
            # far mechs that are not shooting us can wait: keep collecting weapons, cubes and crates
            vis = [v_ for v_ in vis if v_[1] < self.FAR_IGNORE or (
                v_[0]["id"] in self.attackers and t - self.attackers[v_[0]["id"]] < 5)]
        target = None
        best = 1e18
        for m, d, clear, sc in vis:
            mhp_t = m.get("hp", 1400)
            s = d * 1.0 + mhp_t * (self.FINISH_W * (3.0 if self.ffa else 1.0))
            if not clear:
                s += 250
            if m["id"] in self.attackers and t - self.attackers[m["id"]] < 3:
                s -= 60
            if self.focus == m["id"]:
                s -= 40
            ea = m.get("aim")
            if ea:
                b_us = math.atan2(y - m["pos"][1], x - m["pos"][0])
                off = abs(wrap(ea[0] - b_us))
                if off > PI / 2:
                    s -= self.REAR_BONUS          # its back is to us: x1.5 damage and it is busy
                elif off > 0.35:
                    s -= self.REAR_BONUS * 0.35   # not aimed at us yet
            if self.FFA_PICK_STRONG and self.ffa and ea:
                b_us2 = math.atan2(y - m["pos"][1], x - m["pos"][0])
                if abs(wrap(ea[0] - b_us2)) > 0.5:      # busy with someone else
                    s -= 0.05 * (m.get("hp", 1400) + 56.0 * m.get("cubes", 0))
            if s < best:
                best, target = s, (m, d, clear, sc)
        if target:
            self.focus = target[0]["id"]

        # ---- peek-and-hide bookkeeping
        burst = sum(d_[1] for d_ in self.dmg if t - d_[0] < 1.2 and d_[4] != "zone")
        if target and burst > self.BURST_HIDE * mhp and self.hide_until < t - 2.0:
            self.hide_until = t + 1.6
        if target and target[2]:
            self.peek_pt = (x, y)
            self.peek_t = t

        # ---- decide movement goal
        move_goal = None      # (x, y) to path to
        move_vec = None       # direct steering vector
        boost = False
        want_jump = False
        aim_yaw = None
        aim_pitch = 0.0
        label = ""

        # retreat logic
        enemy_hpf = (target[0].get("hp", 1400) / max(1, target[0].get("max_hp", 1400))) if target else 1.0
        outnumbered = len(close_threats) >= 2
        retreat = False
        if target:
            if hpf < (0.45 if self.ffa else self.RETREAT_HP) and enemy_hpf > 0.2:
                retreat = True
            if outnumbered and hpf < (0.9 if self.ffa else self.OUTNUM_HP):
                retreat = True
            if len(close_threats) >= (3 if self.ffa else self.RETREAT_N):
                retreat = True
        elif recent_dmg > 0 and hpf < 0.5:
            retreat = True
        if self.WINNABLE and target and enemy_hpf > 0.25:
            tm = target[0]
            def _pw(hp_, cubes_, kinds_):
                wf = 1.0 + (0.45 if "laser" in kinds_ else 0.0) + (0.45 if "lightning" in kinds_ else 0.0) + (0.15 if "mortar" in kinds_ else 0.0)
                return hp_ * (1.0 + 0.04 * min(10, cubes_)) * wf
            mine = _pw(hp, me.get("cubes", 0), [wp["kind"] for wp in weapons if wp])
            theirs = _pw(tm.get("hp", 1400), tm.get("cubes", 0), tm.get("weapons") or ["gun"])
            if mine < self.WINNABLE * theirs:
                retreat = True
        probe_hide = self.PROBE_HIDE_AFTER >= 0 and self.kills >= self.PROBE_HIDE_AFTER
        if probe_hide and target:
            retreat = True

        # ---------------------------------------------------------------- combat aim / fire
        if target:
            m, d, clear, sc = target
            p = m["pos"]
            v = m["vel"]
            c = (p[0], p[1], p[2] + 4.5 * sc)
            # choose aim mode
            use_mortar = False
            if mortar and mortar["cooldown"] <= 0.05 and 45 < d < 230:
                gun_busy = gun is None or gun.get("ammo", 1) == 0 or not clear
                if gun_busy or d > 140 or (laser is None and light is None):
                    use_mortar = True
            if use_mortar:
                sol = self.mortar_solution(muzzle_mortar=(x, y, z + 9.5 * myscale), tgt=c, vel=v,
                                           cfg=mortar.get("stats", self.mortar_cfg), high=not clear)
                if sol:
                    aim_yaw, aim_pitch, impact = sol
                    if dist2(impact, (x, y)) > self.mortar_cfg["splash_radius"] * 2.2:
                        yerr = abs(wrap(me["aim"][0] - aim_yaw))
                        perr = abs(me["aim"][1] - aim_pitch)
                        if yerr * d < 4.0 and perr < 0.04:
                            fire[slots["mortar"]] = True
                else:
                    use_mortar = False
            if not use_mortar:
                speed = gun["stats"]["speed"] if gun else 252.0
                if laser and gun is None:
                    speed = 1e6
                tx, ty, tz = c
                tau = d / speed
                steady = 0.0
                if self.GF_GUN:
                    # lead fraction measured from recordings: targets that are slowing sideways are about to reverse
                    ldx, ldy = c[0] - x, c[1] - y
                    ld = math.hypot(ldx, ldy) or 1.0
                    nx_, ny_ = -ldy / ld, ldx / ld
                    vl = v[0] * nx_ + v[1] * ny_
                    vr = v[0] * (ldx / ld) + v[1] * (ldy / ld)
                    pv = self.pv.get(m["id"])
                    acc = 1
                    if pv is not None and t - pv[0] < 0.25:
                        dv = abs(vl) - abs(pv[1] * nx_ + pv[2] * ny_)
                        acc = 2 if dv > 0.8 else 0 if dv < -0.8 else 1
                    self.pv[m["id"]] = (t, v[0], v[1])
                    sp_ = abs(vl)
                    top = self.GF_GUN >= 2 or self.ffa
                    if sp_ < 3.0:
                        f_ = 0.0 if acc == 0 else 1.0
                    elif top:
                        f_ = (0.2 if sp_ < 8 else 0.6) if acc == 0 else 0.65 if acc == 1 else 0.8
                    else:
                        f_ = (0.4 if sp_ < 8 else 0.6) if acc == 0 else 0.85 if acc == 1 else 0.95
                    v = (vr * (ldx / ld) + f_ * vl * nx_, vr * (ldy / ld) + f_ * vl * ny_, v[2])
                if self.AIMBOT:
                    h = self.vh.setdefault(m["id"], [])
                    if not h or h[-1][0] < t:
                        h.append((t, v[0], v[1]))
                    del h[:-6]
                    if len(h) >= 2 and t - h[-2][0] < 0.25:
                        dtv = max(0.05, t - h[-2][0])
                        ax, ay = (v[0] - h[-2][1]) / dtv, (v[1] - h[-2][2]) / dtv
                        am = math.hypot(ax, ay)
                        if am > 58.5:
                            ax, ay = ax * 58.5 / am, ay * 58.5 / am
                        old_ = h[0] if t - h[0][0] < 0.65 else h[-2]
                        flipped = v[0] * old_[1] + v[1] * old_[2] < -4.0
                        sp_now = math.hypot(v[0], v[1])
                        if flipped:
                            # strafing back and forth: aim nearer the middle of the dance
                            v = (v[0] * 0.55, v[1] * 0.55, v[2])
                        else:
                            ta = min(tau + self.AIM_LATENCY, 0.3)
                            nvx, nvy = v[0] + ax * ta * 0.5, v[1] + ay * ta * 0.5
                            nm = math.hypot(nvx, nvy)
                            cap = max(sp_now, 11.7)
                            if nm > cap:
                                nvx, nvy = nvx * cap / nm, nvy * cap / nm
                            v = (nvx, nvy, v[2])
                            if am < 12.0 and len(h) >= 4:
                                steady = 1.0
                # the shot leaves 1-2 ticks after this observation: predict both mechs that far ahead
                LAT = self.AIM_LATENCY
                mzx, mzy = x + vx * LAT, y + vy * LAT
                for _ in range(3):
                    tx = c[0] + v[0] * (tau + LAT)
                    ty = c[1] + v[1] * (tau + LAT)
                    tz = c[2] + max(-10.0, min(10.0, v[2])) * tau * 0.5
                    tau = math.dist((mzx, mzy, muzzle[2]), (tx, ty, tz)) / speed
                tx -= mzx - x
                ty -= mzy - y
                aim_yaw = math.atan2(ty - y, tx - x)
                aim_pitch = math.atan2(tz - muzzle[2], math.hypot(tx - x, ty - y))
                yerr = abs(wrap(me["aim"][0] - aim_yaw))
                perr = abs(me["aim"][1] - aim_pitch)
                miss = math.hypot(yerr, perr) * d
                tol = self.GUN_TOL * sc + 0.6
                engage = d < self.ENGAGE_RANGE or (m["id"] in self.attackers and t - self.attackers[m["id"]] < 4)
                hit_ok = True
                if self.AIMBOT and gun:
                    spread = math.radians(gun["stats"].get("spread_deg", 0.6)) * d
                    maneuver = 0.25 * 58.5 * tau * tau * (0.4 if steady else 1.0)
                    hit_ok = (2.5 * sc) / (spread + maneuver + 1e-6) >= self.AIM_HITP or d < 60
                if gun and clear and engage and hit_ok and miss < tol and d < gun["stats"]["range"] * 0.98:
                    if gun.get("reload", 0) <= 0 and gun.get("ammo", 1) > 0:
                        fire[slots["gun"]] = True
                if light and clear and d < light["stats"]["range"] * 0.93:
                    cone = math.radians(light["stats"].get("cone_deg", 30)) * 0.4
                    # fire in bursts: energy only regenerates after a pause, so drain it, then rest until half full
                    if energy < 8:
                        self.zap_rest = True
                    elif energy > self.ZAP_RESUME:
                        self.zap_rest = False
                    if yerr < cone and not self.zap_rest and light["cooldown"] <= 0.05:
                        fire[slots["lightning"]] = True
                if laser:
                    ch = laser.get("charge", 0)
                    full = laser["stats"].get("charge", 0.75)
                    # direct aim for a hitscan beam (small lead for the decision delay)
                    lyaw = math.atan2(c[1] + v[1] * 0.1 - y, c[0] + v[0] * 0.1 - x)
                    lpitch = math.atan2(c[2] - muzzle[2], math.hypot(c[0] - x, c[1] - y))
                    lmiss = math.hypot(wrap(me["aim"][0] - lyaw), me["aim"][1] - lpitch) * d
                    l_ok = abs(wrap(me["aim"][0] - lyaw)) * d < self.LASER_TOL * sc and abs(me["aim"][1] - lpitch) * d < 3.0 * sc
                    if gun is None or ch >= full * 0.55 or (ch > 0 and gun.get("ammo", 1) == 0):
                        aim_yaw, aim_pitch = lyaw, lpitch
                    if ch > 0:
                        if ch >= full * 0.97 and l_ok and clear:
                            fire[slots["laser"]] = False  # release
                        else:
                            fire[slots["laser"]] = True
                    elif laser["cooldown"] <= 0.05 and d < laser["stats"]["range"] * 0.95 and (
                            clear or (self.PRECHARGE and d < 150 and t - self.peek_t < 2.5)):
                        if not retreat or enemy_hpf < 0.3:
                            fire[slots["laser"]] = True
            label = f"T{m['id']} d{d:.0f} {'los' if clear else 'nolos'}"
        else:
            # keep a charged laser from wasting itself
            if laser and laser.get("charge", 0) > 0:
                fire[slots["laser"]] = True     # hold the charge; a target may step back into view

        # ---------------------------------------------------------------- movement decisions
        mortar_threat = self.mortar_dodge(obs, x, y, z)
        laser_threat = None
        for m in enemies:
            if m.get("charging") and m.get("aim"):
                byaw = math.atan2(y - m["pos"][1], x - m["pos"][0])
                if abs(wrap(m["aim"][0] - byaw)) < 0.12:
                    laser_threat = m

        dodge_side = None
        if self.DODGE:
            dodge_side = self.bullet_dodge(obs, x, y, z, vx, vy, myscale, me["stats"].get("walk_speed", 11.7))
            if dodge_side is None and laser_threat is not None:
                # step off the beam line on the side we are already nearer to
                byaw = math.atan2(y - laser_threat["pos"][1], x - laser_threat["pos"][0])
                off = wrap(byaw - laser_threat["aim"][0])
                dodge_side = (1.0 if off >= 0 else -1.0, laser_threat["pos"])
            if dodge_side is not None:
                s_, src = dodge_side
                # express the side as a strafe sign around the current target (or the shooter)
                ref = target[0]["pos"] if target else src
                bx_, by_ = norm2(ref[0] - x, ref[1] - y)
                sx_, sy_ = norm2(src[0] - x, src[1] - y)
                want = (-sy_ * s_, sx_ * s_)
                sign = 1.0 if (want[0] * -by_ + want[1] * bx_) >= 0 else -1.0
                if sign != self.strafe:
                    self.dodges += 1
                self.strafe = sign
                self.strafe_t = max(self.strafe_t, t + 0.3)
                dodge_side = want

        if zone_urgent and not (target and target[1] < 35 and enemy_hpf < 0.25):
            move_goal = (sx, sy)
            boost = energy > 25 or not me["in_zone"]
            self.mode = "zone"
        elif target and retreat:
            self.mode = "retreat"
            cv = self.find_cover(x, y, z, [v_[0] for v_ in vis] or [], zone, zcx, zcy, zr)
            if cv:
                move_goal = cv
            else:
                # run away from the centroid of threats, toward the safe circle
                ax = sum(v_[0]["pos"][0] for v_ in vis) / max(1, len(vis))
                ay = sum(v_[0]["pos"][1] for v_ in vis) / max(1, len(vis))
                ux, uy = norm2(x - ax, y - ay)
                wx, wy = norm2(sx - x, sy - y)
                move_vec = norm2(ux + 0.6 * wx, uy + 0.6 * wy)
            boost = energy > 20
        elif target and self.RELOAD_COVER and enemy_hpf > 0.15 and not (light and target[1] < 40) and target[2] \
                and (self.hide_until > t or (gun and (gun.get("reload", 0) > 0.4 or gun.get("ammo", 12) <= self.PEEK_HIDE_AMMO)
                                              and not (laser and laser["cooldown"] <= 0.05))):
            self.mode = "reload"
            cv = self.find_cover(x, y, z, [v_[0] for v_ in vis], zone, zcx, zcy, zr)
            if cv:
                move_goal = cv
            self.hide_end = t
        elif target:
            self.mode = "fight"
            m, d, clear, sc = target
            pref_lo, pref_hi = self.GUN_PREF_LO, self.GUN_PREF_HI
            if light:
                pref_lo, pref_hi = 18.0, 38.0
            if laser and not light:
                pref_lo, pref_hi = self.LASER_PREF_LO, self.LASER_PREF_HI
            # keep out of reach of an enemy's lightning (45 m arc) unless we out-arc them
            if "lightning" in (m.get("weapons") or []) and not light:
                pref_lo = max(pref_lo, 58.0)
                pref_hi = max(pref_hi, pref_lo + 40)
            if hpf < 0.6:
                pref_lo += 20
                pref_hi += 30
            bx, by = norm2(m["pos"][0] - x, m["pos"][1] - y)
            if t > self.strafe_t:
                self.strafe = -self.strafe if random.random() < 0.7 else self.strafe
                self.strafe_t = t + random.uniform(0.45, 1.2)
            px, py = -by * self.strafe, bx * self.strafe
            radial = 0.0
            if not clear:
                radial = 0.0 if d < 60 else 0.5
            elif d < pref_lo:
                radial = -0.7
            elif d > pref_hi:
                radial = 0.7
            if self.LIGHT_RUSH and light and d > pref_hi + 4 and not zone_urgent:
                radial = 2.2 if clear else 3.0
            mvx, mvy = norm2(px + bx * radial, py + by * radial)
            # flip strafe if the next few meters are blocked
            if not self.walk_ok(x, y, z, x + mvx * 6, y + mvy * 6):
                self.strafe = -self.strafe
                px, py = -px, -py
                mvx, mvy = norm2(px + bx * radial, py + by * radial)
                if not self.walk_ok(x, y, z, x + mvx * 6, y + mvy * 6):
                    mvx, mvy = norm2(bx * radial or -bx, by * radial or -by)
            move_vec = (mvx, mvy)
            if self.VMAP and clear:
                vp = self.value_spot(x, y, z, m, [v_[0] for v_ in vis if v_[0] is not m], (pref_lo + pref_hi) * 0.5, zcx, zcy, zr)
                if vp is not None and math.hypot(vp[0] - x, vp[1] - y) > 4.0:
                    move_vec = None
                    move_goal = vp
            if not clear:
                if self.peek_pt is not None and t - self.peek_t < 8.0 and t - self.hide_end < 6.0:
                    # step back out near where we last had a shot, a few meters to one side
                    self.peek_side = -self.peek_side if t - self.hide_end < 0.15 else self.peek_side
                    bx2, by2 = norm2(m["pos"][0] - self.peek_pt[0], m["pos"][1] - self.peek_pt[1])
                    move_vec = None
                    move_goal = (self.peek_pt[0] - by2 * 3.0 * self.peek_side, self.peek_pt[1] + bx2 * 3.0 * self.peek_side)
                elif self.LIGHT_RUSH and light and d > 38 and hpf > 0.45:
                    move_vec = None
                    move_goal = (m["pos"][0], m["pos"][1])
                elif self.CHASE and d > 60 and hpf > 0.6 and (enemy_hpf < 0.5 or threats_n == 1):
                    move_vec = None
                    move_goal = (m["pos"][0], m["pos"][1])
                elif enemy_hpf < self.FINISH_CHASE and d < 120 and hpf > 0.5:
                    move_vec = None
                    move_goal = (m["pos"][0], m["pos"][1])
                else:
                    move_vec = (0.0, 0.0)     # hold: let it come to us
            if self.HULL and d > 25:
                hsp = self.hull_spot(x, y, z, m, sc, myscale, zcx, zcy, zr)
                if hsp is not None:
                    if math.hypot(hsp[0] - x, hsp[1] - y) > 1.6:
                        move_vec = None
                        move_goal = hsp
                    else:
                        move_vec = (0.0, 0.0)
        else:
            self.mode = "roam"
            # unseen attacker: turn to look, and step to cover
            unseen = [dd for dd in self.dmg if t - dd[0] < 1.5 and dd[2] is not None and dd[4] != "zone"]
            if unseen:
                aim_yaw = unseen[-1][2]
                self.look_yaw = aim_yaw
                self.look_t = t
                if hpf < 0.6:
                    ux, uy = math.cos(aim_yaw), math.sin(aim_yaw)
                    move_vec = norm2(-uy * self.strafe, ux * self.strafe)
            if hpf < self.RECOVER_HP:
                self.recovering = True
            if hpf >= self.RECOVER_TO:
                self.recovering = False
            goal = None
            if getattr(self, "recovering", False):
                self.mode = "recover"
                if move_vec is None:
                    move_goal = None
                    # still grab cubes close by (each heals and adds max HP)
                    near = [c for c in obs.get("cubes", []) if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < self.RECOVER_CUBE_R
                            and c["pos"][2] - z < 1.5]
                    if near:
                        c = min(near, key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                        move_goal = (c["pos"][0], c["pos"][1])
            elif self.CAMP and t > self.CAMP_LOOT_S and obs.get("alive", 16) > self.CAMP_END:
                goal = None                     # quiet: gunfire and crate breaks carry a long way
                near = [c for c in obs.get("cubes", []) if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < 25 and c["pos"][2] - z < 1.5]
                if near and not vis_all_near:
                    c = min(near, key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                    move_goal = (c["pos"][0], c["pos"][1])
            else:
                goal = self.loot_target(obs, x, y, z, hpf, energy, zone, me, gun, npcs, fire, slots)
            if self.mode == "recover":
                pass
            elif goal is not None:
                gx, gy, gaim = goal
                move_goal = (gx, gy)
                if gaim is not None and aim_yaw is None:
                    aim_yaw, aim_pitch = gaim
            elif move_vec is None and self.AMBUSH and hpf > 0.6 and any(
                    t - v["t"] < 12 and math.hypot(v["pos"][0] - x, v["pos"][1] - y) < 250 for v in self.seen.values()):
                # hunters are on the way: hold a covered spot and let them walk into our fire
                self.mode = "ambush"
                spot = self.wall_spot(x, y, z, zone)
                if spot is not None and math.hypot(spot[0] - x, spot[1] - y) < 40:
                    move_goal = spot
                else:
                    move_vec = (0.0, 0.0)
            elif move_vec is None and self.EXPLORE and not self.ffa and not any(
                    wp and wp["kind"] in ("laser", "lightning") for wp in weapons) and not late_game:
                move_goal = self.explore_point(x, y, zone)
                self.mode = "explore"
            elif move_vec is None:
                # go to (or hold) a spot inside the next circle
                spot = self.wall_spot(x, y, z, zone)
                if spot is not None:
                    move_goal = spot
                elif inside_next and late_game is False:
                    move_goal = None
                else:
                    move_goal = (sx, sy)

        # arm first: a fight with only the gun is slow; go get a laser or lightning while shooting
        if self.ARM_FIRST and self.mode in ("fight", "reload") and hpf > 0.35 and (
                (not laser and not light) or (self.LASER_SEEK and not laser) or (self.LOADOUT_LL and gun and not (laser and light))):
            ag = self.arm_goal(x, y, z, energy, zcx, zcy, zr, skip=("laser" if laser else "lightning" if light else None),
                               maxd=(self.ARM_RANGE if (not (laser or light) or (self.LASER_SEEK and not laser)) else 120.0))
            if ag is not None:
                move_vec = None
                move_goal = ag
                boost = energy > 35
                self.mode = "arm"

        # supply drops: 3 cubes and a full heal for 4 s alone in the ring
        if self.DROP_GO and not self.ffa and hpf > 0.25 and self.mode in ("fight", "reload", "arm", "ambush", "roam", "explore", "recover"):
            for dr in obs.get("drops", []):
                if dr.get("holder") not in (None, me["id"]):
                    continue
                ddx, ddy = dr["pos"][0] - x, dr["pos"][1] - y
                dd = math.hypot(ddx, ddy)
                if dd < 260 and dd / 11.0 > dr.get("lands_in", 0) - 8 and dr["pos"][2] - z < 3:
                    if dd > 3.0:
                        move_vec = None
                        move_goal = (dr["pos"][0], dr["pos"][1])
                        boost = energy > 40 and dd > 40
                    else:
                        move_goal = None
                        move_vec = (0.0, 0.0)
                    self.mode = "drop"
                    break

        # cube magnet: kills drop cubes; grab them now, before the next hunter arrives
        if self.CUBE_MAGNET and self.mode in ("fight", "reload", "arm", "ambush") and hpf > 0.3:
            self.kill_sites = [k for k in self.kill_sites if t - k[0] < 25 and not (
                math.hypot(k[1] - x, k[2] - y) < 5)]
            best_c, bd_c = None, self.CUBE_MAGNET
            for cb in obs.get("cubes", []):
                dz = cb["pos"][2] - z
                if dz > 1.5:
                    continue
                dc = math.hypot(cb["pos"][0] - x, cb["pos"][1] - y)
                if dc < bd_c:
                    best_c, bd_c = (cb["pos"][0], cb["pos"][1]), dc
            if best_c is None:
                for k in self.kill_sites:
                    dc = math.hypot(k[1] - x, k[2] - y)
                    if dc < bd_c * 2 and k[3] - z < 1.5:
                        best_c, bd_c = (k[1], k[2]), dc / 2
            if best_c is not None and (target is None or target[1] > 25 or enemy_hpf > 0.2):
                move_vec = None
                move_goal = best_c
                self.mode = "cube"

        # pickups within reach: take them whenever
        take = self.choose_take(obs, x, y, z, weapons, laser)
        if take:
            act.update(take)

        # dodge overlays
        if mortar_threat is not None:
            ux, uy = norm2(x - mortar_threat[0], y - mortar_threat[1])
            move_goal = None
            move_vec = (ux, uy)
            boost = energy > 10
        elif laser_threat is not None:
            bx, by = norm2(laser_threat["pos"][0] - x, laser_threat["pos"][1] - y)
            move_vec = (-by * self.strafe, bx * self.strafe)
            if self.DODGE and dodge_side is not None:
                move_vec = dodge_side
            boost = energy > 10
            want_jump = me["grounded"] and energy > 30 and random.random() < 0.3

        # ---------------------------------------------------------------- path following
        mv = (0.0, 0.0)
        if move_vec is not None:
            mv = move_vec
        elif move_goal is not None:
            mv, jmp, bst = self.follow(move_goal, x, y, z, me, energy, t, vis, zone)
            want_jump = want_jump or jmp
            boost = boost or bst
        # walking somewhere under fire: lean out of the bullets' path without giving up the goal
        if self.DODGE and dodge_side is not None and move_vec is None and math.hypot(*mv) > 0.3:
            mv = norm2(mv[0] + dodge_side[0] * 1.2, mv[1] + dodge_side[1] * 1.2)
        if self.CAMP and self.mode in ("roam", "ambush") and not zone_urgent and obs.get("alive", 16) > self.CAMP_END and (
                move_goal is None or math.hypot(move_goal[0] - x, move_goal[1] - y) < 120):
            walk = me["stats"].get("walk_speed", 11.7)
            q = min(1.0, 4.6 / max(1.0, walk))
            mv = (mv[0] * q, mv[1] * q)
            boost = False
        # stay inside the live circle, whatever else we do
        if d_zone_now > zr - 4.0 and zr > 1:
            ux, uy = norm2(zcx - x, zcy - y)
            mv = norm2(mv[0] + ux * 1.5, mv[1] + uy * 1.5)
        # stuck detection
        self.hist.append((t, x, y))
        self.hist = [h for h in self.hist if t - h[0] < 2.0]
        moving = math.hypot(*mv) > 0.3
        if moving and len(self.hist) > 12 and t > 3:
            hx, hy = self.hist[0][1], self.hist[0][2]
            if math.hypot(x - hx, y - hy) < 2.0 and t - self.hist[0][0] > 1.5:
                self.stuck_until = t + 0.9
                self.path_t = -99
                self.hist = []
        if t < self.stuck_until:
            want_jump = me["grounded"]
            boost = energy > 8
            if t > self.stuck_until - 0.5:
                mv = norm2(mv[0] - mv[1] * self.strafe * 0.8, mv[1] + mv[0] * self.strafe * 0.8)
        if self.climb and not me["grounded"] and energy > 4:
            boost = True

        # ---------------------------------------------------------------- aim when idle
        if aim_yaw is None:
            if self.look_yaw is not None and t - self.look_t < 1.5:
                aim_yaw = self.look_yaw
            else:
                aim_yaw = self.idle_look(obs, x, y, mv, t)
            aim_pitch = 0.0
        act["move"] = [round(mv[0], 3), round(mv[1], 3)]
        act["aim"] = [round(wrap(aim_yaw), 4), round(max(-0.69, min(1.29, aim_pitch)), 4)]
        if probe_hide and target:
            fire = [False] * len(fire)
        act["fire"] = fire
        act["boost"] = bool(boost and moving)
        if want_jump:
            act["jump"] = True
        if gun and gun.get("ammo", 1) < gun["stats"].get("mag", 12) * 0.5 and not target and gun.get("reload", 0) <= 0:
            act["reload"] = True
        if self.mode == "reload" and gun and gun.get("reload", 0) <= 0 and gun.get("ammo", 12) < gun["stats"].get("mag", 12):
            act["reload"] = True
        # one short label for our own replays (debug drawing never affects the match)
        act["debug"] = {"label": f"{self.mode} hp{hp:.0f} e{energy:.0f} {label}"[:78]}
        el = (time.perf_counter() - t0) * 1000
        if el > 25:
            log(f"slow act {el:.1f} ms at t={t:.1f} mode={self.mode}")
        self.prev_hp = hp
        return act

    # ------------------------------------------------------------------ pieces
    def mortar_solution(self, muzzle_mortar, tgt, vel, cfg, high=False):
        v = cfg.get("speed", 72.0)
        g = cfg.get("gravity", 22.0)
        x0, y0, z0 = muzzle_mortar
        tx, ty, tz = tgt[0], tgt[1], tgt[2] - 3.0
        T = 1.5
        for _ in range(3):
            px = tx + vel[0] * T
            py = ty + vel[1] * T
            d = math.hypot(px - x0, py - y0) - 3.0
            dz = tz - z0
            disc = v ** 4 - g * (g * d * d + 2 * dz * v * v)
            if disc < 0 or d < 1:
                return None
            root = math.sqrt(disc)
            th = math.atan2(v * v + root, g * d) if high else math.atan2(v * v - root, g * d)
            T = d / max(1e-3, v * math.cos(th))
        if th > 1.28 or th < -0.69:
            return None
        return math.atan2(py - y0, px - x0), th, (px, py)

    def bullet_dodge(self, obs, x, y, z, vx, vy, sc, walk):
        """Which side to step so the fewest visible bullets hit us. Returns (side, shooter_xy) or None.
        side is +1 / -1 around the bullets' mean direction of travel (left / right of the shooter as we face it)."""
        bl = []
        for pr in obs.get("projectiles", []):
            if pr.get("kind") == "mortar":
                continue
            px, py, pz = pr["pos"]
            bvx, bvy, bvz = pr["vel"]
            sp = math.hypot(bvx, bvy)
            if sp < 1:
                continue
            ux, uy = bvx / sp, bvy / sp
            along = (x - px) * ux + (y - py) * uy
            if along <= 0:
                continue
            tca = along / sp
            if tca > 0.8:
                continue
            zt = pz + bvz * tca
            if zt < z - 2.0 or zt > z + 9.0 * sc + 6.0:
                continue
            bl.append((px, py, ux, uy, tca))
        if not bl:
            return None
        mux = sum(b[2] for b in bl)
        muy = sum(b[3] for b in bl)
        mux, muy = norm2(mux, muy)
        # the shooter sits back along the bullets' path
        src = (x - mux * 100.0, y - muy * 100.0)
        sxx, syy = -mux, -muy                      # from us toward the shooter
        R = 2.5 * sc + 0.9
        A = 58.5

        def hits(side):
            if side == 0:
                tvx, tvy = vx, vy
            else:
                tvx, tvy = -syy * side * walk, sxx * side * walk
            n = 0
            for px, py, ux, uy, tca in bl:
                nx, ny = -uy, ux
                lat = (x - px) * nx + (y - py) * ny
                vl = vx * nx + vy * ny
                tl = tvx * nx + tvy * ny
                lag = min(tca, 0.05)
                ta = tca - lag
                dv = tl - vl
                tr = abs(dv) / A
                if ta <= tr:
                    disp = vl * ta + 0.5 * math.copysign(A, dv) * ta * ta
                else:
                    disp = (vl + tl) * 0.5 * tr + tl * (ta - tr)
                if abs(lat + vl * lag + disp) < R:
                    n += 1
            return n

        keep, left, right = hits(0), hits(1), hits(-1)
        if keep == 0:
            return None
        if left == right:
            if left >= keep:
                return None
            # either side works: keep whichever we are already moving toward
            cur = vx * -syy + vy * sxx
            return (1.0 if cur >= 0 else -1.0, src)
        return (1.0 if left < right else -1.0, src)

    def mortar_dodge(self, obs, x, y, z):
        g = self.mortar_cfg.get("gravity", 22.0)
        sr = self.mortar_cfg.get("splash_radius", 7.0)
        for pr in obs.get("projectiles", []):
            if pr.get("kind") != "mortar":
                continue
            px, py, pz = pr["pos"]
            vx, vy, vz = pr["vel"]
            for k in range(1, 80):
                dt = 0.05 * k
                qx = px + vx * dt
                qy = py + vy * dt
                qz = pz + vz * dt - 0.5 * g * dt * dt
                surf = self.world.surface(qx, qy) if self.world else 0.0
                if qz <= surf or (abs(qx - x) < 3 and abs(qy - y) < 3 and qz < z + 9):
                    if math.hypot(qx - x, qy - y) < sr + 5.0:
                        return (qx, qy)
                    break
        return None

    def find_cover(self, x, y, z, threats, zone, zcx, zcy, zr):
        """A nearby walkable spot that the visible threats cannot see."""
        t = self.t
        if self.cover and t - self.cover_t < 0.6:
            cx, cy = self.cover
            if math.hypot(cx - x, cy - y) > 2.5:
                return self.cover
        w = self.world
        if w is None or not threats:
            return None
        best, bs = None, -1e18
        tpos = [(m["pos"][0], m["pos"][1], m["pos"][2] + 8 * self.scale(m.get("cubes", 0))) for m in threats]
        for r in (10.0, 20.0, 32.0, 46.0):
            for k in range(12):
                a = k * TAU / 12 + (r * 0.05)
                cx, cy = x + r * math.cos(a), y + r * math.sin(a)
                if not w.inside(cx, cy, 10.0):
                    continue
                if math.hypot(cx - zcx, cy - zcy) > zr - 8:
                    continue
                gi, gj = w.cell(cx, cy)
                ch = w.nh[gj * w.gw + gi]
                if ch - z > 1.6 or w.nbld[gj * w.gw + gi] and ch - z > 1.0:
                    continue
                cz = w.ground_at(cx, cy) if not w.nbld[gj * w.gw + gi] else ch
                hidden = 0
                for tp in tpos:
                    if not w.los(tp, (cx, cy, cz + 4.5)):
                        hidden += 1
                        if not w.los(tp, (cx, cy, cz + 8.5)):
                            hidden += 0.5
                if hidden == 0:
                    continue
                s = hidden * 100 - r * 1.2
                # don't run toward threats
                for tp in tpos:
                    dn = math.hypot(cx - tp[0], cy - tp[1])
                    do = math.hypot(x - tp[0], y - tp[1])
                    if dn < do - 5:
                        s -= 60
                if not self.walk_ok(x, y, z, cx, cy):
                    s -= 40
                if s > bs:
                    bs, best = s, (cx, cy)
        self.cover = best
        self.cover_t = t
        return best

    def wval(self, kind):
        if self.LIGHT_FIRST and kind == "lightning":
            return 3.6
        return WEAPON_VALUE.get(kind, 0)

    def choose_take(self, obs, x, y, z, weapons, laser):
        have = {wp["kind"]: i for i, wp in enumerate(weapons) if wp is not None}
        empty = [i for i, wp in enumerate(weapons) if wp is None]
        best = None
        bv = 0.0
        for p in obs.get("pickups", []):
            if p["id"] in self.bad_pickups and self.t - self.bad_pickups[p["id"]] < 3:
                continue
            if math.hypot(p["pos"][0] - x, p["pos"][1] - y) > self.pickup_r - 0.3 or abs(p["pos"][2] - z) > 3.8:
                continue
            k = p["kind"]
            if k in have:
                continue
            v = self.wval(k)
            if empty:
                if v > bv:
                    bv, best = v, {"take": p["id"]}
            elif self.LOADOUT_LL and k in ("laser", "lightning") and "gun" in have and (
                    ("laser" in have) != ("lightning" in have)):
                # second special weapon: it replaces the gun
                if v + 5 > bv:
                    bv, best = v + 5, {"take": p["id"], "drop": have["gun"]}
            else:
                # swap out the weakest non-gun weapon if this one is better
                worst_i, worst_v = None, 9.0
                for kind, i in have.items():
                    if kind == "gun":
                        continue
                    if kind == "laser" and laser and laser.get("charge", 0) > 0:
                        continue
                    if self.wval(kind) < worst_v:
                        worst_v, worst_i = self.wval(kind), i
                if worst_i is not None and v > worst_v + 0.3 and v > bv:
                    bv, best = v, {"take": p["id"], "drop": worst_i}
        return best

    def loot_target(self, obs, x, y, z, hpf, energy, zone, me, gun, npcs, fire, slots):
        """When no mech is in sight: weapons, cubes, supply drops, NPCs, crates."""
        weapons = me["weapons"]
        have = {wp["kind"] for wp in weapons if wp is not None}
        empty = any(wp is None for wp in weapons)
        worst = min((self.wval(wp["kind"]) for wp in weapons if wp and wp["kind"] != "gun"), default=0)
        ncx, ncy = zone["next_center"]
        nr = zone["next_radius"]
        cands = []
        def ok_zone(px, py):
            return math.hypot(px - ncx, py - ncy) < nr + 40 or nr > 300
        for pid, (pk, pp) in self.known.items():
            p = {"id": pid, "kind": pk, "pos": pp}
            if p["kind"] in have or (p["id"] in self.bad_pickups and self.t - self.bad_pickups[p["id"]] < 8):
                continue
            v = self.wval(p["kind"])
            want_ll = self.LOADOUT_LL and p["kind"] in ("laser", "lightning") and "gun" in have and (
                ("laser" in have) != ("lightning" in have))
            if not empty and v <= worst + 0.3 and not want_ll:
                continue
            dz = p["pos"][2] - z
            if dz > 1.5 and (dz > 30 or energy < dz * 1.8 + 15):
                continue
            d = math.hypot(p["pos"][0] - x, p["pos"][1] - y)
            rng = self.ARM_RANGE if p["kind"] in ("laser", "lightning") and (want_ll or not (have & {"laser", "lightning"})) else self.PICKUP_RANGE
            if d < rng and ok_zone(*p["pos"][:2]):
                cands.append((d - 40 * v - (150 if rng > self.PICKUP_RANGE else 0), p["pos"][0], p["pos"][1], None))
        for c in obs.get("cubes", []):
            dz = c["pos"][2] - z
            if dz > 1.5 and energy < dz * 1.8 + 15:
                continue
            d = math.hypot(c["pos"][0] - x, c["pos"][1] - y)
            if d < 90 and ok_zone(*c["pos"][:2]):
                cands.append((d - 50, c["pos"][0], c["pos"][1], None))
        for dr in obs.get("drops", []):
            px, py = dr["pos"][0], dr["pos"][1]
            d = math.hypot(px - x, py - y)
            if dr.get("holder") not in (None, me["id"]):
                continue
            eta = d / 11.0
            if d < 220 and hpf > 0.45 and eta > dr["lands_in"] - 6:
                cands.append((d - 80, px, py, None))
        # NPC and crate hunting: shoot from where we stand
        muzzle = (x, y, z + 7)
        tgt = None
        if hpf > 0.5:
            bestn = 1e9
            for n in npcs:
                d = math.dist(muzzle, n["pos"])
                if d < self.FARM_NPC_RANGE and n.get("hp", 350) > 0:
                    c = (n["pos"][0], n["pos"][1], n["pos"][2] + 1.8)
                    if self.los(muzzle, c) and d < bestn:
                        bestn, tgt = d, (n, c)
            if tgt is None:
                for cr in obs.get("crates", []):
                    d = math.dist(muzzle, cr["pos"])
                    c = (cr["pos"][0], cr["pos"][1], cr["pos"][2] + 1.2)
                    if d < self.FARM_CRATE_RANGE and self.los(muzzle, c) and d < bestn:
                        bestn, tgt = d, (cr, c)
        aim = None
        if tgt is not None:
            n, c = tgt
            v = n.get("vel", [0, 0, 0])
            d = math.dist(muzzle, c)
            speed = gun["stats"]["speed"] if gun else 1e6
            tau = d / speed
            tx, ty = c[0] + v[0] * tau, c[1] + v[1] * tau
            yaw = math.atan2(ty - y, tx - x)
            pitch = math.atan2(c[2] - muzzle[2], math.hypot(tx - x, ty - y))
            aim = (yaw, pitch)
            err = math.hypot(wrap(me["aim"][0] - yaw), me["aim"][1] - pitch) * d
            if gun and err < 1.4 and gun.get("ammo", 1) > 0 and gun.get("reload", 0) <= 0:
                fire[slots["gun"]] = True
            if "lightning" in slots and d < 40 and me["energy"] > 40:
                fire[slots["lightning"]] = True
            lz = next((wp for wp in weapons if wp and wp["kind"] == "laser"), None)
            if lz is not None and err < 2.5:
                full = lz["stats"].get("charge", 0.75)
                ch = lz.get("charge", 0)
                fire[slots["laser"]] = (ch < full * 0.97) and (ch > 0 or lz["cooldown"] <= 0.05)
            # walk closer: drones to ~55 m, crates to ~30 m
            if self.FARM_APPROACH:
                stand = 55.0 if n.get("max_hp", 300) >= 350 or n.get("id", 0) < 1000 else 30.0
                if not gun:
                    stand = 34.0
                if d > stand + 10:
                    ux, uy = norm2(c[0] - x, c[1] - y)
                    cands.append((d + 20, c[0] - ux * stand, c[1] - uy * stand, None))
        if cands:
            cands.sort()
            _, gx, gy, _ = cands[0]
            return gx, gy, aim
        if aim is not None:
            return x, y, aim
        return None

    def explore_point(self, x, y, zone):
        """A random open point inside the next circle, re-picked when reached or after 25 s."""
        ep = getattr(self, "_explore", None)
        ncx, ncy = zone["next_center"]
        rr = max(20.0, zone["next_radius"] * 0.8)
        if ep and self.t - self._explore_t < 25 and math.hypot(ep[0] - x, ep[1] - y) > 8 \
                and math.hypot(ep[0] - ncx, ep[1] - ncy) < rr + 5:
            return ep
        w = self.world
        best = None
        for _ in range(30):
            a = random.uniform(0, TAU)
            d = random.uniform(40, 140)
            px, py = x + d * math.cos(a), y + d * math.sin(a)
            if math.hypot(px - ncx, py - ncy) > rr:
                continue
            if w is not None:
                if not w.inside(px, py, 15):
                    continue
                gi, gj = w.cell(px, py)
                if w.nbld[gj * w.gw + gi]:
                    continue
            best = (px, py)
            break
        if best is None:
            best = (ncx, ncy) if w is None else w.nearest_open(ncx, ncy)
        self._explore = best
        self._explore_t = self.t
        return best

    def wall_spot(self, x, y, z, zone):
        """A ground cell beside a tall wall, inside the next circle, near us (cached for a few s)."""
        w = self.world
        if w is None:
            return None
        ncx, ncy = zone["next_center"]
        nr = zone["next_radius"]
        rr = max(0.0, nr - max(10.0, 0.18 * nr))
        sp = getattr(self, "_spot", None)
        if sp and self.t - self._spot_t < 8 and math.hypot(sp[0] - ncx, sp[1] - ncy) <= rr:
            return sp
        best, bd = None, 1e18
        ci, cj = w.cell(x, y)
        R = 12
        for dj in range(-R, R + 1):
            for di in range(-R, R + 1):
                ni, nj = ci + di, cj + dj
                if ni < 2 or nj < 2 or ni >= w.gw - 2 or nj >= w.gh - 2:
                    continue
                k = nj * w.gw + ni
                if w.nbld[k] or w.nblock[k]:
                    continue
                h0 = w.nh[k]
                if abs(h0 - z) > 3.0:
                    continue
                walls = 0
                for ei, ej in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    kk = (nj + ej) * w.gw + ni + ei
                    if w.nbld[kk] and w.nh[kk] - h0 > 8.0:
                        walls += 1
                if not walls:
                    continue
                px, py = w.cell_center(ni, nj)
                if math.hypot(px - ncx, py - ncy) > rr:
                    continue
                d = di * di + dj * dj - walls * (45 if self.CAMP else 6)
                if d < bd:
                    bd, best = d, (px, py)
        self._spot = best
        self._spot_t = self.t
        return best

    def value_spot(self, x, y, z, tgt, others, pref_d, zcx, zcy, zr):
        """Best nearby cell to fight from: keeps a line of fire to the target, is hidden from the
        other threats, sits by a wall, and is near the preferred range. Recomputed twice a second."""
        w = self.world
        if w is None:
            return None
        if self._vm is not None and self.t - self._vm_t < 0.5:
            return self._vm
        tp = tgt["pos"]
        tc = (tp[0], tp[1], tp[2] + 4.5)
        oth = [(o["pos"][0], o["pos"][1], o["pos"][2] + 8.0) for o in others[:4]]
        best, bs = None, -1e18
        cands = [(0.0, 0.0)] + [(r * math.cos(k * TAU / 10 + r), r * math.sin(k * TAU / 10 + r)) for r in (7.0, 14.0, 24.0) for k in range(10)]
        for dx, dy in cands:
            cx, cy = x + dx, y + dy
            if not w.inside(cx, cy, 10.0) or math.hypot(cx - zcx, cy - zcy) > zr - 8:
                continue
            gi, gj = w.cell(cx, cy)
            k = gj * w.gw + gi
            ch = w.nh[k]
            if ch - z > 1.6 or z - ch > 6.0:
                continue
            cz = ch if w.nbld[k] else w.ground_at(cx, cy)
            s = -0.8 * math.hypot(dx, dy)
            s += 40.0 if w.los((cx, cy, cz + 7.0), tc) else -30.0
            for op in oth:
                s += 20.0 if not w.los(op, (cx, cy, cz + 4.5)) else -15.0
            wall = 0
            for ei, ej in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ni, nj = gi + ei, gj + ej
                if 0 <= ni < w.gw and 0 <= nj < w.gh and w.nbld[nj * w.gw + ni] and w.nh[nj * w.gw + ni] - ch > 6.0:
                    wall = 1
            s += 10.0 * wall
            s -= 0.3 * abs(math.hypot(cx - tp[0], cy - tp[1]) - pref_d)
            if self._vm is not None and math.hypot(cx - self._vm[0], cy - self._vm[1]) < 5.0:
                s += 8.0
            if (dx or dy) and not self.walk_ok(x, y, z, cx, cy):
                s -= 25.0
            if s > bs:
                bs, best = s, (cx, cy)
        self._vm = best
        self._vm_t = self.t
        return best

    def hull_spot(self, x, y, z, tgt, tsc, msc, zcx, zcy, zr):
        """A nearby spot behind low cover: our muzzle (7 m up) sees the target over it while the
        target's gun cannot see our body center (4.5 m up). Recomputed twice a second."""
        w = self.world
        if w is None:
            return None
        if self.t - self._hs_t < 0.5:
            return self._hs
        tp = tgt["pos"]
        tc = (tp[0], tp[1], tp[2] + 4.5 * tsc)
        tmz = (tp[0], tp[1], tp[2] + 7.0 * tsc)
        best, bs = None, 0.0
        cands = [(0.0, 0.0)] + [(r * math.cos(k * TAU / 12 + r), r * math.sin(k * TAU / 12 + r)) for r in (3.0, 6.0, 10.0, 15.0, 21.0) for k in range(12)]
        for dx, dy in cands:
            cx, cy = x + dx, y + dy
            if not w.inside(cx, cy, 10.0) or math.hypot(cx - zcx, cy - zcy) > zr - 8:
                continue
            gi, gj = w.cell(cx, cy)
            k = gj * w.gw + gi
            ch = w.nh[k]
            if ch - z > 1.6 or z - ch > 6.0:
                continue
            cz = w.surface(cx, cy)
            if abs(cz - ch) > 1.5:
                continue
            if not w.los((cx, cy, cz + 7.0 * msc - 0.3), tc):
                continue                      # cannot shoot from here
            if w.los(tmz, (cx, cy, cz + 4.5 * msc)):
                continue                      # body center exposed: ordinary open ground
            sc_ = 50.0 - 1.2 * math.hypot(dx, dy)
            if not w.los(tmz, (cx, cy, cz + 6.2 * msc)):
                sc_ += 15.0                   # hidden up to the shoulders
            if self._hs is not None and math.hypot(cx - self._hs[0], cy - self._hs[1]) < 2.5:
                sc_ += 10.0
            if (dx or dy) and not self.walk_ok(x, y, z, cx, cy):
                sc_ -= 30.0
            if sc_ > bs:
                bs, best = sc_, (cx, cy)
        self._hs = best
        self._hs_t = self.t
        return best

    def arm_goal(self, x, y, z, energy, zcx, zcy, zr, skip=None, maxd=None):
        best, bd = None, (maxd or self.ARM_RANGE)
        for pid, (pk, pp) in self.known.items():
            if pk not in ("laser", "lightning") or pk == skip:
                continue
            if pid in self.bad_pickups and self.t - self.bad_pickups[pid] < 8:
                continue
            dz = pp[2] - z
            if dz > 1.5 and (dz > 30 or energy < dz * 1.8 + 15):
                continue
            if math.hypot(pp[0] - zcx, pp[1] - zcy) > zr - 5:
                continue
            d = math.hypot(pp[0] - x, pp[1] - y) - (self.ARM_LASER_BONUS if pk == ("lightning" if self.LIGHT_FIRST else "laser") else 0)
            if d < bd:
                best, bd = (pp[0], pp[1]), d
        return best

    def idle_look(self, obs, x, y, mv, t):
        if t < 1.7 and self.start_yaw is not None:
            return self.start_yaw + 3.9 * t      # opening 360 scan for weapons
        # look toward recent sounds of steps/guns, then sweep around the move direction
        snd = [s for s in obs.get("sounds", []) if s["kind"] in ("step", "boost", "jet", "gun", "laser_charge", "lightning", "reload")
               and s["dist"] in ("near", "mid")]
        if snd:
            snd.sort(key=lambda s: (s["dist"] != "near", s["age"]))
            self.look_yaw = snd[0]["bearing"]
            self.look_t = t
            return self.look_yaw
        # last known positions of mechs
        recent = [v for v in self.seen.values() if t - v["t"] < 12]
        if recent:
            r = min(recent, key=lambda v: math.hypot(v["pos"][0] - x, v["pos"][1] - y))
            base = math.atan2(r["pos"][1] - y, r["pos"][0] - x)
            return base + 0.5 * math.sin(t * 1.5)
        if math.hypot(*mv) > 0.3:
            base = math.atan2(mv[1], mv[0])
            amp = 1.9 if self.mode == "explore" else 1.0
            return base + amp * math.sin(t * 1.3)
        return (t * 0.9) % TAU - PI

    def follow(self, goal, x, y, z, me, energy, t, vis, zone):
        """Steer along an A* path to goal. Returns (move, jump, boost)."""
        w = self.world
        gx, gy = goal
        if w is None:
            return norm2(gx - x, gy - y), False, False
        if math.hypot(gx - x, gy - y) < 2.0:
            return (0.0, 0.0), False, False
        replan = (self.path_goal is None or math.hypot(gx - self.path_goal[0], gy - self.path_goal[1]) > 6
                  or t - self.path_t > 1.5 or not self.path or self.path_i >= len(self.path))
        if replan:
            jet_up = max(0.0, min(30.0, (energy - 25) / 1.8))
            avoid = []
            for v in self.seen.values():
                if t - v["t"] < 10:
                    avoid.append((v["pos"][0], v["pos"][1], 35.0, 6.0))
            path, done = w.astar((x, y), goal, jet_up=jet_up, budget=4000, avoid=avoid or None, start_h=z + 0.3, deadline_ms=7.0)
            self.path = path
            self.path_i = 1 if len(path) > 1 else 0
            self.path_goal = goal
            self.path_t = t
        # advance along the path; skip ahead along straight walkable segments
        path = self.path
        i = self.path_i
        while i < len(path) - 1 and math.hypot(path[i][0] - x, path[i][1] - y) < 3.0:
            i += 1
        j = i
        for k in range(i + 1, min(len(path), i + 6)):
            if path[k][2] - z > 1.4:
                break
            if self.walk_ok(x, y, z, path[k][0], path[k][1]):
                j = k
            else:
                break
        self.path_i = i
        wx, wy, wh = path[j] if path else (gx, gy, z)
        if j == len(path) - 1 and math.hypot(gx - wx, gy - wy) < 6:
            wx, wy = gx, gy
        mv = norm2(wx - x, wy - y)
        jump = boost = False
        up = wh - z
        self.climb = False
        if up > 1.3 and math.hypot(wx - x, wy - y) < 9.0:
            if energy > up * 1.6 + 6 or not me["grounded"]:
                self.climb = True
                jump = me["grounded"]
                boost = not me["grounded"]
        return mv, jump, boost


# ---------------------------------------------------------------------- IO loop

def _reader(q):
    for line in sys.stdin:
        if line.strip():
            q.put(line)
    q.put(None)


def send(msg):
    sys.stdout.write(json.dumps(msg, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    q = queue.Queue()
    threading.Thread(target=_reader, args=(q,), daemon=True).start()
    bot = None
    last = {}
    errors = 0
    while True:
        batch = [q.get()]
        while True:
            try:
                batch.append(q.get_nowait())
            except queue.Empty:
                break
        latest = None
        for raw in batch:
            if raw is None:
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "hello":
                try:
                    bot = Bot(msg)
                except Exception:
                    log("init failed:", traceback.format_exc())
                send({"type": "ready", "name": Bot.name})
            elif kind == "obs":
                latest = msg
            elif kind == "end":
                log(f"end place={msg.get('place')} kills={msg.get('kills')}")
                return
        if latest is None:
            continue
        action = {}
        if bot is not None:
            try:
                action = bot.act(latest) or {}
                last = {k: action[k] for k in ("move", "aim", "boost") if k in action}
            except Exception:
                errors += 1
                if errors <= 5 or errors % 100 == 0:
                    log(f"act failed at tick {latest.get('tick')}:\n{traceback.format_exc()}")
                action = dict(last)
        action["tick"] = latest["tick"]
        try:
            send(action)
        except (TypeError, ValueError):
            send({"tick": latest["tick"], **last})


if __name__ == "__main__":
    main()
