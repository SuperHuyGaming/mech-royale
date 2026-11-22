package mech

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
)

// ffaTuning is applied to the base parameters once a free-for-all is detected
// (mechs dying that we did not kill). MECH_FFA overrides it for local league tests.
var ffaTuning = `{"Bubble":180,"BubbleFight":50,"UseDrops":true,"EndHide":60}`

func applyFFA(p *Params) {
	t := ffaTuning
	if v := os.Getenv("MECH_FFA"); v != "" {
		t = v
	}
	_ = json.Unmarshal([]byte(t), p)
}

// applyBase lets local league tests override the opening parameters (MECH_BASE); unset in matches.
func applyBase(p *Params) {
	if v := os.Getenv("MECH_BASE"); v != "" {
		_ = json.Unmarshal([]byte(v), p)
	}
}

// ffaWatch collects evidence that mechs are fighting each other (a free-for-all) rather than
// acting as one squad: a damaged mech we never hit, a beam that was not aimed at us, or weapon
// fire heard while nobody is shooting at us. mask selects the evidence kinds (1, 2, 4).
type ffaWatch struct {
	hit      map[int]bool
	soundAt  float64
	aimedAt  float64
	Evidence int
}

const ffaWindow = 45.0

func segDist(px, py, ax, ay, bx, by float64) float64 {
	dx, dy := bx-ax, by-ay
	l2 := dx*dx + dy*dy
	t := 0.0
	if l2 > 0 {
		t = clamp(((px-ax)*dx+(py-ay)*dy)/l2, 0, 1)
	}
	return hyp(px-(ax+dx*t), py-(ay+dy*t))
}

func (b *Brain) ffaObserve(o *Obs, mask int) {
	w := &b.fw
	if w.hit == nil {
		w.hit = map[int]bool{}
		w.soundAt, w.aimedAt = -100, -100
	}
	me := &o.Self
	x, y := me.Pos[0], me.Pos[1]
	now := o.T
	for i := range o.Events {
		if e := &o.Events[i]; e.Type == "hit" {
			w.hit[e.Target] = true
		}
	}
	if now > ffaWindow {
		return
	}
	for i := range o.Mechs {
		m := &o.Mechs[i]
		if m.Kind != "mech" {
			continue
		}
		if math.Abs(angDiff(m.Aim[0], math.Atan2(y-m.Pos[1], x-m.Pos[0]))) < 0.35 {
			w.aimedAt = now
		}
		if mask&1 != 0 && m.MaxHP > 0 && m.HP < m.MaxHP-1 && !w.hit[m.ID] {
			w.Evidence |= 1
		}
	}
	if mask&2 != 0 {
		for i := range o.Beams {
			bm := &o.Beams[i]
			if bm.By == me.ID || bm.By >= 100 || len(bm.Path) < 2 {
				continue
			}
			near := math.Inf(1)
			for k := 0; k+1 < len(bm.Path); k++ {
				p, q := bm.Path[k], bm.Path[k+1]
				if len(p) >= 2 && len(q) >= 2 {
					near = math.Min(near, segDist(x, y, p[0], p[1], q[0], q[1]))
				}
			}
			if near > 15 && now-b.lastHurt > 1 {
				w.Evidence |= 2
			}
		}
	}
	if mask&4 != 0 {
		for i := range o.Sounds {
			switch o.Sounds[i].Kind {
			case "gun", "laser", "lightning", "mortar", "explosion":
				if w.soundAt < 0 {
					w.soundAt = now
				}
			}
		}
		if w.soundAt >= 0 {
			if now-b.lastHurt < 3 || now-w.aimedAt < 3 {
				w.soundAt = -100
			} else if now-w.soundAt >= 2 {
				w.Evidence |= 4
			}
		}
	}
}

// ffaMask selects which evidence kinds may trigger the free-for-all switch (0 = deaths only).
var ffaMask = 0

var ffaMaskEnv = -1

func ffaMaskNow() int {
	if ffaMaskEnv < 0 {
		ffaMaskEnv = ffaMask
		if v := os.Getenv("MECH_MASK"); v != "" {
			ffaMaskEnv = int(v[0] - '0')
		}
	}
	return ffaMaskEnv
}

// ffaLog appends the switch time to MECH_LOGFILE (local tests only).
func ffaLog(t float64, ev int) {
	if p := os.Getenv("MECH_LOGFILE"); p != "" {
		if f, err := os.OpenFile(p, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
			b, _ := json.Marshal(map[string]any{"t": t, "ev": ev})
			f.Write(append(b, '\n'))
			f.Close()
		}
	}
}

var traceF *os.File
var traceInit bool

func traceOn() bool {
	if !traceInit {
		traceInit = true
		if p := os.Getenv("MECH_TRACE"); p != "" {
			traceF, _ = os.OpenFile(fmt.Sprintf("%s.%d", p, os.Getpid()), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		}
	}
	return traceF != nil
}

func traceLine(slot int, t float64, mode string, hp, x, y, td, thp float64, tlos bool, tw string, nvis int, w1 string, retreat, prac, ffa, fire, boost bool, alive int) {
	fmt.Fprintf(traceF, "%d %.1f %-10s hp=%.0f pos=%.0f,%.0f foe=%.0f/%.0f los=%v %s vis=%d w=%s ret=%v prac=%v ffa=%v fire=%v boost=%v alive=%d\n",
		slot, t, mode, hp, x, y, td, thp, tlos, tw, nvis, w1, retreat, prac, ffa, fire, boost, alive)
}

// slowLog records slow decisions to MECH_LOGFILE (local tests only).
func slowLog(t float64, mode string, ms float64) {
	if p := os.Getenv("MECH_LOGFILE"); p != "" {
		if f, err := os.OpenFile(p+".slow", os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
			fmt.Fprintf(f, "%.1f %s %.2f\n", t, mode, ms)
			f.Close()
		}
	}
}

// ffaLatchUntil: deaths we did not cause only mean free-for-all early on; late in practice
// house mechs can die to the zone.
const ffaLatchUntil = 150.0
