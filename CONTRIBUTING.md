# Contributing

Thanks for helping. Bug reports, fixes and new drawing ideas are all welcome.

## Set up

Follow the [guide's Setup](docs/guide.md#setup), then install the test tools:

    pip install -e ".[dev,media]"

## Before you open a pull request

Run the same two checks CI runs, from the `RoyaleViser` folder:

    python -m pytest -q
    python -m ruff check royaleviser tests

Some tests skip on your machine and say why in their skip line, for example when no engine is
built. A skip is not a pass, and CI runs the rest.

## What goes where

- How to use the viewer: [docs/guide.md](docs/guide.md).
- How it works inside, the frame model and the stream protocol: [docs/internals.md](docs/internals.md).
- A change a user will notice: add a line to [CHANGELOG.md](CHANGELOG.md) under "Unreleased".

## Questions

Ask on [Discord](https://discord.gg/cvRu4nEGXY), or open an issue.
