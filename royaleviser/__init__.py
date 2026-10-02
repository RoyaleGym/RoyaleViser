"""RoyaleViser: an out-of-process viewer for Clash Royale battles.

One window, three sources (``royaleviser.sources``: recordings, traces, a running engine), one
frame model (``royaleviser.model``), one renderer (``royaleviser.render``) with the old
simulator's layout (``royaleviser.theme``). Never in the tick loop: a running engine sends
frames only while a viewer is attached.

    royaleviser                       # a run publishing on 127.0.0.1:9870
    royaleviser battle.msgpack        # a saved battle (or a folder: its newest)
"""

from importlib.metadata import PackageNotFoundError, version

from .model import Frame, Names, Player, Source, Spell, Unit

try:
    __version__ = version("royaleviser")  # pyproject.toml's, so there is one place to bump it
except PackageNotFoundError:  # imported from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = ["Frame", "Names", "Player", "Source", "Spell", "Unit", "__version__"]
