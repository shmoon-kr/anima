"""Game time x N for bots on a fast Mundi (--speed N, [server] speed): clocks and sleeps scale together."""
import asyncio
import time

from anima import timescale


def test_game_time_runs_n_times_and_stays_continuous():
    try:
        a = timescale.now()
        timescale.set_scale(10)
        b = timescale.now()
        assert abs(b - a) < 0.05, "no jump when the scale changes"
        t0 = time.monotonic()
        asyncio.run(timescale.sleep(1.0))                 # one game second ...
        assert time.monotonic() - t0 < 0.5, "... is a tenth of a wall second"
        assert timescale.now() - b >= 1.0
    finally:
        timescale.set_scale(1)
