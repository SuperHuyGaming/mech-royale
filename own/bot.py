"""GMUNVIS-Ghost: stealth-first survival bot for Mech Royale (mechfight/1).

Plan (placement is the only score):
  * stay silent (creep below the 5 m/s step threshold) whenever an enemy may be near,
  * hide under tree crowns / beside building corners inside the next safe circle,
    as far as possible from every enemy we have seen recently, out of their line of sight,
  * never fire unless engaged (gunfire is heard 300+ m away), and only when the lead solution
    is tight enough that the shot is likely to land,
  * flee when hit: boost away behind cover while keeping the torso (front armor) on the attacker,
  * rotate to the next circle early, quietly,
  * endgame (<=3 alive or a small circle): fight with boost, laser first.
Standard library only.
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Tuple

import mechfight as mf

TAU = 2.0 * math.pi
CREEP = 0.40          # 0.40 * 11.7 m/s = 4.7 m/s < 5 m/s step-sound threshold
CELL = 40.0
JUKE = float(os.environ.get("GH_JUKE", "1.4"))
GOALW = float(os.environ.get("GH_GOALW", "1.0"))
FIGHTALL = os.environ.get("GH_FA", "0") == "1"
FLEEHP = float(os.environ.get("GH_FLEEHP", "0.45"))
WANT = float(os.environ.get("GH_WANT", "60"))


def wrap(a: float) -> float:
    return (a + math.pi) % TAU - math.pi


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# ----------------------------------------------------------------------------- world
class World:
    def __init__(self) -> None:
        self.boxes: List[Tuple[float, float, float, float]] = []
        self.grid: Dict[Tuple[int, int], List[int]] = {}
        self.trees: List[Tuple[float, float, float]] = []
        self.spots: List[Tuple[float, float, float]] = []  # x, y, concealment bonus

    def load(self, hello: dict) -> None:
        m = hello.get("map", {}) or {}
        d = m.get("dir", "/arena")
        paths = [os.path.join(d, "v2", "world.json"), "/arena/v2/world.json",
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "arena", "v2", "world.json")]
        for p in paths:
            try:
                with open(p, "r") as f:
                    w = json.load(f)
                break
            except Exception:
                w = None
        if not w:
            return
        for pr in w.get("prisms", []):
            if pr.get("kind") == "bridge" or (pr.get("top", 0) - pr.get("base", 0)) < 4.0:
                continue
            r = pr.get("ring", [])
            xs, ys = r[0::2], r[1::2]
            if not xs:
                continue
            self._add_box((min(xs), min(ys), max(xs), max(ys)))
        tr = w.get("trees", {}) or {}
        fields, data = tr.get("fields", []), tr.get("data", [])
        if fields and data:
            n = len(fields)
            ix, iy, ir = fields.index("x"), fields.index("y"), fields.index("crownR")
            icx = fields.index("crownX") if "crownX" in fields else ix
            icy = fields.index("crownY") if "crownY" in fields else iy
            for k in range(0, len(data) - n + 1, n):
                cx = data[k + icx] if data[k + icx] is not None else data[k + ix]
                cy = data[k + icy] if data[k + icy] is not None else data[k + iy]
                self.trees.append((cx, cy, data[k + ir] or 0.0))
        self._build_spots()

    def _add_box(self, b) -> None:
        i = len(self.boxes)
        self.boxes.append(b)
        for gx in range(int(math.floor(b[0] / CELL)), int(math.floor(b[2] / CELL)) + 1):
            for gy in range(int(math.floor(b[1] / CELL)), int(math.floor(b[3] / CELL)) + 1):
                self.grid.setdefault((gx, gy), []).append(i)

    def near_boxes(self, x0, y0, x1, y1):
        seen = set()
        for gx in range(int(math.floor(min(x0, x1) / CELL)), int(math.floor(max(x0, x1) / CELL)) + 1):
            for gy in range(int(math.floor(min(y0, y1) / CELL)), int(math.floor(max(y0, y1) / CELL)) + 1):
                for i in self.grid.get((gx, gy), ()):
                    if i not in seen:
                        seen.add(i)
                        yield i, self.boxes[i]

    def inside(self, x, y, pad=0.0) -> bool:
        for _, b in self.near_boxes(x - pad, y - pad, x + pad, y + pad):
            if b[0] - pad <= x <= b[2] + pad and b[1] - pad <= y <= b[3] + pad:
                return True
        return False

    @staticmethod
    def _seg_box(x0, y0, x1, y1, b) -> bool:
        dx, dy = x1 - x0, y1 - y0
        lo, hi = 0.0, 1.0
        for p, q in ((-dx, x0 - b[0]), (dx, b[2] - x0), (-dy, y0 - b[1]), (dy, b[3] - y0)):
            if p == 0.0:
                if q < 0.0:
                    return False
            else:
                t = q / p
                if p < 0.0:
                    if t > lo:
                        lo = t
                else:
                    if t < hi:
                        hi = t
                if lo > hi:
                    return False
        return True

    def blocker(self, x0, y0, x1, y1) -> Optional[int]:
        for i, b in self.near_boxes(x0, y0, x1, y1):
            if self._seg_box(x0, y0, x1, y1, b):
                return i
        return None

    def los(self, x0, y0, x1, y1) -> bool:
        return self.blocker(x0, y0, x1, y1) is None

    def under_tree(self, x, y) -> bool:
        for tx, ty, r in self.trees:
            if (x - tx) ** 2 + (y - ty) ** 2 <= r * r * 0.7:
                return True
        return False

    def _build_spots(self) -> None:
        out = []
        for b in self.boxes:  # building corners: two walls of cover
            for cx, cy in ((b[0] - 4.5, b[1] - 4.5), (b[2] + 4.5, b[1] - 4.5),
                           (b[0] - 4.5, b[3] + 4.5), (b[2] + 4.5, b[3] + 4.5)):
                if not self.inside(cx, cy, 3.2):
                    out.append((cx, cy, 10.0 + (25.0 if self.under_tree(cx, cy) else 0.0)))
        for tx, ty, r in self.trees:  # dense crowns hide us from far eyes
            if r >= 3.5 and not self.inside(tx, ty, 3.2):
                out.append((tx, ty, 22.0 + min(10.0, r)))
        self.spots = out


W = World()


# ----------------------------------------------------------------------------- memory
class Track:
    __slots__ = ("x", "y", "z", "vx", "vy", "t", "hp", "weapons", "facing")

    def __init__(self) -> None:
        self.x = self.y = self.z = self.vx = self.vy = 0.0
        self.t = -99.0
        self.hp = 1400.0
        self.weapons: List[str] = []
        self.facing = 0.0


UPGRADE_ORDER = ["plating", "armor", "nanites", "regen", "hp", "gun_damage", "laser_damage",
                 "laser_charge", "gun_rate", "lightning_efficiency", "servos", "gyros",
                 "gun_velocity", "gun_mag", "reactor", "capacitor", "jets", "sensors",
                 "lightning_chain", "lightning_range", "laser_pierce"]
WEAPON_RANK = {"laser": 3, "lightning": 2, "mortar": 0}


class Bot:
    name = "GMUNVIS-Ghost"

    def __init__(self, hello: Optional[dict] = None) -> None:
        hello = hello or {}
        if not W.boxes:
            W.load(hello)
        cfg = hello.get("config", {}) or {}
        wc = cfg.get("weapons", {}) or {}
        self.gun_speed = float((wc.get("gun", {}) or {}).get("speed", 252.0))
        self.gun_range = float((wc.get("gun", {}) or {}).get("range", 230.0))
        self.tracks: Dict[int, Track] = {}
        self.last_hit_t = -99.0
        self.hit_bearing: Optional[float] = None
        self.engaged_until = -99.0
        self.spot: Optional[Tuple[float, float]] = None
        self.spot_t = -99.0
        self.hist: List[Tuple[float, float]] = []
        self.scan = 0.0
        self.strafe = 1.0
        self.strafe_t = 0.0
        self.noise: List[Tuple[float, float, float]] = []  # bearing, weight, t

    # ------------------------------------------------------------------ helpers
    def threats(self, t: float, horizon: float = 25.0):
        return [(k, tr) for k, tr in self.tracks.items() if t - tr.t <= horizon]

    def safe_circle(self, zone: dict, t: float):
        c = zone.get("center", [0.0, 0.0])
        r = float(zone.get("radius", 500.0))
        nc = zone.get("next_center", c)
        nr = float(zone.get("next_radius", r))
        ss = float(zone.get("shrink_start", 0.0) or 0.0)
        # rotate early: aim for the next circle when its shrink is near (or underway)
        if nr < r and (ss - t) < 30.0:
            return nc[0], nc[1], nr
        return c[0], c[1], r

    def pick_spot(self, x, y, t, cx, cy, cr, zone):
        ths = self.threats(t, 30.0)
        best, best_s = None, -1e9
        reach = 160.0
        margin = clamp(cr * 0.25, 6.0, 45.0)
        cand = []
        for sx, sy, bonus in W.spots:
            d = math.hypot(sx - x, sy - y)
            if d > reach:
                continue
            dc = math.hypot(sx - cx, sy - cy)
            if dc > cr - margin:
                continue
            s = bonus - 0.25 * d - 0.08 * dc
            for _, tr in ths:
                de = math.hypot(sx - tr.x, sy - tr.y)
                s += min(de, 200.0) * 0.35 - (60.0 if de < 50.0 else 0.0)
            for b, wgt, nt in self.noise:
                if t - nt < 12.0:
                    s += math.cos(math.atan2(sy - y, sx - x) - b) * -12.0 * wgt
            cand.append((s, sx, sy))
        cand.sort(reverse=True)
        for s, sx, sy in cand[:25]:
            for _, tr in ths:
                if W.los(tr.x, tr.y, sx, sy):
                    s -= 30.0
            if s > best_s:
                best_s, best = s, (sx, sy)
        return best

    def steer(self, x, y, gx, gy):
        """Direction toward goal, detouring around the first building in the way."""
        i = W.blocker(x, y, gx, gy)
        if i is not None:
            b = W.boxes[i]
            if not (b[0] - 3 <= x <= b[2] + 3 and b[1] - 3 <= y <= b[3] + 3):
                best, bd = None, 1e18
                for cxx, cyy in ((b[0] - 6, b[1] - 6), (b[2] + 6, b[1] - 6), (b[0] - 6, b[3] + 6), (b[2] + 6, b[3] + 6)):
                    if W.los(x, y, cxx, cyy):
                        dd = math.hypot(cxx - x, cyy - y) + math.hypot(gx - cxx, gy - cyy)
                        if dd < bd:
                            bd, best = dd, (cxx, cyy)
                if best:
                    gx, gy = best
        dx, dy = gx - x, gy - y
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return 0.0, 0.0, 0.0
        return dx / d, dy / d, d

    def lead(self, x, y, z, mh, tr: Track, speed: float):
        tx, ty, tz = tr.x, tr.y, tr.z + 4.5
        px, py = tx, ty
        for _ in range(3):
            tf = math.hypot(px - x, py - y) / speed
            px, py = tx + tr.vx * tf, ty + tr.vy * tf
        dxy = math.hypot(px - x, py - y)
        return math.atan2(py - y, px - x), clamp(math.atan2(tz - (z + mh), max(1.0, dxy)), -0.7, 1.2), dxy

    # ------------------------------------------------------------------ main
    def act(self, obs: dict) -> dict:
        t = float(obs.get("t", 0.0))
        me = obs.get("self") or {}
        act: dict = {"tick": obs.get("tick", 0)}
        if not me:
            return act
        x, y, z = me.get("pos", [0.0, 0.0, 0.0])
        yaw0, pitch0 = (me.get("aim") or [0.0, 0.0])[:2]
        hp = float(me.get("hp", 1400.0))
        mhp = float(me.get("max_hp", 1400.0)) or 1400.0
        hpf = hp / mhp
        energy = float(me.get("energy", 100.0))
        grounded = me.get("grounded", True)
        mh = float((me.get("stats") or {}).get("muzzle_height", 7.0))
        weps = me.get("weapons") or [None, None]
        gun = weps[0] if weps else None
        sec = weps[1] if len(weps) > 1 else None
        sec_kind = sec.get("kind") if sec else None
        alive = int(obs.get("alive", 16))
        zone = obs.get("zone") or {}
        zc = zone.get("center", [x, y])
        zr = float(zone.get("radius", 500.0))

        # ---- events
        for ev in obs.get("events", []) or []:
            ty = ev.get("type")
            if ty == "damaged":
                b = ev.get("bearing")
                if b is not None or ev.get("source") not in (None, "zone"):
                    self.last_hit_t = t
                    if b is not None:
                        self.hit_bearing = b
                    self.engaged_until = t + 6.0
        # ---- memory
        vis = []
        for m in obs.get("mechs", []) or []:
            if m.get("kind") != "mech":
                continue
            k = m.get("id")
            tr = self.tracks.get(k) or Track()
            tr.x, tr.y, tr.z = m.get("pos", [0, 0, 0])
            v = m.get("vel") or [0, 0, 0]
            tr.vx, tr.vy = v[0], v[1]
            tr.t = t
            tr.hp = float(m.get("hp", tr.hp) or tr.hp)
            tr.weapons = m.get("weapons") or []
            tr.facing = (m.get("aim") or [0, 0])[0]
            self.tracks[k] = tr
            vis.append((k, tr, m))
        for s in obs.get("sounds", []) or []:
            if s.get("kind") in ("gun", "laser", "mortar", "lightning", "boost", "step", "explosion"):
                b = s.get("bearing")
                if b is not None:
                    w = {"near": 3.0, "mid": 1.5}.get(s.get("dist"), 0.5)
                    self.noise.append((b, w, t))
        self.noise = [n for n in self.noise if t - n[2] < 12.0][-40:]

        # ---- upgrades
        offer = obs.get("offer")
        if offer and offer.get("choices"):
            best, bs = 0, 1e9
            for i, ch in enumerate(offer["choices"]):
                cid = str(ch.get("id", "")).lower()
                r = 50
                for j, key in enumerate(UPGRADE_ORDER):
                    if key == cid or key in cid:
                        r = j
                        break
                if "mortar" in cid:
                    r = 90
                if r < bs:
                    bs, best = r, i
            act["pick"] = best

        # ---- gear: laser first, never keep the mortar (laser cook-off)
        if sec_kind == "mortar":
            act["drop"] = 1
            sec, sec_kind = None, None
        cur_rank = WEAPON_RANK.get(sec_kind, -1) if sec_kind else -1
        for p in obs.get("pickups", []) or []:
            pp = p.get("pos", [0, 0, 0])
            if math.hypot(pp[0] - x, pp[1] - y) <= 3.8 and abs(pp[2] - z) <= 3.8:
                rk = WEAPON_RANK.get(p.get("kind"), -1)
                if rk > cur_rank and rk > 0:
                    act["take"] = p.get("id")
                    cur_rank = rk

        # ---- situation
        endgame = alive <= 3 or zr <= 70.0
        engaged = t < self.engaged_until
        near_vis = [(k, tr, m) for k, tr, m in vis if math.hypot(tr.x - x, tr.y - y) < 230.0]
        facing_me = []
        for k, tr, m in near_vis:
            bme = math.atan2(y - tr.y, x - tr.x)
            if abs(wrap(tr.facing - bme)) < 0.5:
                facing_me.append((k, tr, m))
        if facing_me and any(math.hypot(tr.x - x, tr.y - y) < 120.0 for _, tr, _ in facing_me):
            engaged = True
            self.engaged_until = max(self.engaged_until, t + 2.0)
        fight = bool(near_vis) and (endgame or engaged or FIGHTALL)

        # target choice: weakest / closest, prefer those aiming at us
        target = None
        if near_vis:
            def score(e):
                k, tr, m = e
                d = math.hypot(tr.x - x, tr.y - y)
                s = -d - tr.hp * 0.05
                if e in facing_me:
                    s += 60.0
                if m.get("charging"):
                    s += 40.0
                return s
            target = max(near_vis, key=score)[1]

        # ---- aim & fire
        aim_yaw, aim_pitch = yaw0, 0.0
        fire0 = fire1 = False
        if target is not None:
            ly, lp, dxy = self.lead(x, y, z, mh, target, self.gun_speed)
            aim_yaw, aim_pitch = ly, lp
            if fight:
                err = abs(wrap(yaw0 - ly))
                tol = math.atan2(2.0, max(1.0, dxy))
                clear = W.los(x, y, target.x, target.y)
                if gun and clear and dxy <= min(self.gun_range, 200.0) and err < tol + 0.01 \
                        and float(gun.get("cooldown", 0.0)) <= 0.05:
                    fire0 = True
                if sec_kind == "laser" and clear and dxy <= 270.0:
                    tx = target.x + target.vx * 0.1
                    tyy = target.y + target.vy * 0.1
                    aim_yaw = math.atan2(tyy - y, tx - x)
                    charge = float(sec.get("charge", 0.0) or 0.0)
                    lerr = abs(wrap(yaw0 - aim_yaw))
                    ltol = math.atan2(2.2, max(1.0, dxy))
                    if float(sec.get("cooldown", 0.0) or 0.0) <= 0.0:
                        if charge < 0.70:
                            fire1 = True            # keep charging
                        else:
                            fire1 = lerr > ltol     # release only when on target
                elif sec_kind == "lightning" and dxy <= 44.0 and energy > 8.0 \
                        and abs(wrap(yaw0 - aim_yaw)) < math.radians(14.0):
                    fire1 = True
        elif self.hit_bearing is not None and t - self.last_hit_t < 4.0:
            aim_yaw = self.hit_bearing          # front armor toward the unseen shooter
        else:
            # idle scan: sweep the torso to spot enemies before they spot us
            self.scan += 0.35
            base = math.atan2(zc[1] - y, zc[0] - x)
            aim_yaw = wrap(base + math.pi + 1.6 * math.sin(self.scan * 0.5))
            if self.noise:
                aim_yaw = self.noise[-1][0]
        act["aim"] = [aim_yaw, aim_pitch]
        act["fire"] = [fire0, fire1]

        # ---- movement goal
        cx, cy, cr = self.safe_circle(zone, t)
        dcur = math.hypot(x - zc[0], y - zc[1])
        margin_cur = zr - dcur
        dnext = math.hypot(x - cx, y - cy)
        boost = False
        speed = 1.0
        mvx = mvy = 0.0

        recent = [tr for _, tr in self.threats(t, 8.0)]
        close_threat = any(math.hypot(tr.x - x, tr.y - y) < 70.0 for tr in recent)

        if (t - self.spot_t) > 2.0 or self.spot is None or \
                math.hypot(self.spot[0] - cx, self.spot[1] - cy) > cr:
            self.spot = self.pick_spot(x, y, t, cx, cy, cr, zone)
            self.spot_t = t
        goal = self.spot if self.spot else (cx, cy)

        flee = (t - self.last_hit_t < 3.0 and not endgame and not FIGHTALL) or (fight and hpf < FLEEHP and not endgame)
        shooter = target
        if shooter is None and self.hit_bearing is not None and t - self.last_hit_t < 3.0:
            shooter = Track()
            shooter.x = x + 100.0 * math.cos(self.hit_bearing)
            shooter.y = y + 100.0 * math.sin(self.hit_bearing)
        if (fight or flee) and shooter is not None:
            d = math.hypot(shooter.x - x, shooter.y - y)
            ux, uy = (shooter.x - x) / max(1.0, d), (shooter.y - y) / max(1.0, d)
            px, py = -uy, ux
            gx, gy, _ = self.steer(x, y, goal[0], goal[1])
            sign = 1.0 if (px * gx + py * gy) >= 0 else -1.0
            if t - self.strafe_t > JUKE:
                self.strafe, self.strafe_t = -self.strafe, t
            if JUKE < 50 and any(tr.facing is not None for tr in [shooter]) and t - self.strafe_t < 0.35:
                sign = -sign
            mvx, mvy = px * sign, py * sign
            if flee:
                mvx += gx * GOALW
                mvy += gy * GOALW
                boost = energy > 15.0
            else:
                want = 110.0 if sec_kind == "laser" else (30.0 if sec_kind == "lightning" else WANT)
                if d > want + 20.0:
                    mvx += ux * 0.6
                    mvy += uy * 0.6
                elif d < want - 15.0:
                    mvx -= ux * 0.6
                    mvy -= uy * 0.6
                boost = (endgame or t - self.last_hit_t < 2.0) and energy > 25.0
        elif flee:
            fx, fy, _ = self.steer(x, y, goal[0], goal[1])
            mvx, mvy = fx, fy
            boost = energy > 15.0
        else:
            fx, fy, d = self.steer(x, y, goal[0], goal[1])
            if d > 3.0:
                mvx, mvy = fx, fy
            speed = CREEP if close_threat else 1.0
            if d < 12.0:
                speed = min(speed, CREEP)

        # zone override
        need = dnext - cr * 0.6
        ss = float(zone.get("shrink_start", 0.0) or 0.0)
        if not me.get("in_zone", True) or margin_cur < 15.0:
            fx, fy, _ = self.steer(x, y, cx, cy) if dnext > 2 else (0.0, 0.0, 0.0)
            mvx, mvy, speed = fx, fy, 1.0
            boost = energy > 10.0
        elif need > 0 and (ss - t) < need / 11.7 + 5.0 and dnext > cr:
            fx, fy, _ = self.steer(x, y, cx, cy)
            mvx, mvy, speed = fx, fy, 1.0

        # dodge charging lasers pointed at us
        for k, tr, m in vis:
            if m.get("charging"):
                bme = math.atan2(y - tr.y, x - tr.x)
                if abs(wrap(tr.facing - bme)) < 0.12:
                    mvx += -math.sin(tr.facing) * 2.0
                    mvy += math.cos(tr.facing) * 2.0
                    speed = 1.0
                    boost = boost or energy > 20.0

        # hard zone boundary: never step outward near the edge
        rx, ry = x - zc[0], y - zc[1]
        rd = max(1.0, math.hypot(rx, ry))
        if zr - rd < 25.0:
            ux, uy = rx / rd, ry / rd
            out = mvx * ux + mvy * uy
            if out > 0:
                mvx -= out * ux
                mvy -= out * uy

        # anti-stuck
        self.hist.append((x, y))
        if len(self.hist) > 12:
            self.hist.pop(0)
        if len(self.hist) == 12 and speed >= 0.99 and math.hypot(mvx, mvy) > 0.5:
            if math.hypot(x - self.hist[0][0], y - self.hist[0][1]) < 0.6:
                if grounded:
                    act["jump"] = True
                mvx, mvy = -mvy, mvx

        n = math.hypot(mvx, mvy)
        if n > 1e-6:
            act["move"] = [mvx / n * speed, mvy / n * speed]
        else:
            act["move"] = [0.0, 0.0]
        act["boost"] = bool(boost and n > 1e-6)
        if gun and int(gun.get("ammo", 12) or 0) < 6 and not fire0 and not fight:
            act["reload"] = True
        return act


if __name__ == "__main__":
    mf.run(Bot)
