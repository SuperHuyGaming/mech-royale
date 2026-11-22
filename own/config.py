"""
Configuration and hyperparameters for GMUNVIS-Apex Mech Royale bot.
"""
from __future__ import annotations

# Identity
BOT_NAME = "GMUNVIS-Apex"

# Survival and Positioning Thresholds
CAMP_MIN_R_FRAC: float = 0.58       # Inner radius fraction of next safe circle for camp spot
CAMP_MAX_R_FRAC: float = 0.85       # Outer radius fraction (edge play / dead side)
CAMP_ARRIVE_DIST: float = 3.0       # Distance (m) to consider anchored in camp spot
SILENT_SPEED_LIMIT: float = 5.0     # Clamped speed for 0 dB footstep sound (m/s)
NORMAL_WALK_SPEED: float = 11.7     # Base mech walking speed (m/s)
SILENT_MOVE_CLAMP: float = SILENT_SPEED_LIMIT / NORMAL_WALK_SPEED  # ~0.427
SILENT_ENEMY_DIST: float = 65.0     # When enemy within 65m, walk silently (<= 5 m/s)
STANDOFF_FARM_DIST: float = 150.0   # Distance to mechs required before farming drones/crates/cubes
DEFEND_PERIMETER_DIST: float = 30.0 # Point-blank defense radius (fire if enemy invades)
LETHAL_KILL_HP: float = 180.0       # HP threshold to break stealth and finish off target
RETREAT_HP_FRAC: float = 0.50       # Boost retreat when HP < 50%
RECOVER_HP_FRAC: float = 0.85       # End retreat mode once healed above 85% HP

# Endgame Aggression Thresholds
ENDGAME_ALIVE_COUNT: int = 3        # Alive mechs <= 3 triggers aggressive mode
ENDGAME_CIRCLE_RADIUS: float = 30.0 # Small circle radius triggers aggressive mode

# Combat Range Spacing
OPTIMAL_LASER_DIST: float = 125.0
OPTIMAL_LIGHTNING_DIST: float = 28.0
OPTIMAL_GUN_DIST: float = 75.0

# Upgrade Ranking (Higher is better, negative means never pick)
UPGRADE_RANK = {
    "armor": 100,               # -8% damage taken (critical for survival)
    "plating": 95,
    "hp": 90,
    "health": 90,
    "regen": 85,
    "nanites": 80,
    "laser_damage": 75,
    "laser_charge": 70,
    "laser": 68,
    "gun_damage": 65,
    "gun_rate": 60,
    "rotary": 60,
    "speed": 40,
    "boost": 35,
    "sensor": 30,
    "mortar": -100,             # Hard ban
}

# Weapon Ranking for Pickups
WEAPON_RANK = {
    "laser": 4,
    "lightning": 3,
    "gun": 1,
    "mortar": -100,
}
