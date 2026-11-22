package mech

// Salt offsets the per-slot RNG seed (0 = the upstream behaviour).
const Salt int64 = 0

// slotSalt overrides Salt for single slots.
var slotSalt = map[int]int64{}

func saltFor(slot int) int64 {
	if s, ok := slotSalt[slot]; ok {
		return s
	}
	return Salt
}
