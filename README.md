<p align="center"><img src="docs/media/logo.png" width="128" alt="The RoyaleViser logo: a teal crown shield with a white eye on it, outlined in gold"></p>

<h1 align="center">RoyaleViser</h1>

<p align="center"><a href="https://github.com/RoyaleGym/RoyaleViser/actions/workflows/suite.yml"><img alt="CI" src="https://github.com/RoyaleGym/RoyaleViser/actions/workflows/suite.yml/badge.svg"></a> <img alt="License" src="https://img.shields.io/github/license/RoyaleGym/RoyaleViser?style=flat-square&color=555"> <img alt="Python" src="https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-3776AB?style=flat-square&logo=python&logoColor=white"> <a href="https://royalegym.github.io/RoyaleGym/"><img alt="Docs" src="https://img.shields.io/badge/docs-royalegym.github.io-8957e5?style=flat-square&logo=readthedocs&logoColor=white"></a> <a href="https://discord.gg/cvRu4nEGXY"><img alt="Discord" src="https://img.shields.io/discord/1551699576304705647?style=flat-square&logo=discord&logoColor=white&label=discord&color=5865F2"></a> <img alt="Last commit" src="https://img.shields.io/github/last-commit/RoyaleGym/RoyaleViser?style=flat-square&color=555"></p>

Watch your Clash Royale bot play, in a window on your own machine.
It is the viewer for the battles RoyaleGym runs, live while a bot trains or saved to a file.

<p align="center"><img src="docs/viewer-trace.png" width="100%" alt="The viewer on a battle from the engine, with a Knight pinned in the inspector"></p>

## Install

    pip install "royalegym[all]"

Python 3.12. The viewer is the `[viser]` part; [Install](https://royalegym.github.io/RoyaleGym/install/)
has the details.

## Try it

    royaleviser                         # watch a training run live
    royaleviser my_bot_battle.msgpack   # watch a saved battle
    royaleviser runs/                   # the newest saved battle in a folder

Space plays and pauses, the arrow keys step, `h` shows every key, `q` quits.

## Next

- The docs: [royalegym.github.io/RoyaleGym](https://royalegym.github.io/RoyaleGym/)
- Everything the viewer does, step by step: [the guide](https://royalegym.github.io/RoyaleGym/repos/royaleviser/guide/)
- How it works inside (Advanced): [internals](https://royalegym.github.io/RoyaleGym/repos/royaleviser/internals/)
- Questions: [Discord](https://discord.gg/cvRu4nEGXY)

MIT licensed. See [LICENSE](LICENSE).
