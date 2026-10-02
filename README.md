# RoyaleViser

[![suite](https://github.com/RoyaleGym/RoyaleViser/actions/workflows/suite.yml/badge.svg)](https://github.com/RoyaleGym/RoyaleViser/actions/workflows/suite.yml)

Watch your Clash Royale bot play, in a window on your own machine.
It is the viewer for the battles RoyaleGym runs, live while a bot trains or saved to a file.

<p align="center"><img src="docs/viewer-trace.png" width="100%" alt="The viewer on a battle from the engine, with a Knight pinned in the inspector"></p>

## Install

    pip install royalegym[all]

The viewer is the `[viser]` part. Until the packages are on PyPI, install from the
GitHub repos as the [guide's Setup](docs/guide.md#setup) shows.

## Try it

    royaleviser                         # watch a training run live
    royaleviser my_bot_battle.msgpack   # watch a saved battle
    royaleviser runs/                   # the newest saved battle in a folder

Space plays and pauses, the arrow keys step, `h` shows every key, `q` quits.

## Next

- Everything the viewer does, step by step: [docs/guide.md](docs/guide.md)
- How it works inside (Advanced): [docs/internals.md](docs/internals.md)
- Questions: [Discord](https://discord.gg/4D2BS5JBHP)

MIT licensed. See [LICENSE](LICENSE).
