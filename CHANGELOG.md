# Changelog

All notable changes to RoyaleViser. Versions follow [Semantic Versioning](https://semver.org).

## Unreleased

### Added
- A `royaleviser` command. On its own it waits for a training run on `127.0.0.1:9870` and shows
  it live. Given a folder, it opens the newest saved battle in it.
- `royaleviser --version` and `royaleviser.__version__`.
- Saved battles show the special forms: hero cards are crowned, and evolved cards count their
  plays. A battle saved with RoyaleGym 2f710e8 or later also shows the ability buttons.

### Changed
- The README is short. The full guide is now [docs/guide.md](docs/guide.md).
- A missing file, an empty folder or a missing RoyaleGym is reported in one line, not a
  traceback.
- CI also runs on macOS.

### Fixed
- Opening a saved battle whose step log holds an ability press no longer crashes.
- Two cards in one deck can no longer show the same small-tile code.

## 0.1.0

The first version: recordings, saved battles and a running engine in one window, with the
compare ghost, the inspector, pictures and clips.
