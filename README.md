# Oval Runner — Mech Royale bot

Team Sandeep Madhavarapu's entry for the Builders Buildathon Mech Royale track.

The pilot is a Go program (`cmd/bot`, `mech/`) built into a static binary by the `Dockerfile` (multi-stage, toolchain
pinned by digest, `scratch` runtime, runs as uid 65534). It reads the map from `hello.map.dir` (`/arena`).

## Credits and third-party code (MIT)

The Go pilot is based on **giddygiddyboy-mech-royale** by giddygiddyboy
(https://github.com/justnau/giddygiddyboy-mech-royale, commit `e19e3c8d45d980df35c2fa5318353aaeb7c515bd`), used and
modified under its MIT License, kept in `LICENSE-giddygiddyboy`. Our changes on top of it are listed in the git
history and in the section below.

Our earlier TypeScript bot (lead aim, line-of-sight model, cliff-aware navigation, crate farming, mortar dodging) is
kept in `legacy/oval-runner-ts/` for reference.

## Our changes

- Renamed the pilot and repackaged the build (pinned Go image, static binary, scratch image).
- Ears: the pilot now turns toward unseen gunfire, laser charges, footsteps, jets and laser beams (giddy ignored sounds and beams), so attackers meet our front armor and enter our field of view sooner.
- EndGhost: in practice, once the next circle is 70 m or smaller, switch from squad-fighting to the hiding (ghost) mode and keep retreat/cover available, aiming to outlast the house bots in the zone collapse.
- `cmd/bot/params.json` (embedded at build time) overrides tuning parameters for the base and squad modes.
