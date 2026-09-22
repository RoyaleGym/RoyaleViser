"""RoyaleViser: an out-of-process viewer for Clash Royale battles.

One window, three sources (``royaleviser.sources``: recordings, traces, a running engine), one
frame model (``royaleviser.model``), one renderer (``royaleviser.render``) with the old
simulator's layout (``royaleviser.theme``). Never in the tick loop: a running engine sends
frames only while a viewer is attached.

    python -m royaleviser <capture.jsonl[.gz] | trace.msgpack> [--compare other] [--seat 0|1|local]
    python -m royaleviser --stream 127.0.0.1:9870
"""

from .model import Frame, Names, Player, Source, Spell, Unit

__all__ = ["Frame", "Names", "Player", "Source", "Spell", "Unit"]
