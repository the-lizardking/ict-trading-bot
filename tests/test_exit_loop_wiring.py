"""The decoupled exit loop's wiring — the actual Tier-2 behaviour change.

Everything before this was scaffolding that changed nothing. These pin the three
properties that make the change safe to deploy: the rollback works, the loop
cannot be permanently stopped by a raising pass, and a hanging pass leaves
liveness ageing rather than refreshing it.
"""
from __future__ import annotations

import importlib

import pytest

main = importlib.import_module("src.main")


# --- the interval knob fails ON ------------------------------------------------

@pytest.mark.parametrize("bad", ["", "0", "-5", "abc"])
def test_unusable_interval_falls_back_rather_than_pausing(monkeypatch, bad):
    """A typo must not silently stop exit evaluation, which is the one thing this
    loop exists to do. Pausing is not offered as a value; the rollback puts the
    work back on the tick instead of dropping it."""
    monkeypatch.setenv("EXIT_LOOP_INTERVAL_SECONDS", bad)
    assert main.exit_loop_interval_seconds() == 30.0


def test_interval_default_is_near_the_measured_pass():
    """~28s measured pass, so 30s is effectively back-to-back with slack."""
    assert main.exit_loop_interval_seconds() == 30.0


# --- the rollback switch -------------------------------------------------------

@pytest.mark.parametrize("val,expected_decoupled", [
    (None, True), ("", True), ("0", True), ("false", True),
    ("1", False), ("true", False), ("yes", False), ("ON", False),
])
def test_rollback_switch_semantics(val, expected_decoupled):
    """Default-OFF kill-switch over an ON capability (the REGIME_ROUTER_DISABLED
    shape), NOT a default-off *_ENABLED gate in front of a required capability."""
    assert main._truthy(val) is (not expected_decoupled)


# --- a raising pass must not stop the loop ------------------------------------

def test_a_raising_pass_does_not_kill_the_loop(monkeypatch):
    """A crash-loop here would be invisible from the main heartbeat. The loop has
    to survive its own failures; `exit_loop_health` is what notices it is not
    producing passes, because a thread can be alive and useless."""
    calls = {"n": 0}

    def boom(**kwargs):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise KeyboardInterrupt      # break the infinite loop under test
        raise RuntimeError("pass exploded")

    recorded: list[float] = []
    import src.runtime.exit_loop_health as h
    import src.runtime.order_monitor as om
    monkeypatch.setattr(om, "run_exit_evaluation_tick", boom)
    monkeypatch.setattr(h, "record_pass", lambda ms, **_: recorded.append(ms))
    monkeypatch.setattr(h, "write_state_file", lambda *a, **k: None)
    monkeypatch.setattr(main, "_build_monitor_ohlcv_fetcher", lambda s: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    with pytest.raises(KeyboardInterrupt):
        main._exit_loop({})

    assert calls["n"] == 3, "loop stopped early on a raising pass"
    # Each failed pass still recorded — a pass that ran and failed is not a pass
    # that never happened, and conflating them would hide a crash-loop.
    assert len(recorded) == 2


def test_record_pass_is_called_after_the_pass_returns(monkeypatch):
    """Order matters: recording BEFORE would make a hanging pass refresh liveness,
    which is the silent wedge this whole design exists to avoid."""
    order: list[str] = []
    import src.runtime.exit_loop_health as h
    import src.runtime.order_monitor as om

    def slow_pass(**kwargs):
        order.append("pass")
        raise KeyboardInterrupt        # stop after one iteration

    monkeypatch.setattr(om, "run_exit_evaluation_tick", slow_pass)
    monkeypatch.setattr(h, "record_pass", lambda ms, **_: order.append("record"))
    monkeypatch.setattr(h, "write_state_file", lambda *a, **k: None)
    monkeypatch.setattr(main, "_build_monitor_ohlcv_fetcher", lambda s: None)

    with pytest.raises(KeyboardInterrupt):
        main._exit_loop({})
    # The pass raised, so no record for it — and crucially `record` never precedes
    # `pass`.
    assert order == ["pass"]


def test_an_overrunning_pass_does_not_sleep(monkeypatch):
    """If a pass overruns the interval the next starts immediately: the cadence is
    a floor on frequency, not a schedule to catch up on. Piling passes onto one
    shared IB socket is how the June 2026 wedges started."""
    slept: list[float] = []
    import src.runtime.exit_loop_health as h
    import src.runtime.order_monitor as om
    n = {"i": 0}

    def long_pass(**kwargs):
        n["i"] += 1
        if n["i"] >= 2:
            raise KeyboardInterrupt
        # simulate a 60s pass against a 30s interval
        t0 = main.time.monotonic()
        monkeypatch.setattr(main.time, "monotonic", lambda: t0 + 60.0)

    monkeypatch.setattr(om, "run_exit_evaluation_tick", long_pass)
    monkeypatch.setattr(h, "record_pass", lambda ms, **_: None)
    monkeypatch.setattr(h, "write_state_file", lambda *a, **k: None)
    monkeypatch.setattr(main, "_build_monitor_ohlcv_fetcher", lambda s: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: slept.append(s))

    with pytest.raises(KeyboardInterrupt):
        main._exit_loop({})
    assert slept == [], f"slept after an overrunning pass: {slept}"


def test_start_exit_loop_never_raises_into_startup(monkeypatch):
    """A failure to start must not take the trader down with it."""
    def bad_thread(*a, **k):
        raise RuntimeError("cannot spawn")
    monkeypatch.setattr(main.threading, "Thread", bad_thread)
    main._start_exit_loop({})    # must not raise
