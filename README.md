# RoyaleViser

<p align="center"><a href="https://github.com/RoyaleGym/RoyaleViser/actions/workflows/suite.yml"><img alt="CI" src="https://github.com/RoyaleGym/RoyaleViser/actions/workflows/suite.yml/badge.svg"></a> <img alt="License" src="https://img.shields.io/github/license/RoyaleGym/RoyaleViser?style=flat-square&color=555"> <img alt="Python" src="https://img.shields.io/badge/python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white"> <a href="https://royalegym.github.io/RoyaleGym/"><img alt="Docs" src="https://img.shields.io/badge/docs-royalegym.github.io-8957e5?style=flat-square&logo=readthedocs&logoColor=white"></a> <a href="https://discord.gg/4D2BS5JBHP"><img alt="Discord" src="https://img.shields.io/discord/1551699576304705647?style=flat-square&logo=discord&logoColor=white&label=discord&color=5865F2"></a> <img alt="Last commit" src="https://img.shields.io/github/last-commit/RoyaleGym/RoyaleViser?style=flat-square&color=555"></p>

Watch your Clash Royale bot play, in a window on your own machine.
It is the viewer for the battles RoyaleGym runs, live while a bot trains or saved to a file.

<p align="center"><img src="docs/viewer-trace.png" width="100%" alt="The viewer on a battle from the engine, with a Knight pinned in the inspector"></p>

## Install

    pip install "royalegym[all]" --find-links https://github.com/RoyaleGym/RoyaleGym/releases/expanded_assets/v0.1.1

Python 3.12 or 3.13. The viewer is the `[viser]` part. The packages are not on PyPI yet, so the
line points pip at the release; [Install](https://royalegym.github.io/RoyaleGym/install/) has the
details.

## Try it

    royaleviser                         # watch a training run live
    royaleviser my_bot_battle.msgpack   # watch a saved battle
    royaleviser runs/                   # the newest saved battle in a folder

Space plays and pauses, the arrow keys step, `h` shows every key, `q` quits.

## Next

- The docs: [royalegym.github.io/RoyaleGym](https://royalegym.github.io/RoyaleGym/)
- Everything the viewer does, step by step: [the guide](https://royalegym.github.io/RoyaleGym/repos/royaleviser/guide/)
- How it works inside (Advanced): [internals](https://royalegym.github.io/RoyaleGym/repos/royaleviser/internals/)
- Questions: [Discord](https://discord.gg/4D2BS5JBHP)

MIT licensed. See [LICENSE](LICENSE).
