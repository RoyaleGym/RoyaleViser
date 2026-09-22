"""Open the real window on the synthetic battle STREAMED over UDP, learner and all.

    python tests/run_stream.py --seconds 6 --shot docs/viewer-learning.png
    python tests/run_stream.py --from-tick 900 --scale 20        # a window to watch

One process plays the parts a real run splits over two. A thread publishes the scripted
battle frame by frame (``sources.Publisher``, the environment's end of the stream) and a
``sources.LearningPublisher`` sends one status per iteration, on its own port and its own
clock. The viewer attaches to both over UDP, exactly as ``python -m royaleviser --stream
HOST:PORT`` does, and nothing reaches the window that did not come off the socket: it is
the look check for the live path and for the learning panel.

The numbers the fake learner sends are invented, and drift a little per iteration so the
panel visibly moves; the shape of the message is the real one.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from royaleviser import sources
from royaleviser.app import run
from royaleviser.model import Learning
from royaleviser.render import ViewState
from synthetic import battle

ITERATION_S = 2.0  # how often the fake learner reports; a real one is minutes apart


def iteration(n: int) -> Learning:
    """Iteration 1420 + n of a run that is slowly getting better."""
    return Learning(
        run="ppo-0007",
        iteration=1420 + n,
        policy_loss=0.0241 - 0.0004 * n,
        value_loss=0.1873 - 0.002 * n,
        entropy=1.204 - 0.003 * n,
        kl=0.0094 + 0.0002 * n,
        clip_frac=0.181,
        explained_var=0.62 + 0.004 * n,
        grad_norm=0.47,
        learning_rate=2.5e-4,
        env_steps_per_s=18400.0,
        engine_ticks_per_s=184000.0,
        episode_ticks=1870.0,
        crowns_per_episode=1.34,
        towers_per_episode=1.21,
        illegal_rate=0.0173,
        elixir_wasted=3.8,
        elo=1183.0 + 2 * n,
        win_rate=0.564,
        pool_size=6,
        games_vs_pool=2480 + 40 * n,
    )


def publish_battle(
    frames: list,
    pub: sources.Publisher,
    learner: sources.LearningPublisher,
    stop: threading.Event,
    tick_s: float,
) -> None:
    """The environment's thread: one frame per tick of wall time, a status per iteration."""
    started = time.monotonic()
    sent = 0
    for i, f in enumerate(frames):
        if stop.is_set():
            return
        pub.publish(f)
        while sent * ITERATION_S <= time.monotonic() - started:
            learner.publish(iteration(sent))
            sent += 1
        due = started + (i + 1) * tick_s
        if (wait := due - time.monotonic()) > 0:
            time.sleep(wait)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--shot", default=None)
    ap.add_argument("--seat", type=int, default=0)
    ap.add_argument("--scale", type=int, default=None)
    ap.add_argument("--from-tick", type=int, default=900, help="where the publisher starts")
    args = ap.parse_args(argv)

    frames = battle()[args.from_tick :]
    pub = sources.Publisher(port=0)
    learner = sources.LearningPublisher(port=0)
    src = sources.StreamSource(*pub.address, learner.address)
    stop = threading.Event()
    thread = threading.Thread(
        target=publish_battle,
        args=(frames, pub, learner, stop, frames[0].tick_ms / 1000),
        name="synthetic-publisher",
        daemon=True,
    )
    thread.start()
    try:
        return run(
            [src],
            ViewState(seat=args.seat),
            seconds=args.seconds,
            shot=args.shot,
            scale=args.scale,
        )
    finally:
        stop.set()
        thread.join(timeout=2.0)
        learner.close()
        pub.close()


if __name__ == "__main__":
    sys.exit(main())
