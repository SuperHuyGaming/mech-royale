# buildathon-mech-royale

Team "who dey" entry for the Builders Buildathon Mech Royale track (protocol `mechfight/1`).

```sh
docker build --platform linux/amd64 -t bot .
```

## Credit and disclosure

The bot in `cmd/` and `mech/` is a fork of giddygiddyboy's Mech Royale bot, used under its MIT License
(see `LICENSE`, copyright giddygiddyboy). Source: https://github.com/justnau/giddygiddyboy-mech-royale, commit
`e19e3c8d45d980df35c2fa5318353aaeb7c515bd`. Go, standard library only. We did not write that bot; the decision
logic, map code and settings in `cmd/` and `mech/` are theirs except for the changes listed below.

## Our changes on top of the fork

- `mech/scan.go` and one case in the aim fallback of `mech/brain.go`: a full-circle scan. When there is nothing
  to aim at, the torso keeps turning so the 120 degree view covers the whole circle. `scanAlways` in
  `mech/scan.go` chooses whether it runs in every match or only once a free-for-all is confirmed.
- `squadParams` in `mech/brain.go`: target choice weighs enemy health at 0.17 (the fork uses 0.18).
- `mech/reroll.go`: a switch to re-roll the bot's random choices (off).
- Earlier commits in this repository's history also tried, one at a time: re-rolling the bot's random choices
  (`mech/reroll.go`), single squad-setting changes in `squadParams`, and our own gun lead table (`mech/gf.go`).
  Each of those commits says what it changes in its message.

## Our own bot

`ownbot/` holds our own Python bot (not built into the image): `core.py` (helpers and I/O loop), `squad.py`
(decision loop), `geo.py` (map and paths). Its best practice score was 5.13 at commit
`22d9506c8c45f65347678907bd90206174873d50`.
