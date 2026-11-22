package mech

import (
	"math"
	"time"
)

// Survival layer for free-for-alls (rogue_agi's own work).
//
// Placement is survival order, and most early deaths come from letting an armed mech walk
// up to duelling range and then trading shots in the open. This layer keeps a bubble around
// us: as soon as another mech is tracked inside it we move, before contact, to the reachable
// spot that is out of everyone's line of sight and far from them, and stay quiet there.

type evadeState struct {
	goal   [2]float64
	at     float64
	ok     bool
	hidden bool
	hot    bool
	hotAt  float64
	setAt  float64
	score  float64
}

// threatsNear lists tracked mechs (seen in the last memory seconds) within radius r.
func (b *Brain) threatsNear(me *Self, r, memory float64) []*Track {
	var out []*Track
	for _, t := range b.tracks {
		if b.now-t.Seen > memory {
			continue
		}
		if hyp(t.Pos[0]-me.Pos[0], t.Pos[1]-me.Pos[1]) < r {
			out = append(out, t)
		}
	}
	return out
}

// evadeMove picks and walks to the best hiding spot away from the given threats.
// It reports false when there is nothing to evade or nowhere better to go.
func (b *Brain) evadeMove(o *Obs, threats []*Track, hot bool, mx, my *float64) bool {
	if len(threats) == 0 {
		return false
	}
	me := &o.Self
	x, y := me.Pos[0], me.Pos[1]
	e := &b.ev
	pathW, nearW, lat, maxSteps := 1.1, 0.55, 4, 85.0
	if hot {
		e.hotAt = b.now
	} else if b.now-e.hotAt < 3 {
		hot = true // stay in the under-fire regime briefly so the goal does not flip
	}
	if hot {
		pathW, nearW, lat, maxSteps = 3.0, 0.25, 2, 45.0
	}
	// Commit to a hidden goal: re-plan only when it is reached, exposed, or stale.
	if e.ok && e.hidden && b.now-e.setAt < 4 && hyp(e.goal[0]-x, e.goal[1]-y) > 2 {
		gz := b.W.WalkAt(e.goal[0], e.goal[1])
		still := true
		for _, t := range threats {
			ey := b.eye(t)
			if b.W.Clear(ey, Vec3{e.goal[0], e.goal[1], gz + me.Stats.Height*0.6}) || b.W.Clear(ey, Vec3{e.goal[0], e.goal[1], gz + me.Stats.Height}) {
				still = false
				break
			}
		}
		if still {
			*mx, *my = b.evadeTravel(me)
			return true
		}
	}
	if e.ok && e.hot == hot && b.now-e.at < 0.5 {
		*mx, *my = b.evadeTravel(me)
		return true
	}
	if len(threats) > 4 {
		// keep the four nearest
		for i := 0; i < 4; i++ {
			for j := i + 1; j < len(threats); j++ {
				if hyp(threats[j].Pos[0]-x, threats[j].Pos[1]-y) < hyp(threats[i].Pos[0]-x, threats[i].Pos[1]-y) {
					threats[i], threats[j] = threats[j], threats[i]
				}
			}
		}
		threats = threats[:4]
	}
	h := me.Stats.Height
	score := func(cx, cy float64, steps float64) (float64, bool) {
		cz := b.W.WalkAt(cx, cy)
		body := Vec3{cx, cy, cz + h*0.6}
		head := Vec3{cx, cy, cz + h}
		seenBy := 0
		near := math.Inf(1)
		for _, t := range threats {
			d := hyp(t.Pos[0]-cx, t.Pos[1]-cy)
			near = math.Min(near, d)
			if d < 260 {
				ey := b.eye(t)
				if b.W.Clear(ey, body) || b.W.Clear(ey, head) {
					seenBy++
				}
			}
		}
		s := nearW*math.Min(near, 220) - pathW*steps - b.zonePenalty(&o.Zone, cx, cy)
		if seenBy == 0 {
			s += 110
		} else {
			s -= 35 * float64(seenBy-1)
		}
		cl := float64(b.W.Clearance[b.W.Cell(cx, cy)])
		if cl < 2 {
			s -= (2 - cl) * 8 // too tight to move
		} else if cl > 7 {
			s -= math.Min(cl-7, 10) * 1.5 // a wall close by is the escape from a charging laser
		}
		// off-centre is safer than the middle of the next circle
		if nr := o.Zone.NextRadius; nr > 80 {
			if rr := hyp(cx-o.Zone.NextCenter[0], cy-o.Zone.NextCenter[1]) / nr; rr < 0.45 {
				s -= (0.45 - rr) * 60
			}
		}
		return s, seenBy == 0
	}

	start := time.Now()
	w := b.W
	s0 := w.NearestFree(w.Cell(x, y))
	if s0 < 0 {
		return false
	}
	w.gen++
	gen := w.gen
	queue := []int32{int32(s0)}
	w.stamp[s0] = gen
	w.g[s0] = 0
	bestC, bestS, bestH := -1, math.Inf(-1), false
	hereS, hereH := score(x, y, 0)
	checked := 0
	for qh := 0; qh < len(queue) && checked < 480; qh++ {
		c := int(queue[qh])
		ci, cj := c%w.NNX, c/w.NNX
		steps := float64(w.g[c])
		if steps > maxSteps {
			break
		}
		if ci%lat == 0 && cj%lat == 0 && steps > 2 {
			cx, cy := w.CellPos(c)
			checked++
			if checked%24 == 0 && time.Since(start) > 9*time.Millisecond {
				break // never risk the 50 ms decision budget
			}
			if sc, hid := score(cx, cy, steps); sc > bestS {
				bestC, bestS, bestH = c, sc, hid
			}
		}
		for k, o2 := range nbrs {
			i, j := ci+o2[0], cj+o2[1]
			if i < 0 || j < 0 || i >= w.NNX || j >= w.NNY {
				continue
			}
			nb := j*w.NNX + i
			if w.stamp[nb] == gen || !w.canStep(c, nb, k >= 4) {
				continue
			}
			if k >= 4 && (w.Blocked[cj*w.NNX+i] || w.Blocked[j*w.NNX+ci]) {
				continue
			}
			w.stamp[nb] = gen
			w.g[nb] = w.g[c] + 1
			queue = append(queue, int32(nb))
		}
	}
	e.at, e.hot = b.now, hot
	// Staying put wins unless somewhere is clearly better (a hidden spot always beats an exposed one).
	if bestC < 0 || (hereS >= bestS-12 && (hereH || !bestH)) {
		e.ok, e.hidden, e.goal, e.score = true, hereH, [2]float64{x, y}, hereS
		*mx, *my = 0, 0
		return true
	}
	gx, gy := w.CellPos(bestC)
	if e.ok && hyp(e.goal[0]-x, e.goal[1]-y) > 3 {
		// keep the current goal unless the new one is clearly better
		if cur, _ := score(e.goal[0], e.goal[1], hyp(e.goal[0]-x, e.goal[1]-y)/2); cur >= bestS-20 {
			*mx, *my = b.evadeTravel(me)
			return true
		}
	}
	e.ok, e.hidden, e.goal, e.score = true, bestH, [2]float64{gx, gy}, bestS
	e.setAt = b.now
	*mx, *my = b.evadeTravel(me)
	return true
}

func (b *Brain) evadeTravel(me *Self) (float64, float64) {
	if hyp(b.ev.goal[0]-me.Pos[0], b.ev.goal[1]-me.Pos[1]) < 1.5 {
		return 0, 0
	}
	return b.travel(me, b.ev.goal[0], b.ev.goal[1])
}

// endHide keeps the survival bubble on in the last phase: with a few mechs left, the others
// wear each other down while we heal out of sight. We fight only an opponent clearly weaker than
// us, or when the circle is too small to hide in (P.EndHide is the smallest next radius, m).
func (b *Brain) endHide(o *Obs, me *Self) bool {
	if b.P.EndHide <= 0 || o.Zone.NextRadius < b.P.EndHide {
		return false
	}
	for _, t := range b.tracks {
		if t.Seen != b.now || !t.LOS {
			continue
		}
		if t.HP < 0.7*me.HP || t.HP < 400 {
			return false
		}
	}
	return true
}
