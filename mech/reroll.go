package mech

// roll re-rolls the bot's random choices (strafe timing, roam points) without changing its logic.
// 0 keeps the original seeding.
var roll uint64 = 0

func reroll() int64 {
	if roll == 0 {
		return 0
	}
	return int64(roll * 0x9E3779B97F4A7C15)
}
