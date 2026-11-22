# GMUNVIS Mech Royale: Fresh Bot Architecture & Technical Blueprint (`exp/own_mech`)

**Author:** `@mech-research`  
**Target Implementer:** `@mech-coder`  
**Priority:** 2 (Team MECH)  
**Objective:** Clean, modular, fresh Python implementation of Huy's Survival Plan, built from the ground up without copying or transliterating legacy or competitor code.

---

## 1. Executive Summary & Core Physics Rules

The goal in Mech Royale is maximizing **Placement Points** ($\text{Score} = 16 - \text{Placement}$).
- 1st Place = 15 pts, 2nd = 14 pts, ..., 16th (first to die) = 0 pts.
- Kills grant **0 placement points**. Survival time and staying inside safe zone cover is 100% of the tournament objective.
- In practice matches, the 15 house bots act as an interconnected squad: they share vision and target the player weakest-first when detected.

### Critical Game Mechanics to Exploit:
1. **Rear Armor Multiplier ($1.5\times$):**
   - Hits outside the torso's front $180^\circ$ arc deal **$1.5\times$ damage** (a laser deals $315 \times 1.5 = 472.5$ dmg!).
   - **Crucial Rule:** Decouple `move` and `aim`. When retreating or kiting to cover, `move` directs the legs away while `aim` remains locked facing the threat bearing. Never turn your back to an enemy!
2. **Passive Regeneration (45 HP/s):**
   - After **5.0 seconds** without dealing or taking damage while inside the safe zone, the mech regenerates **45 HP/s** (up to 1400 max HP).
   - **Crucial Rule:** Firing or taking a hit *resets* the 5-second timer. When injured, disengage combat completely, break Line-of-Sight (LOS) behind solid buildings, and wait for HP recovery before re-engaging.
3. **Sound Broadcast Ranges & Stealth:**
   - Gunfire: **320 m** | Mortar: **420 m** | Laser: **380 m** | Laser charging hum: **140 m**
   - Boost sprint: **90 m** | Footstep ($> 5.0$ m/s): **40 m**
   - Ground speed $\le 5.0$ m/s: **0 m (completely silent!)**
   - Standing still (`move = [0, 0]`): **0 m (completely silent!)**
   - **Crucial Rule:** Strict trigger discipline. Shooting a passing enemy broadcasts your position to all bots within 320 m. Do NOT shoot unless a shot is lethal, you are actively taking damage, or an enemy enters your point-blank defensive perimeter ($< 30$ m).
4. **Weapon Triangle & Mortar Hard Ban:**
   - **Laser:** Hitscan (infinite speed, 280 m range, 315 base dmg). Highest priority secondary.
   - **Lightning:** Auto-lock cone within 44 m, 180 dmg, disrupts enemy laser charge. Strong close-quarters secondary.
   - **Gun:** Arm kinetic gun (252 m/s, 230 m range, 45 dmg/shot). Standard primary.
   - **Mortar:** Hard ban! Drop immediately (`drop: 1`). Slow lob (72 m/s), near-zero hit rate on moving targets, and critically: **takes $+60\%$ cookoff damage when hit by enemy lasers!**

---

## 2. Directory & Module Structure (`exp/own_mech`)

```
exp/own_mech/
├── Dockerfile          # Compliant container sandbox: python:3.13-slim, uid 65534, /tmp tmpfs
├── mechfight.py        # Official kit protocol wrapper (standard I/O handling)
├── bot.py              # Main entrypoint: Bot class, protocol loop, top-level decision pipeline
├── config.py           # Centralized configuration, hyperparams, upgrade ranks, distance thresholds
├── map_geom.py         # Spatial hash grid of 3D building prisms, fast ray-AABB LOS, camp corner finder
├── aim.py              # Predictive aim: quadratic ballistic lead solver, laser settling, angular tolerances
├── weapons.py          # Weapon management: secondary looting, hard mortar ban, lethal early laser release
├── survival.py         # Huy's 7 survival plan points: camp anchoring, stealth, evasion, dead-side rotation
└── navigation.py       # Obstacle steering around buildings, zone clamping, jump/jet unsticking
```

---

## 3. Module Specifications & Algorithms

