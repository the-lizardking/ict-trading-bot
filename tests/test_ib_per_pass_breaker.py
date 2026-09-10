"""The per-pass IB circuit breaker — R2.

Tier-2, operator-approved as `DEC-20260910-EXIT-EVAL-60S-REMEDY`
(chosen `r2_only`, 2026-09-10T07:52Z) on
`WO-20260910-ROOT-CAUSE-THE-EXIT-EVAL-60S-BREACHES`. The evidence it acts on is
`docs/claude/work/EXIT-EVAL-60S-ROOTCAUSE-2026-09-10.md`: a COMPLETE CENSUS of
all 1218 within-process 60s breaches shows every one is a slow PASS, and the
live residual is 10 of 14 post-fix breaches inside the 04:00-05:00Z IBKR reset
window, where each IB-routed package pays a serialized 29.0s pinned-thread
queue timeout so a pass costs `n_IB x 29s`.

⚠️ WHAT THESE TESTS CANNOT DO, SAID FIRST. A harness cannot reach a wedged IB
queue, so **none of this is evidence the breaker works on the fleet.** These
pin the DECISION — what trips it, what does not, what it skips, what it leaves
alone, and that the rollback restores the prior behaviour. The fleet half is
`OI-20260910-IB-PER-PASS-BREAKER-ARMED-AND-HAS-SKIPPED-NOTHING`, and its
criterion is an OBSERVED reset-window pass costing ~35s rather than n x 29s.

⚠️ AND THEY SAY NOTHING ABOUT R1 OR THE RESTART GAP. R1 was OFFERED and NOT
chosen; the restart-boundary tail (max 210.4s) is untouched by this change.
"""

from __future__ import annotations

import threading

import pandas as pd
import pytest

import src.main as main_module
from src.exchange import ib_connector


# --------------------------------------------------------------------------
# The signal itself: a queue timeout is DISTINCT from any other failure.
# --------------------------------------------------------------------------


def test_consume_queue_timeout_is_false_when_nothing_happened():
    """A thread that has observed no timeout reports none.

    The reassuring value is not fabricated anywhere else either: the flag is
    only ever raised inside the `_FutureTimeout` branch.
    """
    ib_connector.consume_queue_timeout()  # drain anything a prior test left
    assert ib_connector.consume_queue_timeout() is False


def test_consume_queue_timeout_reads_and_clears():
    """True exactly once per observed timeout.

    Read-and-clear rather than read-only so a flag raised in pass N can never
    trip the breaker in pass N+1 — a stale trip would skip IB packages on a
    HEALTHY pass, which is a real degradation and is not what was approved.
    """
    ib_connector.consume_queue_timeout()
    ib_connector._mark_queue_timeout()
    assert ib_connector.consume_queue_timeout() is True
    assert ib_connector.consume_queue_timeout() is False


def test_the_flag_is_thread_local_so_another_thread_cannot_trip_this_pass():
    """The exit pass must see ITS OWN timeout, not any timeout in the process.

    A module-level counter would also be incremented by the tick thread, so a
    pass whose own IB fetch SUCCEEDED could still trip. That is a widening of
    the approved change, which is scoped to "once ONE IB-routed fetch IN THAT
    PASS has returned a queue timeout".
    """
    ib_connector.consume_queue_timeout()
    other = threading.Thread(target=ib_connector._mark_queue_timeout)
    other.start()
    other.join()
    assert ib_connector.consume_queue_timeout() is False


# --------------------------------------------------------------------------
# The rollback knob.
# --------------------------------------------------------------------------


def test_breaker_is_armed_by_default(monkeypatch):
    monkeypatch.delenv("EXIT_LOOP_IB_BREAKER_DISABLED", raising=False)
    assert main_module.ib_breaker_armed() is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_explicit_truthy_disarms(monkeypatch, value):
    monkeypatch.setenv("EXIT_LOOP_IB_BREAKER_DISABLED", value)
    assert main_module.ib_breaker_armed() is False


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "ture", "disabled"])
def test_anything_else_leaves_it_armed(monkeypatch, value):
    """A typo must not silently restore the behaviour the operator approved
    removing. `disabled` and `ture` are in here deliberately: both LOOK like a
    rollback and neither is one."""
    monkeypatch.setenv("EXIT_LOOP_IB_BREAKER_DISABLED", value)
    assert main_module.ib_breaker_armed() is True


# --------------------------------------------------------------------------
# The breaker, exercised through the real fetcher closure.
# --------------------------------------------------------------------------


