# rogue_agi mech bot

Bot for the Builders Buildathon Mech Royale track (protocol `mechfight/1`). Go, standard library only.

## Credits and what is ours

- Base pilot (navigation grid, aiming, squad-practice tuning): the MIT-licensed **giddygiddyboy** bot,
  copyright (c) 2026 giddygiddyboy, see `LICENSE-giddygiddyboy`.
- Late-game hide rule in practice: idea from SandeepMadhavarapu/BattleRoyale (MIT).
- Our own work (MIT, `LICENSE`): the free-for-all survival layer (`mech/survive.go`: threat bubble,
  cover search under fire, committed hiding goals), free-for-all detection and tuning (`mech/ffa.go`),
  per-slot seeding (`mech/salt.go`), and the changes to the decision loop in `mech/brain.go` that use them.

```sh
docker build --platform linux/amd64 -t bot .
```