### 3.1 `config.py` — Central Parameters
```python
# Tuning constants for Bot
BOT_NAME = "GMUNVIS-Apex"

# Survival Thresholds
CAMP_MIN_R_FRAC = 0.58       # Inner radius fraction of next safe circle for camp spot
CAMP_MAX_R_FRAC = 0.85       # Outer radius fraction (edge play / dead side)
CAMP_ARRIVE_DIST = 3.0       # Distance (m) to consider anchored in camp spot
SILENT_SPEED_LIMIT = 5.0     # Clamped speed for 0 dB footstep sound
SILENT_ENEMY_DIST = 65.0     # When enemy within 65m, walk silently (<= 5 m/s)
STANDOFF_FARM_DIST = 150.0   # Distance to mechs required before farming drones/crates/cubes
DEFEND_PERIMETER_DIST = 30.0 # Point-blank defense radius (fire if enemy invades)
LETHAL_KILL_HP = 180.0       # HP threshold to break stealth and finish off target
RETREAT_HP_FRAC = 0.50       # Boost retreat when HP < 50%
RECOVER_HP_FRAC = 0.85       # End retreat mode once healed above 85% HP

# Endgame Thresholds
ENDGAME_ALIVE_COUNT = 3      # Alive mechs <= 3 triggers aggressive mode
ENDGAME_CIRCLE_RADIUS = 30.0 # Small circle radius triggers aggressive mode

# Upgrade Ranking
UPGRADE_RANK = {
    "armor": 100,               # -8% damage taken (critical for survival)
    "plating": 95,
    "hp": 90,
    "regen": 85,
    "nanites": 80,
    "laser_damage": 75,
    "laser_charge": 70,
    "gun_damage": 65,
    "gun_rate": 60,
    "speed": 40,
    "boost": 35,
    "mortar": -100,             # Hard ban
}
```

### 3.2 `map_geom.py` — Spatial Hash Grid & Line-of-Sight
```python
# Load /arena/v2/world.json at startup
# Extract prisms: each has base (float), top (float), ring ([x0, y0, x1, y1, ...])
# Compute 2D bounding boxes and 3D AABBs: (min_x, min_y, base, max_x, max_y, top)
# Partition into 80m x 80m spatial hash buckets: dict[(gx, gy), list[AABB]]

def has_line_of_sight(p1: tuple[float, float, float], p2: tuple[float, float, float]) -> bool:
    """Ray-AABB slab intersection test across grid cells traversed by ray from p1 to p2.
    Returns True if clear (no building intersection), False if blocked. Execution time: < 5 microseconds."""
    ...

def find_camp_spots_in_circle(next_center: tuple[float, float], next_radius: float, my_pos: tuple[float, float], sound_cues: list) -> list[tuple[float, float]]:
    """Generates candidate camp spots along exterior walls of building prisms that fall within
    the dead-side ring [0.58 * next_radius, 0.85 * next_radius].
    Scores candidates by:
      + 80 (orthogonal wall cover occluding >= 180 deg)
      - travel distance from my_pos
      - distance to map origin (avoid center meat-grinder)
      - penalty if near active sound cues (>= 80m away from combat sounds)
    Returns sorted list of safe camp coordinates."""
    ...
```

### 3.3 `aim.py` — Predictive Ballistic & Laser Aim
```python
def solve_gun_lead(muzzle: tuple[float, float, float], target_pos: tuple[float, float, float], target_vel: tuple[float, float, float], bullet_speed: float = 252.0) -> tuple[float, float]:
    """Solves quadratic equation ||target_pos + target_vel * t - muzzle||^2 = (bullet_speed * t)^2.
    Returns (yaw, pitch) aiming angles compensated for target movement and muzzle height."""
    ...

def solve_laser_aim(muzzle: tuple[float, float, float], target_pos: tuple[float, float, float], target_vel: tuple[float, float, float], lead_settle_s: float = 0.08) -> tuple[float, float]:
    """Torso slews at 4.0 rad/s. A short 80ms settle lead ensures the hitscan beam lands on center-of-mass."""
    ...

def is_aim_aligned(current_aim: tuple[float, float], target_aim: tuple[float, float], dist: float, target_radius: float = 2.5) -> bool:
    """Returns True only when angular error |delta_yaw| < tol and |delta_pitch| < tol, where tol = atan2(target_radius, dist).
    Prevents spraying ammo and wasting laser charges while torso is slewing."""
    ...
```

### 3.4 `weapons.py` — Weapon Management & Lethal Laser Execution
```python
class WeaponManager:
    def evaluate_pickups(self, me_pos, pickups, current_secondary, zone):
        """Scans visible pickups within 4.0m pickup radius:
        1. If mortar is held in slot 1 -> immediate drop: action['drop'] = 1.
        2. If laser is on ground within safe reach (< 250m) and no enemy LOS -> prioritize taking it.
        3. If lightning is nearby and slot 1 is empty -> take lightning."""
        ...

    def compute_fire_action(self, me, target, dist, has_los, is_aligned, is_endgame, under_attack):
        """Laser Charge & Release Logic:
        - When holding laser in slot 1:
          * If target aligned and charge >= 0.72s -> release trigger: action['fire'][1] = False (fires full-power beam).
          * Early Lethal Release: If current_charge * 420.0 >= target['hp'] and is_aligned -> release trigger early to secure kill!
          * Otherwise, hold trigger: action['fire'][1] = True (keep charging).
        - Primary Gun:
          * Fire slot 0 only when aligned, within 220m, has_los, and (under_attack or is_endgame or target['hp'] < LETHAL_KILL_HP or dist < DEFEND_PERIMETER_DIST).
          * Reload when ammo == 0."""
        ...
```

