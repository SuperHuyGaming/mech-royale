from __future__ import annotations
import json
import os
DEFAULTS = {'rng_salt': 0, 'weapon_value': {'laser': 10.0, 'lightning': 8.0, 'mortar': 2.0, 'gun': 5.0}, 'late_lightning_bonus': 4.0, 'late_radius': 140.0, 'swap_margin': 2.5, 'loot_detour_m': 90.0, 'cube_detour_m': 70.0, 'crate_shoot_range': 150.0, 'npc_hunt': True, 'npc_hunt_range': 140.0, 'npc_min_hp_frac': 0.8, 'fight_cube_m': 0.0, 'fight_cube_hp': 0.5, 'farm_regen_hp': 0.85, 'reload_below_frac': 0.6, 'squad_npc_min_hp_frac': 0.5, 'squad_npc_hunt_range': 170.0, 'squad_fight_cube_m': 60.0, 'squad_farm_regen_hp': 0.5, 'squad_reload_below_frac': 0.999, 'aggression': 1.0, 'engage_ratio': 1.15, 'retreat_hp_frac': 0.45, 'kite_range_laser': 120.0, 'close_range_lightning': 28.0, 'lightning_energy_reserve': 12.0, 'strafe_min_s': 0.35, 'strafe_max_s': 1.1, 'gun_aim_tol': 0.9, 'laser_aim_tol': 0.6, 'gun_slop': 0.02, 'laser_min_range': 0.0, 'mortar_min_range': 25.0, 'mortar_max_target_speed': 4.0, 'zone_margin': 24.0, 'zone_hold_frac': 0.55, 'zone_lead_s': 12.0, 'edge_cover_bonus': 1.0, 'drop_go': True, 'drop_max_dist': 160.0, 'drop_hp_frac': 0.70, 'upgrade_value': {'plating': 10.0, 'nanites': 8.5, 'armor': 8.0, 'laser_damage': 8.5, 'laser_charge': 6.0, 'laser_pierce': 1.0, 'gun_mag': 7.0, 'gun_damage': 7.0, 'gun_rate': 6.5, 'gun_velocity': 4.5, 'lightning_efficiency': 6.5, 'lightning_range': 6.0, 'lightning_chain': 3.5, 'mortar_reload': 4.0, 'mortar_velocity': 3.0, 'mortar_splash': 3.0, 'sensors': 5.0, 'servos': 5.0, 'gyros': 4.0, 'reactor': 4.0, 'capacitor': 3.5, 'jets': 3.5}, 'gun_laser_always': True, 'gun_laser_unless_ffa': False, 'squad_lightning_value': 4.0, 'squad_mode': True, 'squad_after_s': 0.0, 'squad_no_retreat': False, 'squad_heal_below': 0.8, 'squad_fight_back': False, 'squad_dmg_upgrades': True, 'dodge_pred': True, 'squad_dodge_pred': True, 'dodge_all': True, 'squad_dodge_all': True, 'squad_upgrade_value': {'laser_damage': 12.0, 'gun_damage': 11.0, 'gun_rate': 10.5, 'lightning_efficiency': 10.0, 'laser_charge': 9.5, 'lightning_chain': 8.0, 'lightning_range': 7.5}, 'squad_gun_laser': False, 'squad_hyper': False, 'squad_smith_target': False, 'cover_ray_budget': 150, 'astar_chunks_path': 5, 'astar_chunks_nopath': 7, 'astar_free_rays': 160, 'cover_guard_s': 0.015, 'tick_guard_s': 0.02, 'wd_on': True, 'wd_stall_s': 4.0, 'wd_ban_s': 60.0, 'wd_escape_s': 1.5, 'ffa_gate': True, 'ffa_gate_close_m': 60.0, 'ffa_gate_bump_m': 30.0, 'ffa_gate_weak': 0.5, 'ffa_gate_weak_hp': 0.6, 'ffa_gate_busy_hp': 0.75, 'ffa_gate_busy_m': 170.0, 'ffa_gate_hurt_alive': 10, 'ffa_gate_edge_alive': 6, 'ffa_gate_off_alive': 3, 'ffa_hit_s': 4.0, 'ffa_hide': True, 'ffa_hide_m': 190.0, 'ffa_hide_reach_m': 60.0, 'ffa_hide_wait_s': 10.0, 'ffa_quiet': True, 'ffa_quiet_m': 250.0, 'ffa_quiet_s': 8.0, 'ffa_snug': True, 'ffa_loot': True, 'ffa_loot_near_m': 25.0, 'ffa_cube_detour_m': 60.0, 'ffa_loot_mech_m': 110.0, 'ffa_loot_mech_s': 20.0, 'ffa_need_sure': True, 'squad_reactive': False, 'fight_boost_e': 0.0, 'fight_boost_stop_e': 45.0, 'fight_boost_charge_e': 28.0, 'fight_boost_ffa': False, 'squad_spot': False, 'squad_spot_m': 170.0, 'squad_spot_r': 7.0, 'squad_spot_gain': 10.0, 'squad_threat_cal': False, 'squad_ring': False, 'squad_ring_near_m': 60.0, 'squad_ring_near_hp': 0.85, 'squad_ring_m': 200.0, 'squad_ring_quiet_s': 2.0, 'squad_ring_hp': 0.65, 'squad_ring_abort_hp': 300.0, 'squad_ring_wait_s': 6.0, 'squad_ring_early_s': 8.0, 'ffa_unknown_s': 0.0, 'ffa_unknown_m': 0.0, 'squad_lose_rule': True, 'squad_lose_k': 1.0, 'squad_aggr': 1.25, 'squad_lose_pulse_s': 1.5, 'squad_lethal_laser': True, 'lethal_k': 0.7, 'squad_finish': False, 'squad_finish_hp': 0.38, 'squad_finish_my_hp': 0.42, 'squad_finish_others': 1, 'squad_finish_s': 6.0, 'squad_finish_range': 20.0, 'squad_finish_bonus': 6.0, 'squad_range': False, 'squad_range_gun': 85.0, 'squad_range_laser': 120.0, 'squad_last_stand': True, 'squad_last_stand_k': 0.8, 'squad_last_stand_reach': 0.7, 'squad_cover_m': 0.0, 'seed_k': 16, 'seed_play': -1, 'seed_salts': {'0': 9, '8': 9, '1': 7, '9': 7, '2': 0, '10': 0, '3': 9, '11': 9, '4': 9, '12': 9, '5': 10, '13': 10, '6': 7, '14': 7, '7': 5, '15': 5}, 'seed_over': {'0': {'squad_lose_rule': False, 'squad_lethal_laser': False, 'squad_last_stand': False}, '8': {'squad_lose_rule': False, 'squad_lethal_laser': False, 'squad_last_stand': False}, '3': {'squad_lose_rule': False, 'squad_lethal_laser': False, 'squad_last_stand': False}, '11': {'squad_lose_rule': False, 'squad_lethal_laser': False, 'squad_last_stand': False}, '4': {'squad_lose_rule': False, 'squad_lethal_laser': False, 'squad_last_stand': False}, '12': {'squad_lose_rule': False, 'squad_lethal_laser': False, 'squad_last_stand': False}}, 'scan_period_s': 3.2, 'debug_draw': True}

class Params(dict):

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as a:
            raise AttributeError(k) from a

def load() -> Params:
    d = Params(json.loads(json.dumps(DEFAULTS)))
    g = os.environ.get('MECH_PARAMS')
    if g and os.path.exists(g):
        try:
            with open(g) as a:
                c = json.load(a)
            for (b, h) in c.items():
                if isinstance(h, dict) and isinstance(d.get(b), dict):
                    d[b].update(h)
                else:
                    d[b] = h
        except Exception:
            pass
    return d
