"""The learning-status protocol, checked from this side of it.

RoyaleLearn implements the same wire protocol independently, on purpose: the viewer watches
the learner, so the dependency runs one way and pulling pygame into a training run to share a
constant would be the wrong trade. RoyaleLearn's own suite already compares the two copies
(``tests/test_viser_protocol.py`` there, under an ``importorskip`` on this package).

So why this file. That test fails in THAT suite. The constants it protects live HERE, and a
session editing this package runs THIS suite: a rename in ``sources.py`` would go green all
afternoon and render as an empty panel, which is indistinguishable from no learner attached.
A check belongs where the change is made, not only where the consequence is felt.

What this adds beyond a mirror of that file: the status is carried all the way into the PANEL
and the drawn rows are compared with the fields the sink filled. A field that survives the
wire and is not drawn is a field a learner reports into nothing, and neither side's constant
comparison can see it.

It skips, loudly, where RoyaleLearn is not installed -- a public clone of this repo has no
reason to have it -- and a skip here is not a pass.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import msgspec
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from royaleviser import model, sources
from royaleviser.render import LEARNING_GROUPS, UNSET, Renderer

viser_sink = pytest.importorskip(
    "royalelearn.metrics.viser_sink",
    reason=(
        "SKIPPED, NOT PASSED: this test pins this package's wire constants against the "
        "learner that sends them, and royalelearn is not installed here"
    ),
)


def test_the_constants_a_learner_sends_by_are_still_the_ones_here() -> None:
    """Every number and byte string the sender needs, graded from the viewer's copy."""
    assert sources.STREAM_HELLO == viser_sink.VISER_HELLO
    assert sources.STREAM_HEARTBEAT_S == viser_sink.VISER_HEARTBEAT_S
    assert sources.STREAM_ATTACH_TIMEOUT_S == viser_sink.VISER_ATTACH_TIMEOUT_S
    assert sources.STREAM_MAX_DATAGRAM == viser_sink.VISER_MAX_DATAGRAM
    assert sources.LEARNING_REPEATS == viser_sink.VISER_REPEATS
    assert sources.LEARNING_PORT_OFFSET == viser_sink.VISER_LEARNING_PORT_OFFSET
    assert (sources.STREAM_HOST, sources.STREAM_PORT) == (
        viser_sink.VISER_HOST,
        viser_sink.VISER_PORT,
    )
    assert model.LEARNING_TAG == viser_sink.LEARNING_TAG
    assert model.LEARNING_PREFIX == viser_sink.LEARNING_PREFIX
    # The port pairing is a rule, not a constant: both ends must derive the same endpoint.
    assert sources.learning_endpoint(
        sources.STREAM_HOST, sources.STREAM_PORT
    ) == viser_sink.learning_endpoint(None)
    assert sources.learning_endpoint("10.0.0.4", 7000) == viser_sink.learning_endpoint(
        "10.0.0.4:7000"
    )


def test_the_field_list_a_learner_fills_is_the_field_list_the_panel_draws() -> None:
    fields = tuple(f for f in model.Learning.__dataclass_fields__ if f != "extra")
    assert fields == viser_sink.LEARNING_FIELDS
    assert set(viser_sink.FIELD_SOURCES) <= set(fields)


def test_a_status_the_learner_builds_arrives_in_the_panel_with_its_numbers() -> None:
    """The end the constant comparisons cannot reach: a field can agree on both sides, cross
    the wire intact and still be drawn nowhere, which is a learner reporting into nothing."""
    row = {key: 1.5 for key in viser_sink.FIELD_SOURCES.values()}
    row["run/iteration"] = 12
    row["ladder/pool_size"] = 7
    row["ladder/eval_games_total"] = 2200
    row["policy/cards_per_match"] = 21.5
    row["ladder/gate_failed_condition"] = "beats_champion"
    sink = viser_sink.ViserSink(pump_thread=False)
    sink.run = "ppo-0007"

    data = msgspec.msgpack.encode({viser_sink.LEARNING_TAG: sink.status_of(row)})
    assert model.is_learning(data)
    status = model.decode_learning(data)

    r = Renderer(scale=24)
    drawn = dict(r.learning_lines(status))
    assert drawn, "the panel drew no rows at all"
    # Every fixed row the sink filled is drawn with a value, and none of them is the em dash
    # the panel uses for a field nobody sent.
    for _heading, fs in LEARNING_GROUPS:
        for label, attr, _fmt in fs:
            if attr in viser_sink.FIELD_SOURCES or attr in ("iteration", "pool_size"):
                assert drawn[label] != UNSET, f"{attr} arrived and is drawn as unset"
    # And the learner's own rows, which have no field of their own, reach the extra block.
    assert drawn["cards / match"] == "21.5"
    assert drawn["gate"] == "beats_champion"
    assert status.run == "ppo-0007"


def test_a_field_the_learner_leaves_out_is_drawn_as_unset_not_as_zero() -> None:
    sink = viser_sink.ViserSink(pump_thread=False)
    data = msgspec.msgpack.encode({viser_sink.LEARNING_TAG: sink.status_of({"run/iteration": 3})})
    status = model.decode_learning(data)
    drawn = dict(Renderer(scale=24).learning_lines(status))
    assert drawn["iteration"] == "3"
    assert drawn["KL"] == UNSET and drawn["ELO vs pool"] == UNSET