class _FakeIB(ib_connector.IBMarketData):
    """An `IBMarketData` for `isinstance`, built without touching a socket."""

    def __init__(self):  # noqa: D107 — deliberately does not call super()
        self._client = None


class _FakeBybit:
    pass


@pytest.fixture
def harness(monkeypatch):
    """Route symbols to fake connectors and record every `fetch_candles` call.

    `MES` is IB-routed, `BTCUSDT` is not. `fetch_candles` returns a frame,
    except for a symbol in `timeout_on` (returns None AND raises the
    pinned-thread queue-timeout flag) or in `none_on` (returns None and raises
    NOTHING — an ordinary failure). Those two sets are what let the tests below
    tell the breaker's real trigger from a bare `None`.
    """
    ib_connector.consume_queue_timeout()
    monkeypatch.delenv("EXIT_LOOP_IB_BREAKER_DISABLED", raising=False)

    ib, bybit = _FakeIB(), _FakeBybit()
    state = {"calls": [], "timeout_on": set(), "none_on": set()}

    monkeypatch.setattr(
        "src.runtime.market_data.connector_for_symbol",
        lambda symbol, settings: ib if symbol in {"MES", "MGC", "MHG"} else bybit,
    )

    def _fake_fetch_candles(symbol, timeframe, **kwargs):
        state["calls"].append(symbol)
        if symbol in state["timeout_on"]:
            ib_connector._mark_queue_timeout()
            return None
        if symbol in state["none_on"]:
            return None
        return pd.DataFrame({"close": [1.0]})

    monkeypatch.setattr(
        "src.runtime.market_data.fetch_candles", _fake_fetch_candles
    )
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config", lambda: {}, raising=False
    )
    return state


def test_one_queue_timeout_skips_the_remaining_ib_packages_in_that_pass(harness):
    """The whole point: the second and third IB packages are not even asked.

    On the measured 2026-09-10T04:21 event three IB packages each paid 29.0s.
    Here MES times out and MGC/MHG never reach `fetch_candles` at all — they
    receive the same `candles=None` they would have received 29s later, which
    is why this bounds a COST and decides nothing.
    """
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    harness["timeout_on"].add("MES")

    assert fetch("MES", "5m") is None
    assert fetch("MGC", "5m") is None
    assert fetch("MHG", "5m") is None

    assert harness["calls"] == ["MES"]


def test_a_non_ib_symbol_is_never_skipped_by_the_ib_breaker(harness):
    """Bybit does not share the pinned IB worker, so its fetch is unaffected.

    This is the property that keeps the real-money Bybit path out of the blast
    radius — the same separation `IB_FETCH_TIMEOUT_S` exists to preserve.
    """
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    harness["timeout_on"].add("MES")

    assert fetch("MES", "5m") is None
    assert fetch("BTCUSDT", "5m") is not None
    assert harness["calls"] == ["MES", "BTCUSDT"]


def test_an_ordinary_none_does_not_trip_the_breaker(harness):
    """A gateway outage, an unknown symbol and a venue-side reqHistoricalData
    timeout all return `None` and none of them is queue congestion.

    A breaker keyed on `None` alone would trip on all three. Their retry cost is
    not 29s and their remedy is different, so collapsing them would be both a
    widening of the approved change and the "Collapsed states" defect.
    """
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    harness["none_on"].update({"MES", "MGC"})

    assert fetch("MES", "5m") is None
    # MGC is still ASKED — the breaker did not trip on MES's bare None.
    assert fetch("MGC", "5m") is None
    assert harness["calls"] == ["MES", "MGC"]


def test_the_breaker_is_per_pass_and_a_new_pass_starts_closed(harness):
    """`_build_monitor_ohlcv_fetcher` is called fresh each pass, so the state
    resets by construction rather than by anybody remembering to clear it."""
    first = main_module._build_monitor_ohlcv_fetcher({})
    harness["timeout_on"].add("MES")
    first("MES", "5m")
    first("MGC", "5m")
    assert harness["calls"] == ["MES"]

    harness["timeout_on"].clear()
    second = main_module._build_monitor_ohlcv_fetcher({})
    assert second("MGC", "5m") is not None
    assert harness["calls"] == ["MES", "MGC"]


