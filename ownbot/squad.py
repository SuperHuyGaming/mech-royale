"""Squad brain: our second-generation decision loop.

Written from our own design notes (newbot/SPEC.md). It reuses our own helpers in bot.py
(path following, cover search, dodging, mortar maths) and replaces the decision loop.
"""
import math
import random

from core import Bot, norm2, wrap, TAU

LAT = 0.1  # seconds between what we see and when our action lands

UPGRADES = ["plating", "armor", "laser_damage", "laser_charge", "nanites", "gun_damage", "gun_rate",
            "lightning_efficiency", "lightning_range", "servos", "gun_mag", "gun_velocity", "gyros",
            "lightning_chain", "reactor", "capacitor", "mortar_reload", "mortar_splash", "mortar_velocity",
            "sensors", "jets", "laser_pierce"]
SLOT_RANK = {"laser": 4, "lightning": 3}


def find(weapons, kind):
    for i, w in enumerate(weapons):
        if w and w["kind"] == kind:
            return i, w
    return -1, None


class Squad(Bot):
    name = "who dey"

    # tunables (ours to tune)
    MAX_TARGET = 240.0
    NEAR_FOE = 60.0
    HP_WEIGHT = 0.18
    LOW_BONUS = 200.0
    STICK_BONUS = 200.0
    RANGE_GUN = 85.0
    RANGE_LASER = 120.0
    RANGE_LIGHT = 28.0
    RANGE_FINISH = 20.0
    FIGHT_CUBE = 70.0
    LOOT_R = 150.0
    LASER_SEEK = 240.0
    FARM_HP = 0.48
    LETHAL_K = 0.7

    def __init__(self, hello):
        super().__init__(hello)
        self.tr = {}            # mech id -> track
        self.np = {}            # npc id -> track
        self.cube_mem = {}
        self.crate_mem = {}
        self.bad = {}           # loot id -> ignore until
        self.loot_id = None
        self.loot_since = 0.0
        self.stick_id = None
        self.stick_t = -99.0
        self.lose_t = -99.0
        self.last_hurt = -99.0
        self.hurt_bearing = 0.0
        self.npc_hurt = -99.0
        self.npc_bearing = 0.0
        self.last_threat = -99.0
        self.side = 1.0
        self.side_until = 0.0
        self.slow_since = None
        self.free_until = -1.0
        self.free_dir = (1.0, 0.0)
        self.rng = random.Random((hello.get("seed", 0) or 0) * 7919 + 13)

    # ---------------------------------------------------------------- zone
    def zone_deadline(self, zone, px, py, pad, t):
        """First time the shrinking circle (minus pad) no longer covers the point."""
        end = zone["shrink_end"]
        for k in range(25):
            tt = t + (max(end, t) - t + 1.0) * k / 24.0
            cx, cy, r = self.zone_now(zone, tt)
            if math.hypot(px - cx, py - cy) > r - pad:
                return tt
        return 1e9

    def zone_cost(self, zone, px, py, t):
        cx, cy, r = self.zone_now(zone, t + 4.0)
        over = math.hypot(px - cx, py - cy) - (r - 10.0)
        return 6.0 * over if over > 0 else 0.0

    # ---------------------------------------------------------------- memory
    def remember(self, obs, me, x, y, z, t):
        eye = (x, y, z + 8.0)
        for ev in obs.get("events", []):
            et = ev.get("type")
            if et == "damaged" and ev.get("weapon") != "zone":
                by = ev.get("by")
                if ev.get("weapon") == "npc" or (by is not None and by >= 100):
                    self.npc_hurt = t
                    if ev.get("bearing") is not None:
                        self.npc_bearing = ev["bearing"]
                    if by in self.np:
                        self.np[by]["hit_me"] = t
                else:
                    self.last_hurt = t
                    if ev.get("bearing") is not None:
                        self.hurt_bearing = ev["bearing"]
                    if by is not None:
                        self.attackers[by] = t
            elif et == "take_failed":
                self.bad[ev.get("id")] = t + 20.0
        for m in obs.get("mechs", []):
            if m["id"] == me["id"]:
                continue
            book = self.tr if m["kind"] == "mech" else self.np
            old = book.get(m["id"])
            d = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
            closing = 0.0
            if old and t - old["t"] < 0.5 and t > old["t"]:
                closing = (old["d"] - d) / (t - old["t"])
            book[m["id"]] = {"id": m["id"], "pos": m["pos"], "vel": m["vel"], "t": t, "d": d,
                             "hp": m.get("hp", 1400), "max_hp": m.get("max_hp", 1400),
                             "weapons": m.get("weapons") or [], "cubes": m.get("cubes", 0),
                             "aim": m.get("aim") or [0.0, 0.0], "charging": m.get("charging", False),
                             "closing": closing, "hit_me": (old or {}).get("hit_me", -99.0)}
        for k, at in self.attackers.items():
            if k in self.tr:
                self.tr[k]["hit_me"] = max(self.tr[k]["hit_me"], at)
        for book, keep in ((self.tr, 20.0), (self.np, 30.0)):
            for k in [k for k, v in book.items() if t - v["t"] > keep]:
                del book[k]
        for v in list(self.tr.values()) + list(self.np.values()):
            if v["t"] != t:
                v["d"] = math.hypot(v["pos"][0] - x, v["pos"][1] - y)
        # loot memory: forget things we should be able to see but cannot
        for name, mem in (("cubes", self.cube_mem), ("crates", self.crate_mem), ("pickups", self.known)):
            vis = set()
            for c in obs.get(name, []):
                mem[c["id"]] = (c["kind"], c["pos"]) if name == "pickups" else c["pos"]
                vis.add(c["id"])
            for cid in [c for c in mem if c not in vis]:
                p = mem[cid][1] if name == "pickups" else mem[cid]
                if math.hypot(p[0] - x, p[1] - y) < 40 and self.los(eye, (p[0], p[1], p[2] + 1.0)):
                    del mem[cid]

    # ---------------------------------------------------------------- choices
    def want(self, weapons, kind):
        if kind == "gun":
            return weapons[0] is None
        if find(weapons, kind)[0] >= 0 or kind not in SLOT_RANK:
            return False
        cur = SLOT_RANK.get(weapons[1]["kind"], 1) if len(weapons) > 1 and weapons[1] else 0
        return SLOT_RANK[kind] > cur

    def pick_loot(self, obs, me, x, y, z, hpf, danger, zone, t, cap):
        walk = max(6.0, me["stats"].get("walk_speed", 11.7))
        ccx, ccy, cr = self.zone_now(zone, t)
        weapons = me["weapons"]
        best, best_u = None, 0.0

        def offer(kind, lid, pos, value):
            nonlocal best, best_u
            if self.bad.get(lid, 0) > t:
                return
            d = math.hypot(pos[0] - x, pos[1] - y)
            if d > 260 or math.hypot(pos[0] - ccx, pos[1] - ccy) > cr - 6:
                return
            if t + d * 1.4 / walk + 5 > self.zone_deadline(zone, pos[0], pos[1], 6.0, t):
                return
            if self.world and pos[2] - self.world.ground_at(pos[0], pos[1]) > 3.2:
                return
            u = value / (d + 18.0)
            if lid == self.loot_id:
                u *= 1.35
            if u > best_u:
                best_u, best = u, (kind, lid, pos)

        for pid, (kind, pos) in self.known.items():
            if not self.want(weapons, kind):
                continue
            d = math.hypot(pos[0] - x, pos[1] - y)
            seek = kind == "laser"
            if d < (self.LASER_SEEK if seek else self.LOOT_R):
                v = 130.0 if len(weapons) > 1 and weapons[1] is None else 60.0
                offer("pickup", pid, pos, max(v, 150.0) if seek else v)
        cube_v = 25.0 if me.get("cubes", 0) >= cap else 75.0
        for cid, pos in self.cube_mem.items():
            if math.hypot(pos[0] - x, pos[1] - y) < 105:
                offer("cube", cid, pos, cube_v)
        if hpf > self.FARM_HP and not danger:
            for cid, pos in self.crate_mem.items():
                if math.hypot(pos[0] - x, pos[1] - y) < self.LOOT_R:
                    offer("crate", cid, pos, cube_v * 0.6)
            if me.get("cubes", 0) < cap:
                for nid, n in self.np.items():
                    if t - n["t"] < 12 and n["d"] < self.LOOT_R:
                        offer("npc", nid, n["pos"], 85.0)
        if best is None:
            self.loot_id = None
            return None
        if best[1] != self.loot_id:
            self.loot_id, self.loot_since = best[1], t
        elif t - self.loot_since > 30:
            self.bad[best[1]] = t + 60
            self.loot_id = None
            return None
        return best

    def pref_range(self, weapons, energy, foe, finish):
        if finish:
            return self.RANGE_FINISH
        light = find(weapons, "lightning")[1]
        if light and energy > 12:
            return self.RANGE_LIGHT
        r = self.RANGE_LASER if find(weapons, "laser")[1] else self.RANGE_GUN
        if "lightning" in foe["weapons"]:
            r = max(r, 64.0)
        if "mortar" in foe["weapons"]:
            r = max(r, 80.0)
        return r

    def fight_step(self, x, y, z, me, foe, others, want_los, pref, zone, t, is_npc=False):
        """Pick one of 16 short steps (or standing still) that best holds range, sight and safety."""
        if is_npc:
            pref = 58.0
        if t >= self.side_until:
            if self.rng.random() < 0.6:
                self.side = -self.side
            self.side_until = t + 0.3 + 0.7 * self.rng.random()
        fx, fy = foe["pos"][0], foe["pos"][1]
        ux, uy = norm2(fx - x, fy - y)
        sx, sy = -uy * self.side, ux * self.side
        mh = me["stats"].get("muzzle_height", 7.0)
        body_h = me["stats"].get("height", 9.0) * 0.6
        fc = (fx, fy, foe["pos"][2] + 4.5 * self.scale(foe.get("cubes", 0)))
        cube = None
        for pos in self.cube_mem.values():
            d = math.hypot(pos[0] - x, pos[1] - y)
            if d < 40 and abs(pos[2] - z) < 4 and (cube is None or d < cube[0]):
                cube = (d, pos)
        w = self.world
        best, mv, side_blocked = -1e18, (0.0, 0.0), False
        for k in range(-1, 16):
            dx = dy = step = 0.0
            cx, cy = x, y
            if k >= 0:
                a = k * TAU / 16
                dx, dy, step = math.cos(a), math.sin(a), 6.0
                cx, cy = x + dx * step, y + dy * step
                if not self.walk_ok(x, y, z, cx, cy):
                    if dx * sx + dy * sy > 0.9:
                        side_blocked = True
                    continue
            cz = w.surface(cx, cy) if w else z
            if cz - z > 1.6:
                cz = z
            d = math.hypot(fx - cx, fy - cy)
            s = -1.1 * abs(d - pref) - 0.4 * step
            if self.los((cx, cy, cz + mh), fc):
                s += 45.0 * want_los
                seen_here = True
            else:
                seen_here = False
            for e in others:
                if e is foe or math.hypot(e["pos"][0] - cx, e["pos"][1] - cy) > 170:
                    continue
                if self.los((e["pos"][0], e["pos"][1], e["pos"][2] + 8.0), (cx, cy, cz + body_h)):
                    s -= 30.0
            s -= self.zone_cost(zone, cx, cy, t)
            if cube:
                s += 1.5 * (cube[0] - math.hypot(cube[1][0] - cx, cube[1][1] - cy))
            if want_los > 0:
                s += 9.0 * (dx * sx + dy * sy)
            if k < 0:
                s -= 8.0
                if want_los < 0 and not seen_here:
                    s += 14.0
            if s > best:
                best, mv = s, (dx, dy)
        if side_blocked:
            self.side = -self.side
            self.side_until = t + 0.5
        return mv

    def lead(self, muzzle, tp, tv, speed, grounded):
        """Yaw and pitch that make a bullet meet a target moving at constant velocity."""
        tt = 0.0
        px, py, pz = tp
        for _ in range(4):
            lt = tt + LAT
            px, py = tp[0] + tv[0] * lt, tp[1] + tv[1] * lt
            pz = tp[2] + (0.0 if grounded else tv[2] * lt)
            tt = math.dist((px, py, pz), muzzle) / max(1.0, speed)
        return (math.atan2(py - muzzle[1], px - muzzle[0]),
                math.atan2(pz - muzzle[2], math.hypot(px - muzzle[0], py - muzzle[1])))

    def table_vel(self, tid, t, mz, tp, tv):
        """Our lead table from recorded matches: keep the target's speed along our line of fire,
        and scale its sideways speed by how fast it is going sideways and whether it is speeding up."""
        dx, dy = tp[0] - mz[0], tp[1] - mz[1]
        d = math.hypot(dx, dy)
        if d < 1.0:
            return tv
        rx, ry = dx / d, dy / d
        nx, ny = -ry, rx
        vr = tv[0] * rx + tv[1] * ry
        vl = tv[0] * nx + tv[1] * ny
        trend = 1
        pv = self.pv.get(tid)
        if pv is not None and t - pv[0] < 0.25:
            dv = abs(vl) - abs(pv[1] * nx + pv[2] * ny)
            trend = 2 if dv > 0.8 else 0 if dv < -0.8 else 1
        self.pv[tid] = (t, tv[0], tv[1])
        sp = abs(vl)
        if sp < 3.0:
            f = 0.0 if trend == 0 else 1.0
        elif trend == 0:
            f = 0.4 if sp < 8 else 0.6
        else:
            f = 0.85 if trend == 1 else 0.95
        return (vr * rx + f * vl * nx, vr * ry + f * vl * ny, tv[2])

    # ---------------------------------------------------------------- main
    def act(self, obs):
        self.t = t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        vx, vy = me["vel"][0], me["vel"][1]
        st = me.get("stats", {})
        walk = max(6.0, st.get("walk_speed", 11.7))
        hpf = me["hp"] / max(1.0, me["max_hp"])
        energy = me["energy"]
        weapons = me["weapons"]
        zone = obs["zone"]
        cfg = self.cfg
        cap = cfg["cubes"].get("stat_cap", 10)
        act = {"tick": obs["tick"]}
        self.remember(obs, me, x, y, z, t)

        off = obs.get("offer")
        if off and off.get("choices"):
            ranks = [UPGRADES.index(c["id"]) if c["id"] in UPGRADES else 99 for c in off["choices"]]
            act["pick"] = ranks.index(min(ranks))

        gi, gun = find(weapons, "gun")
        _, light = find(weapons, "lightning")
        _, laser = find(weapons, "laser")
        late = zone["next_radius"] <= 70 or obs.get("alive", 16) <= 3

        # ---- who matters
        recent = [v for v in self.tr.values() if t - v["t"] <= 2.5]
        live = [v for v in recent if v["t"] == t]
        target = hidden = None
        best = 1e18
        for v in recent:
            v["foe"] = False
            if v["t"] != t:
                continue
            to_me = math.atan2(y - v["pos"][1], x - v["pos"][0])
            aimed = abs(wrap(v["aim"][0] - to_me)) < 0.22
            v["foe"] = (v["d"] < self.NEAR_FOE or t - v["hit_me"] < 6 or late
                        or (v["closing"] > 3 and v["d"] < 180)
                        or (v["hp"] < 0.4 * v["max_hp"] and v["d"] < 160)
                        or (aimed and v["d"] < 190))
            if not v["foe"] or v["d"] > self.MAX_TARGET:
                continue
            s = v["d"] + self.HP_WEIGHT * v["hp"]
            if v["hp"] <= 0.3 * v["max_hp"]:
                s -= self.LOW_BONUS
            if v["id"] == self.stick_id and t - self.stick_t < 2:
                s -= self.STICK_BONUS
            if t - v["hit_me"] < 4:
                s -= 40
            if v["closing"] > 3:
                s -= 15
            if s < best:
                best, target = s, v
        for v in recent:
            if v["t"] != t and t - v["hit_me"] < 6 and v["d"] < self.MAX_TARGET and (hidden is None or v["d"] < hidden["d"]):
                hidden = v
        if target:
            self.stick_id, self.stick_t = target["id"], t
        danger = any(v["foe"] or t - v["hit_me"] < 4 for v in recent)
        crowd = sum(1 for v in live if v["foe"] or v["d"] < 150)
        if danger or t - self.last_hurt < 3:
            self.last_threat = t
        finishing = bool(target) and target["d"] < 120 and (target["hp"] < 200 or target["hp"] < me["hp"] * 0.5)

        # ---- are we losing this spot?
        near = sum(1 for v in live if v["d"] < 160)
        losing = (near >= 2 and hpf < 0.55) or (near >= 3 and hpf < 0.8)
        if target:
            tf = target["hp"] / max(1.0, target["max_hp"])
            if hpf < 0.35 and tf > hpf + 0.1:
                losing = True
            if tf < 0.25 and hpf > 0.2 and near <= 2:
                losing = False
        if losing:
            self.lose_t = t
        retreat = t - self.lose_t < 1.5

        # ---- zone clock
        gx, gy, _inside = self.safe_point(zone, x, y)
        zd = math.hypot(gx - x, gy - y)
        need = zd * 1.45 / walk + 4.0
        deadline = self.zone_deadline(zone, x, y, 8.0, t)
        must_go = (not me.get("in_zone", True)) or t + need + 3 >= deadline
        should_go = t + need + 14 >= deadline

        vis = [(v, v["d"]) for v in live]
        mv, jump, boost, mode = (0.0, 0.0), False, False, "idle"
        farm = farm_pos = None

        def go(px, py):
            return self.follow((px, py), x, y, z, me, energy, t, vis, zone)

        shell = self.mortar_dodge(obs, x, y, z)
        if t < self.free_until:
            mv, mode = self.free_dir, "free"
        elif shell:
            mv = norm2(x - shell[0], y - shell[1])
            if mv == (0.0, 0.0):
                mv = (1.0, 0.0)
            boost, mode = energy > 15, "shell"
        elif must_go and zd > 2:
            mv, jump, b2 = go(gx, gy)
            boost = b2 or ((not me.get("in_zone", True) or t + need >= deadline) and energy > 5)
            mode = "zone"
        elif retreat and not finishing and not late:
            cv = self.find_cover(x, y, z, [{"pos": v["pos"], "cubes": v["cubes"]} for v in live],
                                 zone, *self.zone_now(zone, t))
            if cv:
                mv, jump, b2 = go(cv[0], cv[1])
                boost, mode = b2 or energy > 10, "cover"
            elif target:
                mv = self.fight_step(x, y, z, me, target, live, 1.0, self.pref_range(weapons, energy, target, False), zone, t)
                mode = "cornered"
            else:
                mv, jump, boost = go(gx, gy)
                mode = "flee"
        elif target or hidden:
            foe = target or hidden
            ready = bool(light or laser) or (gun and gun.get("ammo", 0) > 0 and gun.get("reload", 0) <= 0.25
                                             and foe["d"] <= gun["stats"]["range"] - 10)
            fin = (foe["hp"] < 0.38 * foe["max_hp"] and hpf > 0.42 and crowd <= 2)
            mv = self.fight_step(x, y, z, me, foe, live, 1.0 if ready else -1.0,
                                 self.pref_range(weapons, energy, foe, fin), zone, t)
            if light and foe["d"] > 50 and energy > 55 and ready:
                boost = True
            mode = "fight"
            reach = self.FIGHT_CUBE * (0.57 if hpf <= 0.5 else 1.0)
            near_cube = None
            for pos in self.cube_mem.values():
                d = math.hypot(pos[0] - x, pos[1] - y)
                if d < reach:
                    reach, near_cube = d, pos
            if near_cube:
                mv, jump, boost = go(near_cube[0], near_cube[1])
                mode = "fight:cube"
        elif should_go and zd > 2:
            mv, jump, boost = go(gx, gy)
            mode = "zone"
        else:
            lc = self.pick_loot(obs, me, x, y, z, hpf, danger, zone, t, cap)
            if lc:
                kind, lid, pos = lc
                mode = "loot:" + kind
                d = math.hypot(pos[0] - x, pos[1] - y)
                if kind == "npc":
                    n = self.np.get(lid)
                    if n and t - n["t"] < 1 and n["d"] < 75:
                        farm = n
                        mv = self.fight_step(x, y, z, me, n, live, 1.0, 58.0, zone, t, is_npc=True)
                    else:
                        mv, jump, boost = go(pos[0], pos[1])
                elif kind == "crate":
                    ch = cfg.get("crates", {}).get("height", 3.0)
                    cp = (pos[0], pos[1], pos[2] + ch * 0.5)
                    if d < 50 and self.los((x, y, z + st.get("muzzle_height", 7.0)), cp):
                        farm_pos = cp
                        if d < 12:
                            mv = norm2(x - pos[0], y - pos[1])
                    else:
                        mv, jump, boost = go(pos[0], pos[1])
                else:
                    mv, jump, boost = go(pos[0], pos[1])
            elif zd > 3:
                mv, jump, boost = go(gx, gy)
                mode = "zone-walk"

        # ---- side-step incoming fire
        if mode not in ("free", "shell"):
            dg = self.bullet_dodge(obs, x, y, z, vx, vy, self.scale(me.get("cubes", 0)), walk)
            if dg:
                side, src = dg
                ax, ay = norm2(src[0] - x, src[1] - y)
                px, py = -ay * side, ax * side
                mv = norm2(mv[0] * 0.4 + px * 0.9, mv[1] * 0.4 + py * 0.9)

        # ---- wedged against something
        moving = mv[0] != 0 or mv[1] != 0
        if moving and math.hypot(vx, vy) < 1.2 and me.get("grounded", True) and mode != "free":
            if self.slow_since is None:
                self.slow_since = t
            elif t - self.slow_since > 0.7:
                a = self.rng.random() * TAU
                self.free_dir, self.free_until = (math.cos(a), math.sin(a)), t + 0.5
                self.slow_since, self.path = None, []
                jump = True
        else:
            self.slow_since = None

        # ---- what to shoot
        mz = (x + vx * LAT, y + vy * LAT, z + st.get("muzzle_height", 7.0))
        defend = False
        if target is None and t - self.npc_hurt < 3:
            pick, lim = None, 0.7
            for n in self.np.values():
                if t - n["t"] > 0.5 or n["d"] > 150:
                    continue
                offb = 0.0 if t - n["hit_me"] < 3 else abs(wrap(math.atan2(n["pos"][1] - y, n["pos"][0] - x) - self.npc_bearing))
                if offb < lim:
                    lim, pick = offb, n
            if pick:
                farm, farm_pos, defend = pick, None, True
        tp = tv = None
        is_mech = False
        if target:
            sc = self.scale(target["cubes"])
            mc = cfg.get("mech", {})
            tr_, th_ = mc.get("radius", 3.0) * sc, mc.get("height", 9.0) * sc
            tp = [target["pos"][0], target["pos"][1], target["pos"][2] + th_ * 0.5]
            if not self.los(mz, tp):
                tp[2] = target["pos"][2] + th_ * 0.85
            tv, is_mech = target["vel"], True
        elif farm:
            nc = cfg.get("npc", {})
            tr_, th_ = nc.get("radius", 2.0), nc.get("height", 3.0)
            tp = [farm["pos"][0], farm["pos"][1], farm["pos"][2] + th_ * 0.5]
            tv = farm["vel"]
        elif farm_pos:
            cc = cfg.get("crates", {})
            tr_, th_ = cc.get("radius", 2.0), cc.get("height", 3.0)
            tp, tv = list(farm_pos), [0.0, 0.0, 0.0]
        if tp and not is_mech and (retreat or hpf < 0.5) and not defend:
            tp = None

        fire = [False, False]
        aim = None
        if tp:
            dist = math.hypot(tp[0] - x, tp[1] - y)
            grounded = abs(tv[2]) < 0.5
            hy = math.atan2(tp[1] + tv[1] * (LAT + 0.05) - mz[1], tp[0] + tv[0] * (LAT + 0.05) - mz[0])
            hp_ = math.atan2(tp[2] - mz[2], math.hypot(tp[0] - mz[0], tp[1] - mz[1]))
            tol_y = math.atan2(tr_ * 0.75, max(dist, 1.0))
            tol_p = math.atan2(th_ * 0.4, max(dist, 1.0))
            turn, pitch_rate = st.get("turn_rate", 4.0), st.get("pitch_rate", 3.0)
            cur = me["aim"]

            def on(wy, wp):
                return (max(0.0, abs(wrap(wy - cur[0])) - turn * 0.05) < tol_y
                        and max(0.0, abs(wp - cur[1]) - pitch_rate * 0.05) < tol_p)

            aim = [hy, hp_]
            gun_ok = False
            if gun:
                gv = self.table_vel(target["id"], t, mz, tp, tv) if is_mech else tv
                gy_, gp_ = self.lead(mz, tp, gv, gun["stats"]["speed"], grounded)
                aim = [gy_, gp_]
                gun_ok = dist <= gun["stats"]["range"] * 0.96
            for i, w in enumerate(weapons[:2]):
                if not w:
                    continue
                k, ws = w["kind"], w["stats"]
                if k == "gun":
                    fire[i] = bool(gun_ok and w.get("ammo", 0) > 0 and w.get("reload", 0) <= 0 and on(gy_, gp_))
                elif k == "lightning":
                    cone = math.radians(ws.get("cone_deg", 30.0))
                    fire[i] = (dist < ws["range"] - 1.5 and abs(wrap(hy - cur[0])) < cone * 0.5 - 0.05
                               and energy >= ws.get("energy", 0))
                elif k == "laser" and is_mech and dist <= ws["range"] * 0.97:
                    full = ws.get("charge", 1.5)
                    ch = w.get("charge", 0.0)
                    steady = abs(wrap(hy - cur[0])) < tol_y * 0.9 and abs(hp_ - cur[1]) < tol_p
                    release = False
                    if ws.get("min_charge", full) <= ch < full - 0.051:
                        mf = ws.get("min_frac", 0.3)
                        dmg = ws.get("damage", 0) * (mf + (1 - mf) * ch / full)
                        dmg *= 1 + cfg["cubes"].get("damage_per_cube", 0.0) * min(me.get("cubes", 0), cap)
                        if "mortar" in target["weapons"]:
                            dmg *= 1 + ws.get("cookoff", 0.0)
                        release = dmg * self.LETHAL_K >= target["hp"] and steady
                    if release:
                        aim = [hy, hp_]
                    elif ch < full - 0.051:
                        fire[i] = True
                        if ch > full * 0.45:
                            aim = [hy, hp_]
                    else:
                        aim = [hy, hp_]
                        fire[i] = not steady
                elif k == "mortar" and is_mech and w.get("cooldown", 0) <= 0.25 and 16 <= dist <= 200:
                    sol = self.mortar_solution((mz[0], mz[1], z + st.get("height", 9.0) + 0.5), tp, tv, ws)
                    if sol:
                        if not gun or gun.get("reload", 0) > 0 or gun.get("ammo", 0) == 0 or not gun_ok:
                            aim = [sol[0], sol[1]]
                        fire[i] = (max(0.0, abs(wrap(sol[0] - cur[0])) - turn * 0.05) < 0.04
                                   and max(0.0, abs(sol[1] - cur[1]) - pitch_rate * 0.05) < 0.03)
        elif hidden and laser:
            li = find(weapons, "laser")[0]
            ws = laser["stats"]
            if (li < 2 and laser.get("cooldown", 0) <= 0.05 and hidden["d"] < ws["range"]
                    and laser.get("charge", 0) < ws.get("charge", 1.5) + ws.get("hold_s", 1.0) - 0.35):
                fire[li] = True

        if aim is None:
            watch = None
            for v in recent:
                if (v["foe"] or t - v["hit_me"] < 6) and (watch is None or v["d"] < watch["d"]):
                    watch = v
            if watch:
                yaw = math.atan2(watch["pos"][1] - y, watch["pos"][0] - x)
            elif t - self.npc_hurt < 2.5:
                yaw = self.npc_bearing
            elif t - self.last_hurt < 2.5:
                yaw = self.hurt_bearing
            else:
                # nothing to shoot or watch: keep turning so the 120 degree view sweeps the full circle
                # (a full turn takes about 2 s; tracked mechs stay known for 5 s)
                yaw = me["aim"][0] + 1.6
            aim = [yaw, 0.0]
        aim[1] = max(st.get("min_pitch", -0.7), min(st.get("max_pitch", 1.3), aim[1]))

        if (gun and gun.get("reload", 0) <= 0 and gun.get("ammo", 0) < gun["stats"].get("mag", 12)
                and not tp and (gun["ammo"] <= gun["stats"].get("mag", 12) * 0.6 or t - self.last_threat > 3)):
            act["reload"] = True

        for pid, (kind, pos) in self.known.items():
            if math.hypot(pos[0] - x, pos[1] - y) > self.pickup_r - 0.3 or abs(pos[2] - z) > 3.6:
                continue
            if self.bad.get(pid, 0) > t or not self.want(weapons, kind):
                continue
            if kind != "gun" and len(weapons) > 1 and weapons[1]:
                if weapons[1]["kind"] == "laser" and weapons[1].get("charge", 0) > 0:
                    break
                act["drop"] = 1
            act["take"] = pid
            break

        act["move"] = [mv[0], mv[1]]
        act["boost"] = bool(boost and moving)
        act["jump"] = bool(jump)
        act["aim"] = aim
        act["fire"] = fire
        self.mode = mode
        return act