### 3.5 `survival.py` — Huy's 7 Survival Plan Points
1. **Spawn & Camp Spot Selection:**
   - On tick 0, read `zone['next_center']` and `zone['next_radius']`.
   - Calculate candidate camp spot along building walls in the dead-side ring ($[0.60, 0.85] \cdot R_{next}$).
   - Pre-rotate immediately at match start: sprint (`boost = True`) directly to the spot before enemy mechs spread out.
   - Upon arrival ($dist \le 3.0$ m): **Anchor.** Set `move = [0, 0]`. Torso aims outward facing open ground (protecting rear armor against the wall). Bot emits 0 dB sound.
2. **Stealth & Silent Movement:**
   - Suppress all weapon fire unless:
     a) Target HP $< 180$ (lethal finish in $< 1.5$s).
     b) Bot took damage in the last 3.0s (`damaged` event).
     c) An enemy enters $< 30$ m approaching the corner.
     d) Match enters Endgame (`alive <= 3`).
   - If an enemy is heard or seen within 65 m, clamp movement speed to $\le 5.0$ m/s (`move` vector clamped to $0.42$), emitting zero footsteps.
3. **Safe Farming & Upgrade Prioritization:**
   - Only harvest cubes, crates, or drones if:
     - No enemy mech within 150 m AND no enemy has Line-of-Sight to our position.
     - Never chase loot farther than 45 m from safe cover.
   - Upgrade pick: Always select highest rank from `UPGRADE_RANK` (`armor` > `hp` > `regen` > `laser`).
4. **Threat Response & Evasion:**
   - When taking damage or spotted:
     - Identify attacker bearing from `event['bearing']` or enemy pos.
     - Decouple legs and torso: set `move` toward nearest occluding building corner away from attacker, while keeping `aim` locked on attacker bearing.
     - If HP $< 50\%$, engage sprint boost (`boost = True`) to rapidly break LOS.
     - Once behind cover, stop firing to allow the 5.0s cooldown to expire, activating **45 HP/s passive regeneration**.
5. **Early Zone Rotation:**
   - As soon as `next_center` updates, do not wait for `shrink_start`.
   - If current camp spot is outside `next_circle` with margin $< 15$ m, compute new camp spot in `next_circle` and rotate immediately along the quiet circle perimeter.
6. **Third-Party Opportunism:**
   - If two enemy mechs are heard/seen fighting nearby, hold position in cover.
   - Do not intervene until one mech dies (`alive` decreases or death sound heard) or the survivor drops below 160 HP, then swoop in with laser/gun burst.
7. **Endgame Aggression:**
   - When `obs['alive'] <= 3` OR `zone['radius'] <= 30.0` m:
     - Transition to aggressive mode: full mobility, tactical strafing, target lowest HP enemy, precharge laser, and close in for 1st place.

### 3.6 `navigation.py` — Steering & Unsticking
```python
def steer_vector(my_pos, target_pos, building_grid, zone_center, zone_radius) -> list[float]:
    """Computes horizontal unit move vector [mx, my].
    Checks 3m, 6m forward probe points against building prisms.
    If blocked by wall, rotates probe angle in 20-degree increments (strafing around obstacles).
    Clamps destination inside safe zone perimeter (distance < zone_radius - 3.0m)."""
    ...

def handle_stuck(pos_history, energy) -> tuple[bool, bool]:
    """If position hasn't changed > 1.5m over last 8 ticks (stuck against low terrain/corner):
    Returns (jump=True, boost=True) to vault over geometry obstacles."""
    ...
```

---

## 4. Conformance & Performance Targets

1. **Kit Conformance Checker (`check.py`):**
   - Must run in Docker sandbox: 1 CPU, 512 MB RAM, read-only root, `/tmp` tmpfs 64 MB, non-root user `uid 65534`.
   - 0 missed 50 ms budgets (target: p50 $\le 1.5$ ms, max $\le 12.0$ ms).
2. **Simulation Evaluation (`faithful_sim.py`):**
   - Must achieve mean placement points $\ge 2.0$ over $\ge 200$ seeds.

---

## 5. Next Step for Implementation

@mech-coder owns building `exp/own_mech/` based on this blueprint. All modules are clean, modular, self-contained Python without third-party dependencies beyond standard library (`math`, `json`, `os`, `sys`, `time`, `random`).
