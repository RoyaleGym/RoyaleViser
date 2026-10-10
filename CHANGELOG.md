# Changelog

All notable changes to RoyaleViser. Versions follow [Semantic Versioning](https://semver.org).

## Unreleased

### Added
- A flier held on the ground by Vines is drawn on the ground: no shadow or air ring, and a
  dashed green ring instead.
- The inspector names all ten of the engine's status bits (clone, ability windup, ability
  active, charged and grounded were shown as numbers).

## 0.1.2

### Fixed
- A battle with an evolved card no longer crashes the viewer. RoyaleSim sends four values per
  evolution row, and the viewer read three, so live viewing and playing back a saved battle both
  stopped with "too many values to unpack".

## 0.1.1

### Changed
- The window library is pygame-ce instead of pygame. It is used the same way (`import pygame`)
  and installs on Python 3.14, which pygame does not. Uninstall pygame first if you have it:
  the two share the `pygame` folder.
- CI also installs and tests the viewer on Python 3.14.
- The guide tells a `pip install` user to skip Setup, which is only for working on the viewer.

## 0.1.0

The first tagged version: recordings, saved battles and a running engine in one window, with the
compare ghost, the inspector, pictures and clips.

- A `royaleviser` command. On its own it waits for a training run on `127.0.0.1:9870` and shows
  it live. Given a folder, it opens the newest saved battle in it.
- `royaleviser --version` and `royaleviser.__version__`.
- Saved battles show the special forms: hero cards are crowned, and evolved cards count their
  plays. A battle saved with RoyaleGym 2f710e8 or later also shows the ability buttons.
- A missing file, an empty folder or a missing RoyaleGym is reported in one line, not a
  traceback.
- Busy frames send on macOS (its default socket buffer refused a datagram over 9 KB).
- CI on Windows, Ubuntu and macOS.
- Opening a saved battle whose step log holds an ability press no longer crashes.
- Two cards in one deck can no longer show the same small-tile code.