def test_the_rollback_restores_the_prior_behaviour(monkeypatch, harness):
    """`EXIT_LOOP_IB_BREAKER_DISABLED` truthy: every IB package pays its own
    queue timeout again — i.e. every one of them is still ASKED."""
    monkeypatch.setenv("EXIT_LOOP_IB_BREAKER_DISABLED", "1")
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    harness["timeout_on"].add("MES")

    assert fetch("MES", "5m") is None
    assert fetch("MGC", "5m") is not None
    assert fetch("MHG", "5m") is not None
    assert harness["calls"] == ["MES", "MGC", "MHG"]


def test_a_disarmed_window_leaves_no_stale_flag_for_a_later_re_arm(monkeypatch, harness):
    """Re-arming mid-process must not trip on a timeout observed while disarmed.

    The flag is consumed whether or not the breaker is armed; `armed` gates only
    what is DONE with it. Without that, the first IB fetch after a re-arm would
    trip on stale evidence and skip a healthy pass.
    """
    monkeypatch.setenv("EXIT_LOOP_IB_BREAKER_DISABLED", "1")
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    harness["timeout_on"].add("MES")
    fetch("MES", "5m")

    monkeypatch.delenv("EXIT_LOOP_IB_BREAKER_DISABLED", raising=False)
    harness["timeout_on"].clear()
    assert fetch("MGC", "5m") is not None
    assert fetch("MHG", "5m") is not None
    assert harness["calls"] == ["MES", "MGC", "MHG"]


# --------------------------------------------------------------------------
# The DURABLE record. Without it a short reset-window pass is unattributable.
# --------------------------------------------------------------------------


def test_the_report_names_all_four_states_apart(harness, monkeypatch):
    """`pass_ms` alone cannot tell three of these apart, which is the point.

    A pass that skipped two IB packages and a pass that met a healthy queue both
    come back fast; reporting the first from the second would be exactly the
    unprovenanced-diagnostic defect `diagnostic-provenance-guard` exists for.
    """
    # (a) no IB package in the pass at all — NOT "armed and skipped zero"
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    fetch("BTCUSDT", "5m")
    assert main_module._ib_breaker_report(fetch)["ib_breaker"] == {
        "state": "no_ib_packages", "tripped": False, "skipped": 0,
    }

    # (b) armed, queue healthy — this is the DENOMINATOR. Without a row saying
    # `armed / skipped 0`, a run of zeros is indistinguishable from the field
    # never having been written.
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    fetch("MES", "5m")
    assert main_module._ib_breaker_report(fetch)["ib_breaker"] == {
        "state": "armed", "tripped": False, "skipped": 0,
    }

    # (c) armed and it ACTED — the pass is short BECAUSE of it
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    harness["timeout_on"].add("MES")
    fetch("MES", "5m")
    fetch("MGC", "5m")
    fetch("MHG", "5m")
    assert main_module._ib_breaker_report(fetch)["ib_breaker"] == {
        "state": "armed", "tripped": True, "skipped": 2,
    }

    # (d) rolled back — a short pass says nothing about the breaker
    monkeypatch.setenv("EXIT_LOOP_IB_BREAKER_DISABLED", "1")
    fetch = main_module._build_monitor_ohlcv_fetcher({})
    fetch("MES", "5m")
    assert main_module._ib_breaker_report(fetch)["ib_breaker"]["state"] == "disabled"


def test_a_fetcher_carrying_no_breaker_state_reports_ABSENT_not_zero():
    """The fifth reading — *we did not look*. An annotation that could not be
    gathered must be absent from the row, never fabricated as a clean zero."""
    assert main_module._ib_breaker_report(None) == {}
    assert main_module._ib_breaker_report(lambda *a, **k: None) == {}


def test_the_report_never_raises_into_a_pass():
    """A soak annotation must not be able to fail an exit-evaluation pass."""
    class _Hostile:
        @property
        def ib_breaker(self):
            raise RuntimeError("boom")

    assert main_module._ib_breaker_report(_Hostile()) == {}


def test_record_pass_stamps_the_annotation_onto_the_soak_row(monkeypatch):
    """`extra_fields` rides through to the durable row, and its default writes a
    byte-identical row so every existing caller is unaffected."""
    from src.runtime import exit_loop_health

    written = []
    monkeypatch.setattr(
        "src.runtime.exit_interval_soak.record_exit_interval",
        lambda rec: written.append(rec),
    )

    exit_loop_health.record_pass(1234.0)
    exit_loop_health.record_pass(
        1234.0, extra_fields={"ib_breaker": {"state": "armed", "skipped": 2}}
    )

    assert "ib_breaker" not in written[0]
    assert written[1]["ib_breaker"] == {"state": "armed", "skipped": 2}
