"""A datagram that was superseded is not a datagram that was lost.

``StreamSource`` keeps only the NEWEST datagram of each drain and decodes that one, which is
right for a live monitor: a monitor that buffers is a monitor that lies about now. The drop
counter used to charge the whole gap in ``seq`` against the link, which read 0 for as long as
the environment published once per DECISION and became wrong the moment gym published once per
engine tick (RoyaleGym 187d5fa, 2026-09-22): ten datagrams a step, nine superseded before the
next draw, so a healthy stream reported about 90 % loss.

Both directions are tested here, and the second is the one that matters: a fix that makes a
counter read 0 by never counting is indistinguishable from a fix that makes it read 0 because
nothing is wrong.
"""

from __future__ import annotations

import time

import msgspec
import pytest

from royaleviser import sources
from synthetic import frame_at


def datagram(seq: int) -> bytes:
    """One frame on the wire, carrying exactly the sequence number asked for.

    The seq goes in LAST. Written the publisher's way round, ``{"seq": seq, **d["meta"]}``,
    a frame that already carries a seq keeps its own and every datagram here came out as the
    same number -- which made the healthy-case test below pass while testing nothing, since
    a constant sequence has no gaps to charge anyone for.
    """
    d = msgspec.to_builtins(frame_at(600))
    d["meta"] = {**d.get("meta", {}), "seq": seq}
    return msgspec.msgpack.encode(d)


@pytest.fixture
def src() -> sources.StreamSource:
    s = sources.StreamSource("127.0.0.1", 59998)
    yield s
    s.close()


def test_a_burst_that_all_arrived_costs_nothing(src: sources.StreamSource) -> None:
    """Ten sent, ten read, one drawn: the nine the viewer chose not to decode are not losses."""
    src.take_frame(datagram(0), read=1)
    src.take_frame(datagram(9), read=9)
    src.take_frame(datagram(19), read=10)
    assert src.drops == 0, "a superseded datagram is being charged to the link"


def test_a_datagram_that_never_arrived_is_still_counted(src: sources.StreamSource) -> None:
    """The control, and the reason this file exists rather than a one-line change.

    Nine arrive but the sequence jumped by twelve, so three never reached the socket. A
    counter that had simply stopped counting would read 0 here and look exactly like the
    healthy case above.
    """
    src.take_frame(datagram(0), read=1)
    src.take_frame(datagram(12), read=9)
    assert src.drops == 3, f"three datagrams never arrived and the counter says {src.drops}"


def test_the_ordinary_one_at_a_time_case_is_unchanged(src: sources.StreamSource) -> None:
    """A source publishing slower than the viewer draws reads one datagram per drain, which is
    the case the original counter was written for and must still be exact."""
    src.take_frame(datagram(0), read=1)
    src.take_frame(datagram(1), read=1)
    assert src.drops == 0
    src.take_frame(datagram(5), read=1)  # 2, 3 and 4 never came
    assert src.drops == 3


def test_the_publisher_and_the_viewer_agree_over_a_real_socket() -> None:
    """End to end against the real environment publisher, which is where the defect appeared.

    Asserts what gym asked me to check: that this end keeps up at one datagram per engine
    tick. Everything sent is received, and nothing is called lost.
    """
    from royalegym import ClashParallelEnv
    from royalegym.state_mutator import DefaultStateMutator
    from royalegym.viser import ViserPublisher
    from test_sources import ALL_TYPES

    pub = ViserPublisher(port=0)
    env = ClashParallelEnv(viser=pub, state_mutator=DefaultStateMutator(decks=[ALL_TYPES] * 2))
    env.reset(seed=3)
    src = sources.StreamSource(*pub.address)
    try:
        time.sleep(0.05)
        pub._last_poll = 0.0
        for _ in range(4):
            env.step({"blue": 0, "red": 0})
            time.sleep(0.05)
            src.frame()
        assert src.index == pub.sent, f"{pub.sent} sent, {src.index} reached the viewer"
        assert src.drops == 0, f"nothing was lost and the counter says {src.drops}"
        assert src.index >= 40, "the environment is not publishing once per engine tick"
    finally:
        src.close()
        env.close()
