package mech

// Our addition to the fork: a full-circle scan when there is nothing to aim at.
// scanStep is how far (radians) to turn the torso each decision. A full circle takes
// about 2 s; a seen mech stays tracked for 5 s.
const scanStep = 1.6

// scanAlways turns the scan on in every match. When false, the scan only runs once the
// match is confirmed to be a free-for-all, and practice play is the same as the fork's.
const scanAlways = false
