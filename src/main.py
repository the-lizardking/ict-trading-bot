from __future__ import annotations

import logging
import os
import threading
import time

from dotenv import load_dotenv

from src.exchange.bybit_connector import BybitConnector
from src.runtime.heartbeat import write_heartbeat
from src.runtime.outcomes import Level, report
from src.runtime.pipeline import run_pipeline
from src.runtime.validation import build_settings_from_env, validate_startup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

from src.utils.log_redact import install_redacting_filter, suppress_httpx_logging  # noqa: E402
install_redacting_filter()   # redact tokens from every log record
suppress_httpx_logging()     # prevent httpx from emitting full Telegram URLs at INFO

logger = logging.getLogger("src.main")


class BybitExchangeAdapter:
    """Thin adapter so BybitConnector works with safe_place_order."""
    def __init__(self, connector: BybitConnector, symbol: str):
        self._connector = connector
        self._symbol = symbol

    def place_order(self, **order):
        side = order.get("side")
        qty = float(order.get("qty", 0))
        symbol = order.get("symbol", self._symbol)
        logger.info("BybitExchangeAdapter.place_order: %s %s %s", symbol, side, qty)
        if qty <= 0:
            raise ValueError(f"Invalid qty for adapter: {qty}")
        return self._connector.place_market_order(symbol, side, qty)


class DummyTelegramClient:
    def send_message(self, message: str):
        logger.info("DummyTelegramClient.send_message: %s", message)


class _AlertManagerAdapter:
    """Wraps AlertManager.send_alert() as send_message() for pipeline compatibility."""
    def __init__(self, alert_manager):
        self._am = alert_manager

    def send_message(self, message: str):
        self._am.send_alert(message)


def _build_telegram_client():
    """Use real Telegram client if credentials are present, else fall back to dummy."""
    try:
        from src.bot.alert_manager import AlertManager
        am = AlertManager()
        if am.enabled:
            return _AlertManagerAdapter(am)
    except Exception as exc:
        logger.warning("Could not initialise real Telegram client: %s", exc)
    return DummyTelegramClient()


def _build_exchange_adapter(settings: dict):
    exchange_name = settings.get("EXCHANGE", "bybit").lower()
    symbol = settings.get("SYMBOL", "BTCUSDT")

    # FIXED: read BYBIT_TESTNET directly; do not rely on MODE
    bybit_testnet_raw = str(os.environ.get("BYBIT_TESTNET", "true")).strip().lower()
    testnet = bybit_testnet_raw not in {"false", "0", "no"}

    logger.info("Exchange mode: exchange=%s testnet=%s symbol=%s", exchange_name, testnet, symbol)

    connector = BybitConnector(
        api_key=settings.get("BYBIT_API_KEY"),
        api_secret=settings.get("BYBIT_API_SECRET"),
        testnet=testnet,
    )
    return BybitExchangeAdapter(connector, symbol)


def _apply_per_account_leverage() -> None:
    """Pre-flight: set per-symbol leverage for every linear-perp account.

    PR 3 cutover (spot-margin → USDT-margined perpetuals). Bybit V5
    requires `/v5/position/set-leverage` to be called per (symbol,
    account) before placing linear orders; the value persists until
    explicitly changed. Idempotent on retCode=110043 (already set),
    so re-calling on every boot is safe.

    Iterates `config/accounts.yaml`:
      - skips accounts with `market_type` ≠ `linear`
      - skips accounts missing creds (resolve_credentials returns None)
      - reads `risk.leverage` (or `leverage`) from the account's YAML
      - reads the per-strategy symbols from `config/strategies.yaml`
        for the strategies that account is wired to
      - calls `client.set_leverage(symbol, leverage)` for each pair

    Best-effort — a failure on one account does not block the others
    or block boot. A retCode-110043 (already set) is treated as
    success; everything else is logged as a warning. The trader loop
    will surface the consequence (an immediate Bybit order rejection
    with a clear retMsg) if a real leverage problem is left
    unresolved.
    """
    try:
        from src.units.accounts import load_accounts
        from src.units.accounts.clients import bybit_client_for
        from src.units.strategies import load_strategy_config
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_leverage pre-flight: import failed (%s)", exc)
        return

    try:
        accounts = load_accounts()
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_leverage pre-flight: load_accounts failed (%s)", exc)
        return

    try:
        strategies_cfg = load_strategy_config() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_leverage pre-flight: load_strategy_config failed (%s)", exc)
        strategies_cfg = {}

    for account in accounts:
        market_type = (getattr(account, "market_type", "spot") or "spot").lower()
        if market_type != "linear":
            continue

        if getattr(account, "exchange", "").lower() != "bybit":
            # Future non-bybit derivatives have their own leverage
            # primitives; this helper is bybit-specific.
            continue

        leverage = _resolve_account_leverage(account)
        if leverage <= 0:
            logger.warning(
                "set_leverage pre-flight: account=%s has market_type=linear "
                "but no usable `leverage` config — skipping",
                account.name,
            )
            continue

        # Use the SAME pybit HTTP client factory that order placement uses
        # (src/units/accounts/clients.py::bybit_client_for). Three prior
        # implementations of set-leverage all returned retCode=10003 from
        # the SAME credentials that successfully placed orders via this
        # pybit client (see FU-20260510-005):
        #   * PR #781 — ccxt high-level `set_leverage`
        #   * PR #782 — ccxt private_post_v5_position_set_leverage
        #   * PR #903 — hand-rolled direct V5 signed POST in BybitConnector
        # Root cause was never identified in the signing math (all three
        # passed unit tests against Bybit's documented spec), but pybit's
        # internal V5 signer demonstrably DOES work on the same key for
        # set-leverage. Routing through it eliminates the parallel auth
        # path and the every-boot WARNING.
        account_cfg = {
            "api_key_env": getattr(account, "api_key_env", ""),
            "exchange": "bybit",
            "env_path": getattr(account, "env_path", ""),
            "demo": getattr(account, "demo", False),
        }
        try:
            client = bybit_client_for(account_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "set_leverage pre-flight: client init failed for %s (%s)",
                account.name, exc,
            )
            continue
        if client is None:
            logger.warning(
                "set_leverage pre-flight: account=%s creds not resolvable "
                "(env vars unset) — skipping",
                account.name,
            )
            continue

        symbols = _symbols_for_account(account, strategies_cfg)
        if not symbols:
            logger.warning(
                "set_leverage pre-flight: account=%s has no symbols (no "
                "strategies or empty symbol lists) — skipping",
                account.name,
            )
            continue

        for symbol in symbols:
            try:
                resp = client.set_leverage(
                    category="linear",
                    symbol=symbol,
                    buyLeverage=str(int(leverage)),
                    sellLeverage=str(int(leverage)),
                ) or {}
                ret_code = resp.get("retCode")
                # Bybit V5: 0 = newly set; 110043 = "leverage not modified"
                # (already at the target value) — idempotent success.
                if ret_code in (0, "0", 110043, "110043"):
                    logger.info(
                        "set_leverage pre-flight: account=%s symbol=%s x%d ok "
                        "(retCode=%s)",
                        account.name, symbol, leverage, ret_code,
                    )
                    continue
                logger.warning(
                    "set_leverage pre-flight: account=%s symbol=%s x%d "
                    "rejected (retCode=%s retMsg=%s) — order placement may "
                    "be rejected until leverage is set",
                    account.name, symbol, leverage,
                    ret_code, resp.get("retMsg"),
                )
            except Exception as exc:  # noqa: BLE001
                # pybit raises on retCode != 0 for some endpoints; absorb
                # 110043 here too (same idempotent-already-set semantics).
                msg = str(exc)
                if "110043" in msg or "leverage not modified" in msg.lower():
                    logger.info(
                        "set_leverage pre-flight: account=%s symbol=%s x%d "
                        "already set (retCode=110043, idempotent)",
                        account.name, symbol, leverage,
                    )
                    continue
                logger.warning(
                    "set_leverage pre-flight: account=%s symbol=%s x%d "
                    "failed (%s) — order placement may be rejected until "
                    "leverage is set",
                    account.name, symbol, leverage, exc,
                )


def _resolve_account_leverage(account) -> int:
    """Pull integer leverage from an account's YAML config.

    Only source today: ``risk.leverage`` on the account's RiskManager
    (groups it with other risk caps). The ``TradingAccount`` object
    itself carries no leverage field, so there is no separate
    top-level fallback. Returns 0 when unset or the value can't be
    coerced to a positive int.
    """
    candidates = []
    rm = getattr(account, "risk_manager", None)
    if rm is not None:
        candidates.append(getattr(rm, "leverage", None))
    # ``account`` is a TradingAccount; doesn't carry leverage today.
    # Fall through to RiskManager attribute which we'll wire in
    # accounts.yaml as ``risk.leverage``.
    for raw in candidates:
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _symbols_for_account(account, strategies_cfg: dict) -> list:
    """Return the union of symbols the account's strategies trade.

    Reads ``strategies_cfg`` (from ``load_strategy_config()``); each
    strategy entry carries a ``symbols`` list. The account's
    ``strategies`` attribute lists the strategy names that account is
    wired to. We take the union.
    """
    strat_names = getattr(account, "strategies", None) or []
    symbols = []
    for name in strat_names:
        cfg = (strategies_cfg or {}).get(name) or {}
        for sym in (cfg.get("symbols") or []):
            if sym and sym not in symbols:
                symbols.append(str(sym))
    return symbols


def _tick_hook(name: str):
    """Per-hook timing wrapper for the trader tick, with a NO-OP fallback.

    Returns ``tick_cost.hook(name)``, or ``contextlib.nullcontext()`` if the
    measurement module cannot be imported. The fallback is the whole point: this
    wraps the live trading loop, and an instrumentation import error must never
    be able to stop a tick from running. It does NOT swallow the wrapped body's
    exceptions — each hook keeps its own existing handler, and the duration is
    recorded either way, so a hook that burns time and then throws still appears
    in the split instead of vanishing from it.
    """
    try:
        from src.runtime.tick_cost import hook
        return hook(name)
    except Exception:  # noqa: BLE001
        import contextlib
        return contextlib.nullcontext()


def _truthy(value) -> bool:
    """Env-flag truthiness, matching the repo's other kill-switches."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def exit_loop_interval_seconds() -> float:
    """Cadence between exit-evaluation passes.

    Default 30s against a measured ~28s pass, i.e. effectively back-to-back with a
    little slack. An unparseable or non-positive value falls back to the default
    rather than pausing the loop — a typo must not silently stop exit evaluation,
    which is the one thing this loop exists to do (the EXPOSURE_SOAK_SECONDS
    fail-ON discipline). Pausing is not offered as a value at all; the rollback is
    EXIT_LOOP_DECOUPLE_DISABLED, which puts the work back on the tick rather than
    dropping it.
    """
    try:
        v = float(os.environ.get("EXIT_LOOP_INTERVAL_SECONDS", "") or 30.0)
    except (TypeError, ValueError):
        return 30.0
    return v if v > 0 else 30.0


def _ib_breaker_report(fetcher) -> dict:
    """What the per-pass IB breaker did, for the durable exit-interval soak row.

    FOUR STATES, NEVER COLLAPSED — the point of the field is to make a fast pass
    ATTRIBUTABLE, and three of the four look identical in `pass_ms` alone:

      * ``no_ib_packages`` — the pass fetched nothing IB-routed, so the breaker
        had nothing to act on. NOT the same as it having acted and skipped zero.
      * ``armed`` with ``skipped: 0`` — it was live and the queue was healthy.
        This is the ordinary case and it is the DENOMINATOR: without it, a run of
        zeros cannot be told from the field never being written.
      * ``armed`` with ``skipped: n`` — it acted; the pass is short BECAUSE of it.
      * ``disabled`` — rolled back via ``EXIT_LOOP_IB_BREAKER_DISABLED``, so a
        short pass says nothing about the breaker.

    Returns ``{}`` when the fetcher carries no breaker state at all (a caller
    that built its own fetcher, or a future refactor) — the field is then ABSENT
    from the row, which is the honest fifth reading: *we did not look*. Never
    raises: a soak annotation must not be able to fail a pass.
    """
    try:
        state = getattr(fetcher, "ib_breaker", None)
        if not isinstance(state, dict):
            return {}
        armed = state.get("armed")
        if armed is None:
            name = "no_ib_packages"
        elif armed:
            name = "armed"
        else:
            name = "disabled"
        return {"ib_breaker": {
            "state": name,
            "tripped": bool(state.get("tripped")),
            "skipped": int(state.get("skipped") or 0),
        }}
    except Exception:  # noqa: BLE001
        return {}


def _ib_queue_timeout_seconds() -> float:
    """The pinned-thread queue wait, for the breaker's log lines ONLY.

    Read from `ib_connector` so the number printed is the one actually enforced
    rather than a second copy free to drift from it. Falls back to the module's
    own documented default if the import fails — a log line must never raise
    into a pass.
    """
    try:
        from src.exchange.ib_connector import _IB_FETCH_QUEUE_TIMEOUT_S
        return float(_IB_FETCH_QUEUE_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return 29.0


def ib_breaker_armed() -> bool:
    """Is the per-pass IB circuit breaker armed? (R2, Tier-2, 2026-09-10)

    ARMED BY DEFAULT. ``EXIT_LOOP_IB_BREAKER_DISABLED`` truthy is the sanctioned
    rollback -- every IB package pays its own queue timeout again, byte-for-byte
    the pre-2026-09-10 behaviour. One env flip plus a restart, no redeploy, the
    same shape as ``EXIT_LOOP_DECOUPLE_DISABLED``.

    A default-OFF kill-switch over an ON capability, NOT a default-off
    ``*_ENABLED`` gate in front of a required one -- the distinction the Prime
    Directive turns on. Read at call time so the flip needs no code change, and
    only an explicit truthy value disarms: anything else (a typo, an empty
    string, an unset variable) leaves it armed, because a mistyped rollback must
    not silently restore the behaviour the operator approved removing.
    """
    return not _truthy(os.environ.get("EXIT_LOOP_IB_BREAKER_DISABLED"))


def _exit_loop(settings: dict) -> None:
    """Evaluate every open package's exit on our own cadence, forever.

    Deliberately structured so that NOTHING can stop the loop permanently:

      * each pass is individually wrapped, so a raising pass is logged and the next
        one still runs. A crash-loop here would be invisible from the main
        heartbeat, which is why the pass count is what `exit_loop_health` watches
        rather than the thread being alive — a thread can be alive and useless.
      * `record_pass` is called AFTER the pass returns, so a pass that hangs leaves
        liveness ageing and the latched alert fires. A pass that hung while still
        refreshing liveness would be the silent wedge this whole design avoids.
      * if a pass overruns the interval, the next starts immediately rather than
        queueing — the cadence is a floor on frequency, not a schedule to catch up
        on. Piling up passes on one shared IB socket is how the June 2026 wedges
        started.
    """
    from src.runtime.exit_loop_health import record_pass, write_state_file
    from src.runtime.order_monitor import run_exit_evaluation_tick

    while True:
        started = time.monotonic()
        fetcher = _build_monitor_ohlcv_fetcher(settings)
        try:
            run_exit_evaluation_tick(ohlcv_fetcher=fetcher)
        except Exception:  # noqa: BLE001
            logger.exception("exit_loop: pass failed")
        elapsed_ms = (time.monotonic() - started) * 1000.0
        record_pass(elapsed_ms, extra_fields=_ib_breaker_report(fetcher))
        write_state_file()
        slack = exit_loop_interval_seconds() - (elapsed_ms / 1000.0)
        if slack > 0:
            time.sleep(slack)


def _start_exit_loop(settings: dict) -> None:
    """Start the exit loop as a daemon thread. Never raises into startup.

    Daemon so it can never block shutdown. If the thread fails to start at all,
    that is logged AND left visible: `exit_loop_health` stays at `never_ran`, the
    tick's check does not alert on that state by design, but the diag surface shows
    zero passes — so the failure is legible rather than mistaken for health.
    """
    try:
        t = threading.Thread(
            target=_exit_loop, args=(settings,),
            name="exit-evaluation-loop", daemon=True,
        )
        t.start()
    except Exception:  # noqa: BLE001
        logger.exception("exit_loop: FAILED TO START — exits ride nothing now")


def _build_monitor_ohlcv_fetcher(settings: dict):
    """Build the ``(symbol, timeframe) -> DataFrame | None`` fetcher
    that ``run_monitor_tick`` needs to feed strategy ``monitor()``
    hooks fresh candles.

    Without this the monitor loop calls every strategy with
    ``candles_df=None`` and the strategies short-circuit at their
    first guard, never producing TP / SL / VWAP-cross / time-decay
    close verdicts. The bot then leans on the +30 min stuck-strategy
    watchdog and the borrow reconciler as a de-facto exit, which is
    what surfaced the recurring vwap/BTCUSDT stuck cascades (PR #566).

    Built fresh per tick so the connector matches what
    ``pipeline._build_vwap_signal`` / ``_build_turtle_soup_signal``
    do for signal generation. Returns ``None`` instead of raising
    on init failure so the caller's ``run_monitor_tick`` falls back
    to the prior no-change behaviour.
    """
    from src.runtime.market_data import fetch_candles, connector_for_symbol

    # Per-symbol connector cache. The monitor must route each symbol to the
    # SAME exchange the signal builders use (BTCUSDT → Bybit; MES/MGC/MHG →
    # IBKR, per config/instruments.yaml). A single default client asked Bybit
    # for the IB futures ("bybit does not have market symbol MHG"), so open
    # IB-futures positions got candles=None and the strategy monitor()
    # short-circuited — bot-side TP/SL/time-decay exits never ran (the
    # broker-side IBKR bracket still held the position). connector_for_symbol
    # falls back to the default EXCHANGE for unprofiled symbols, so BTCUSDT
    # routing is unchanged. Cached so the (possibly IBKR) client is built at
    # most once per symbol per fetcher build.
    _connector_cache: dict = {}

    def _connector_for(symbol):
        if symbol in _connector_cache:
            return _connector_cache[symbol]
        try:
            client = connector_for_symbol(symbol, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("monitor: connector init failed for %s (%s)", symbol, exc)
            client = None
        _connector_cache[symbol] = client
        return client

    # Per-strategy default timeframes — fallback when a package's meta
    # JSON lacks ``timeframe``. Pre-2026-05-09 every package row was
    # written without the key, so the closure would short-circuit to
    # ``None`` and ``monitor()`` never received candles. Loading the
    # map here once per fetcher build keeps the hot path cheap.
    # Best-effort: a config-load failure leaves the map empty, which
    # means falsy-timeframe packages still short-circuit (no regression
    # vs the prior contract).
    try:
        from src.units.strategies import load_strategy_config
        _per_strategy_tf = {
            name: (cfg or {}).get("timeframe")
            for name, cfg in (load_strategy_config() or {}).items()
        }
    except Exception:  # noqa: BLE001
        _per_strategy_tf = {}

    # --- The per-pass IB circuit breaker (R2, Tier-2, operator-approved
    # DEC-20260910-EXIT-EVAL-60S-REMEDY, chosen `r2_only`, 2026-09-10T07:52Z) --
    #
    # WHAT IT DOES. Once ONE IB-routed fetch in THIS pass has come back with a
    # pinned-thread queue timeout, the remaining IB-routed fetches in the SAME
    # pass are skipped and their callers get `candles=None`.
    #
    # WHY IT CHANGES NOTHING ABOUT ANY OUTCOME. IB market data is serialised on
    # ONE pinned worker (`_IB_FETCH_EXECUTOR`, max_workers=1), so while the
    # queue is congested every later fetch in the pass waits its own
    # `_IB_FETCH_QUEUE_TIMEOUT_S` (29.0s at the shipped default) and then
    # returns None anyway. A skipped package receives EXACTLY the `candles=None`
    # it would have received 29s later, and `order_monitor` already
    # short-circuits on it. This bounds the COST of an outcome that is already
    # determined; it does not decide anything.
    #
    # MEASURED, and this is the whole reason it exists
    # (docs/claude/work/EXIT-EVAL-60S-ROOTCAUSE-2026-09-10.md, complete census
    # of all 1218 within-process breaching intervals read 2026-09-10T06:13Z from
    # /api/bot/exit-interval/soak): every within-process 60s breach is a slow
    # PASS (1218/1218 have pass_ms > 30s), and the live residual is 10 of 14
    # post-fix breaches inside the 04:00-05:00Z IBKR reset window, where a pass
    # costs n_IB x 29s and crosses 60s at THREE packages. INFERRED from that
    # arithmetic: pass cost in the window falls from n x 29s to ~35s.
    #
    # WHY NOT A SHORTER TIMEOUT. BL-20260816's own criterion 2:
    # "do not optimise the interval into a MONITOR BLIND." Lowering
    # `IB_FETCH_QUEUE_TIMEOUT_S` would start discarding genuinely queued healthy
    # fetches. Neither timeout is touched here.
    #
    # THE COST, STATED RATHER THAN HIDDEN. During a TRANSIENT single-fetch queue
    # timeout that is not congestion, the breaker skips later IB packages that
    # might have succeeded; they get `candles=None` for one pass and are retried
    # on the next, ~30s later. That is a real, bounded degradation and is why
    # this is Tier-2 rather than a session's own call.
    #
    # SCOPE. The state lives in THIS closure, and `_build_monitor_ohlcv_fetcher`
    # is called fresh for each pass, so "per pass" is structural rather than
    # something a caller must remember to reset.
    # `armed` starts None and is set on the first IB-ROUTED fetch, so it stays
    # None when the pass had no IB package at all. Those are different facts and
    # the soak row below keeps them apart: "armed and nothing to skip" is not
    # "there was nothing IB-routed to skip in the first place", and neither is
    # "the operator has rolled it back".
    _breaker: dict = {"tripped": False, "skipped": 0, "armed": None}

    def _is_ib_routed(client) -> bool:
        """True when *client* is the IB market-data connector.

        Import guarded: if `src.exchange.ib_connector` cannot be imported then
        no IB connector can have been constructed either, so the honest answer
        is False and the breaker is simply inert.
        """
        try:
            from src.exchange.ib_connector import IBMarketData
        except Exception:  # noqa: BLE001
            return False
        return isinstance(client, IBMarketData)

    def _fetch(symbol, timeframe, strategy_name=None):
        if not symbol:
            return None
        if not timeframe and strategy_name:
            timeframe = _per_strategy_tf.get(strategy_name)
        if not timeframe:
            return None
        client = _connector_for(symbol)
        if client is None:
            return None

        # `ib_routed` is resolved whether or not the breaker is armed, and the
        # flag below is consumed either way, so a DISARMED window cannot leave a
        # stale timeout flag behind for a later re-arm to trip on. `armed` gates
        # only what we DO with it.
        armed = ib_breaker_armed()
        ib_routed = _is_ib_routed(client)
        if ib_routed:
            _breaker["armed"] = armed

        if armed and ib_routed and _breaker["tripped"]:
            _breaker["skipped"] += 1
            logger.warning(
                "monitor: IB breaker OPEN — skipping %s/%s this pass "
                "(the pinned IB queue already timed out once; this package "
                "gets the candles=None it would have got in %.1fs). "
                "Retried next pass. Skipped so far this pass: %d.",
                symbol, timeframe, _ib_queue_timeout_seconds(),
                _breaker["skipped"],
            )
            return None

        candles = fetch_candles(
            symbol, timeframe,
            settings=settings,
            exchange_client=client,
            limit=200,
        )

        if ib_routed:
            # Read-and-clear. This asks "did MY fetch just hit the pinned-thread
            # QUEUE timeout" — a distinct signal, not "did it return None". A
            # gateway outage, an unknown symbol and a venue-side
            # reqHistoricalData timeout all return None too, and none of them is
            # queue congestion; tripping on those would be a widening of what
            # was approved.
            try:
                from src.exchange.ib_connector import consume_queue_timeout
            except Exception:  # noqa: BLE001
                return candles
            timed_out = consume_queue_timeout()
            if timed_out and armed:
                _breaker["tripped"] = True
                logger.warning(
                    "monitor: IB breaker TRIPPED on %s/%s — the pinned IB "
                    "thread did not answer within %.1fs, so the remaining "
                    "IB-routed packages in THIS pass are skipped rather than "
                    "each paying the same wait. Rollback: "
                    "EXIT_LOOP_IB_BREAKER_DISABLED.",
                    symbol, timeframe, _ib_queue_timeout_seconds(),
                )

        return candles

    # The pass reads this back to stamp what the breaker DID onto the durable
    # per-pass soak row. Without it a fast reset-window pass is unattributable:
    # "the breaker skipped two packages" and "the queue simply was not congested
    # this pass" produce the same `pass_ms`, and reporting the first from the
    # second is the unprovenanced-diagnostic defect this repo has a guard for.
    # The two WARNING lines above are NOT a substitute — they reach the systemd
    # journal only, whose retention on this VM was measured at ~30 minutes.
    _fetch.ib_breaker = _breaker  # type: ignore[attr-defined]
    return _fetch


def _run_symbol_tick(settings: dict, exchange_client, telegram_client) -> dict:
    """Run the pipeline for a single symbol (the original run_one_tick body)."""
    result = run_pipeline(
        settings=settings,
        exchange_client=exchange_client,
        telegram_client=telegram_client,
    )
    logger.info("Tick result: %s", result)
    order_result = (result or {}).get("order_result") or {}
    status = order_result.get("status", "unknown")
    report(
        "pipeline_tick",
        status,
        level=Level.INFO,
        symbol=(result or {}).get("signal", {}).get("symbol"),
    )
    _drain_critical_alerts(telegram_client)
    return result


# Per-exchange default instrument when a configured account omits the
# ``symbols`` field in accounts.yaml. Keeps an account trading its natural
# instrument rather than nothing.
_EXCHANGE_DEFAULT_SYMBOL = {
    "bybit": "BTCUSDT",
    "interactive_brokers": "MES",
}


def _resolve_tick_symbols(settings: dict) -> list:
    """Symbols to run this tick — derived from configured accounts.

    ``config/accounts.yaml`` is the single source of truth: the tick loop
    trades the union of every *configured* account's ``symbols`` (falling
    back to the per-exchange default when an account omits the field),
    restricted to accounts that actually trade (an explicit
    ``strategies: []`` opts an account out; ``None`` / non-empty are
    included). So one process trades BTCUSDT (Bybit) and MES (IB) whenever
    those accounts are configured.

    There is intentionally **no enable flag**. Per the "one switch per
    account" rule, ``mode: live|dry_run`` is the only runtime gate — a
    ``dry_run`` account still generates signals (logged, never executed),
    and the symbol set never depends on a separate on/off env. The
    previous ``MULTI_SYMBOL_ENABLED`` env was a forbidden second gate and
    has been removed.

    Best-effort: the primary ``SYMBOL`` is always included, and any
    account-load failure falls back to ``[primary]`` so a config error can
    never empty the tick (defence-in-depth; preserves single-symbol
    behaviour).
    """
    primary = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    try:
        from src.units.accounts import load_accounts

        seen: set = set()
        out: list = []
        if primary:
            seen.add(primary)
            out.append(primary)
        for acct in load_accounts():
            if not getattr(acct, "configured", True):
                continue
            strategies = getattr(acct, "strategies", None)
            if strategies is not None and len(strategies) == 0:
                continue  # explicit opt-out — account trades nothing
            syms = list(getattr(acct, "symbols", None) or [])
            if not syms:
                default = _EXCHANGE_DEFAULT_SYMBOL.get(
                    str(getattr(acct, "exchange", "") or "").lower()
                )
                syms = [default] if default else []
            for s in syms:
                s = str(s).strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
        return out or [primary]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_tick_symbols: account-derived symbols failed (%s); "
            "single-symbol fallback to %s", exc, primary,
        )
        return [primary]


def _exchange_for_symbol(symbol: str):
    """Return the instrument's exchange (BTCUSDT→bybit, MES→IB) or None."""
    try:
        from src.core.coordinator import _instrument_exchange_for
        return _instrument_exchange_for(symbol)
    except Exception:  # noqa: BLE001
        return None


def _per_symbol_client(symbol: str, settings: dict):
    """Build the right market-data connector for *symbol* (None on failure)."""
    try:
        from src.runtime.market_data import connector_for_symbol
        return connector_for_symbol(symbol, settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_one_tick: connector build failed for %s: %s", symbol, exc)
        return None


def run_one_tick(settings: dict, exchange_client, telegram_client) -> dict:
    """Run a single pipeline tick across the configured symbol(s).

    Single-symbol (default) is byte-identical to the legacy behaviour.
    With multi-symbol enabled, each symbol runs its own pipeline pass with
    a per-symbol exchange + connector; a failure on one symbol (e.g. the IB
    Gateway being down for MES) is isolated and never aborts the others, so
    the live crypto path keeps trading regardless of the MES side.
    """
    symbols = _resolve_tick_symbols(settings)
    if len(symbols) <= 1:
        return _run_symbol_tick(settings, exchange_client, telegram_client)

    primary = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    results = {}
    for sym in symbols:
        per = dict(settings)
        per["SYMBOL"] = sym
        ex = _exchange_for_symbol(sym)
        if ex:
            per["EXCHANGE"] = ex
        client = exchange_client if sym == primary else _per_symbol_client(sym, per)
        try:
            results[sym] = _run_symbol_tick(per, client, telegram_client)
        except Exception:  # noqa: BLE001
            logger.exception("run_one_tick: symbol %s tick failed (isolated)", sym)
            results[sym] = {"error": "tick_failed", "symbol": sym}
    return {"multi_symbol": True, "results": results}


def _drain_critical_alerts(telegram_client) -> None:
    """Forward queued critical alerts to Telegram, then drain.

    The coordinator's circuit breaker (PR #741) and other callers push
    alerts onto the in-process queue at
    ``src.units.dashboards.alerts``; pre-this-PR the queue had no
    autonomous consumer, so ``level="critical"`` items (e.g.
    "Account auto-paused after N consecutive exchange rejections")
    only surfaced when the operator manually issued ``/alerts`` on
    Telegram. The 2026-05-10 incident chain (operator missed an
    8-hour VWAP silence; circuit-breaker behaviour ambiguous because
    the would-be alert was queued but never sent) was rooted in this
    silent queue.

    Drain on every tick so operator notification latency is bounded
    by ``TICK_INTERVAL_SECONDS``. Best-effort — never let a
    notification failure break the trader loop.
    """
    try:
        from src.units.dashboards.alerts import pop_alerts
        for alert in pop_alerts():
            if str(alert.get("level", "")).lower() != "critical":
                continue
            source = alert.get("source") or "unknown"
            msg = alert.get("message") or ""
            try:
                telegram_client.send_message(f"[CRITICAL][{source}] {msg}")
            except Exception:  # noqa: BLE001
                logger.exception("alert_drainer: telegram send failed")
    except Exception:  # noqa: BLE001
        logger.exception("alert_drainer: pop_alerts failed")


def main() -> None:
    load_dotenv()
    settings = build_settings_from_env()

    validate_startup()

    # D-3: enable WAL on the trade journal so the pipeline writer, order
    # monitor reader, dashboard API, and diag relay can run concurrently
    # without "database is locked" contention. Persistent at the file
    # level — idempotent on every boot. Best-effort; never blocks start.
    try:
        from src.utils.db_init import enable_wal_mode
        enable_wal_mode()
    except Exception as exc:  # noqa: BLE001
        logger.warning("WAL enable skipped: %s", exc)

    # Operator directive 2026-05-03 — dry/live mode is no longer in env.
    # Per-account ``mode: live | dry_run`` in config/accounts.yaml is the
    # only toggle (see RiskManager.dry_run). Startup logs only report
    # exchange / symbol / testnet — the mode mix is account-scoped.
    logger.info(
        "Startup validation passed. exchange=%s bybit_testnet=%s symbol=%s",
        settings.get("exchange"),
        os.environ.get("BYBIT_TESTNET"),
        settings.get("symbol"),
    )

    # BUG-033: ping the operator on duplicate per-account API keys. Doesn't
    # block startup — per CLAUDE.md the trader runs autonomously and the
    # per-account risk caps bound the blast radius.
    try:
        from src.units.accounts import load_accounts
        from src.units.accounts.dup_key_check import warn_on_duplicate_keys
        warn_on_duplicate_keys(load_accounts())
    except Exception as exc:  # noqa: BLE001
        logger.warning("dup-key check skipped: %s", exc)

    # S-021: log open packages per strategy on every startup so the operator
    # can see at a glance that monitoring will resume (BUG-048 observability gap).
    try:
        from src.runtime.boot_audit import report_open_packages_on_boot
        report_open_packages_on_boot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_audit skipped: %s", exc)

    # Sprint A-3: compare journal open rows against live Bybit positions.
    # Ghost rows (journal open, Bybit flat) get a Telegram alert immediately
    # on startup — before the first tick — so the operator can investigate.
    try:
        from src.runtime.boot_audit import reconcile_journal_vs_exchange_on_boot
        reconcile_journal_vs_exchange_on_boot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("boot_reconcile skipped: %s", exc)

    # S-PERSIST-CANON: snapshot the active strategies.yaml into the
    # (previously dead) trade_journal.db::strategy_versions table so the
    # Data Explorer carries an in-DB strategy-config version history.
    try:
        from src.runtime.boot_audit import snapshot_strategy_versions_on_boot
        snapshot_strategy_versions_on_boot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("strategy_version snapshot skipped: %s", exc)

    # PR 3 cutover: set per-symbol leverage on every linear-perp account
    # before the first tick. Best-effort; logs warnings on failure and
    # never blocks boot. Idempotent on Bybit's retCode=110043 ("leverage
    # not modified") so re-calling on every restart is normal.
    _apply_per_account_leverage()

    exchange_client = _build_exchange_adapter(settings)
    telegram_client = _build_telegram_client()

    loop = str(os.environ.get("LOOP", "true")).strip().lower() not in {"false", "0", "no"}
    # 2026-05-08 operator directive: re-evaluate every minute, on
    # 5-min candles. Strategies are unchanged — they still operate on
    # 5-min bars — but a 1-min tick gives up to 4 min faster reaction
    # to a fresh candle close. Override per environment via
    # TICK_INTERVAL_SECONDS in the systemd unit / .env if a slower
    # cadence is needed (e.g. backtests, reduced API budget).
    interval = int(os.environ.get("TICK_INTERVAL_SECONDS", "60"))

    if not loop:
        logger.info("LOOP=false: running single tick.")
        run_one_tick(settings, exchange_client, telegram_client)
        return

    # ------------------------------------------------------------------ exit loop
    # THE DECOUPLE (Tier-2, operator-approved 2026-08-12; evidence
    # docs/research/M20-exit-monitor-decouple-evidence-2026-08-10.md § 4d).
    #
    # Exit evaluation used to ride this tick, so a live trade waited a mean 104s /
    # peak 125s between evaluations against the operator's 60s ask. Measurement
    # (n=7, all 14 monitor children summing to 99.6% of the parent) split the
    # monitor into two comparable halves: the exit loop at 24.3s mean / 28.2s max,
    # and 24.8s of reconcilers that answer "has the journal caught up with the
    # broker?" rather than "should this trade exit now?". Only the first half is
    # what the 60s ask is about, so only the first half moves.
    #
    # Whole-monitor on a thread would clear 60s by 3.20s (5.3%, on a max over seven
    # ticks). This half alone clears it by 31.78s (53%).
    #
    # ROLLBACK IS ONE ENV FLIP, NO REDEPLOY: EXIT_LOOP_DECOUPLE_DISABLED truthy →
    # no thread starts and the tick calls run_monitor_tick (both halves inline,
    # byte-for-byte today's behaviour). Default-OFF kill-switch over an ON
    # capability, the REGIME_ROUTER_DISABLED shape — NOT a default-off *_ENABLED
    # gate in front of a required capability (Prime Directive).
    decoupled = not _truthy(os.environ.get("EXIT_LOOP_DECOUPLE_DISABLED"))
    if decoupled:
        _start_exit_loop(settings)
        logger.info("exit-evaluation loop DECOUPLED onto its own thread")
    else:
        logger.warning(
            "EXIT_LOOP_DECOUPLE_DISABLED set — exit evaluation rides the main "
            "tick (pre-2026-08-12 behaviour); the 60s ask is NOT met in this mode"
        )

    logger.info("Starting continuous loop. TICK_INTERVAL_SECONDS=%s", interval)
    tick_count = 0
    last_tick_status = "starting"
    while True:
        tick_count += 1
        # Refresh the heartbeat at tick-START, before any work. The IBKR
        # restart-loop incident (2026-06-05) showed why "write only after
        # a successful tick" can starve liveness: a slow tick (e.g. a
        # logged-out IB Gateway making a request hang) holds the loop past
        # the watchdog's stale threshold, the watchdog autoheals (kills)
        # the trader before it ever reaches the post-tick write, and the
        # process never gets to refresh the heartbeat → a perpetual
        # restart loop. Stamping the heartbeat first means a tick that is
        # merely *slow* (now bounded — every IB call has a hard timeout)
        # still proves liveness; a genuine hang that outlives even this is
        # the only thing that can now stall the beat. The post-tick write
        # below still records the "ok"/"error" outcome.
        write_heartbeat(status="tick_start", tick=tick_count)
        try:
            # Per-tick cost measurement (2026-08-09). The hook chain below is a
            # dozen individually-bounded best-effort calls and NOTHING measured
            # the sum — the shape of both June 2026 wedges, where each new
            # component was cheap in isolation and the total was never watched.
            # Measure only: no budget is enforced here, because a cap with no
            # distribution behind it is the exposure-ceiling mistake (a ceiling
            # below normal operation silently throttles correct work). Two
            # monotonic() calls per tick; persisted on a cadence.
            try:
                from src.runtime.tick_cost import begin_tick as _tick_cost_begin
                _tick_cost_begin()
            except Exception:  # noqa: BLE001
                pass

            # PER-HOOK SPLIT — deliberately COARSE, two wraps only.
            # /api/diag/tick_cost measured the SUM at 253s mean / 296s max on
            # 2026-08-10 (13 ticks), which puts every open live trade's
            # re-evaluation on a ~5-minute cadence rather than the
            # TICK_INTERVAL_SECONDS=60 the sleep implies. The sum cannot say
            # WHERE the time goes, and the operator's <=60s requirement needs
            # that before anything is redesigned.
            #
            # Two buckets answer the first-order question and the third comes
            # free: `attributed_pct` covers signal-generation + the monitor, so
            # 100 - attributed is EVERY OTHER HOOK COMBINED (pairs · macro ·
            # five prop prompts · two reachability alerts · IB-state · exposure
            # soak). If signal generation dominates, the decoupling design
            # follows immediately and the other twelve blocks never need
            # touching; if it does not, THAT is the surprise worth finding
            # before instrumenting further. Measure coarsely, refine on
            # evidence — the same order as the census.
            with _tick_hook("run_one_tick"):
                run_one_tick(settings, exchange_client, telegram_client)

            # CLAUDE.md § Architecture rules § 2 + § 3 +
            # architecture-audit-2026-05-02 P1-4: after generating
            # signals on this tick, run the monitor loop across every
            # open order package. The loop calls each strategy's
            # monitor() hook with fresh candles and applies non-None
            # verdicts to the DB unit. Best-effort; never raises.
            with _tick_hook("order_monitor"):
                try:
                    if decoupled:
                        # The exit half runs on its own loop; this tick keeps the
                        # reconcilers/sweeps, whose latency governs how fast the
                        # JOURNAL catches up with the broker (a real requirement,
                        # a different one from exit-evaluation latency).
                        from src.runtime.order_monitor import (
                            run_reconciliation_tick,
                        )
                        run_reconciliation_tick(
                            ohlcv_fetcher=_build_monitor_ohlcv_fetcher(settings),
                        )
                    else:
                        from src.runtime.order_monitor import run_monitor_tick
                        run_monitor_tick(
                            ohlcv_fetcher=_build_monitor_ohlcv_fetcher(settings),
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("order_monitor tick failed")

            # The exit loop left this thread, so it left the liveness watchdog's
            # coverage — that coverage IS the inline execution. This is the only
            # place a check can observe an exit-loop wedge, because the main loop
            # is the one thing still known alive when the exit loop is not.
            # Latched alert, never autoheal (see exit_loop_health for why).
            if decoupled:
                try:
                    from src.runtime.exit_loop_health import (
                        run_exit_loop_health_check,
                    )
                    run_exit_loop_health_check()
                except Exception:  # noqa: BLE001
                    logger.exception("exit_loop_health check failed")
            else:
                # The DISABLED branch must still write the state file, or the
                # previous process's payload survives and keeps reporting
                # `"state": "fresh"` for a loop that is not running — measured
                # live on 2026-08-14 after the #9233 rollback, on a file stamped
                # 16s BEFORE the process it appeared to describe. See
                # exit_loop_health.write_disabled_state_file for the full account.
                try:
                    from src.runtime.exit_loop_health import (
                        write_disabled_state_file,
                    )
                    write_disabled_state_file()
                except Exception:  # noqa: BLE001
                    logger.exception("exit_loop_health disabled-state write failed")

            # Market-neutral pairs sleeve (M22 D2): an ISOLATED 2-leg executor
            # that does NOT fit the single-symbol intent model, so it runs as
            # its own once-per-tick hook (prop-bridge pattern) rather than
            # through multi_account_execute. For each configured pair it
            # reconstructs open-state from the journal, decides on fresh 1h
            # candles, and (only for an `execution: live` pair) places/closes
            # the two legs; an `execution: shadow` pair computes + logs the
            # would-be trade but places nothing. Inert until config/pairs.yaml
            # is authored; best-effort (never raises into the loop).
            try:
                from src.units.strategies.pairs_executor import run_pairs_tick
                run_pairs_tick(settings)
            except Exception:  # noqa: BLE001
                logger.exception("pairs_tick failed")

            # Macro/value thesis sleeve (M28 P3): an ISOLATED, slow-cadence,
            # OBSERVE-ONLY scanner that reads the point-in-time valuation
            # snapshots, forms weeks-horizon value theses (the S1 rule-based
            # former), and logs the would-be theses to a soak. It places NOTHING
            # — the defined-risk options executor is P5, so no order path exists
            # here regardless of config/macro_theses.yaml's `execution` gate.
            # Cadence-gated (hourly by default) + best-effort (never raises into
            # the loop, never blocks a trade). Inert until valuation snapshots
            # accrue.
            try:
                from src.units.strategies.macro_thesis.thesis_tick import run_macro_thesis_tick
                run_macro_thesis_tick(settings)
            except Exception:  # noqa: BLE001
                logger.exception("macro_thesis_tick failed")

            # Prop trades are a manual bridge (no broker feed), so the
            # order_monitor above never sees them. Emit a periodic
            # "still monitoring" pulse per open prop position instead so
            # the operator knows the system is actively tracking it
            # between report-backs. Internally rate-limited to
            # PROP_MONITOR_PULSE_SECONDS (default 15 min); best-effort.
            try:
                from src.prop.prop_monitor_pulse import run_prop_monitor_pulse
                run_prop_monitor_pulse()
            except Exception:  # noqa: BLE001
                logger.exception("prop_monitor_pulse tick failed")

            # A prop ticket that passed its validity window with no report-back
            # is silent drift — the bot can't tell whether it was
            # placed-and-unreported or skipped. Ask the operator with a Yes/No
            # prop-bot prompt: No → log it expired; Yes → send the report prompt
            # to collect the fill details. Once per tick; internally idempotent
            # (each ticket is prompted exactly once via its status flip).
            try:
                from src.prop.prop_expiry_prompt import run_prop_expiry_prompts
                run_prop_expiry_prompts()
            except Exception:  # noqa: BLE001
                logger.exception("prop_expiry_prompt tick failed")

            # The rule-distance guard is only as fresh as the last
            # account-status report-back. While a prop position is open and
            # the latest prop_account_status snapshot is absent/stale, ask
            # the operator for one on the prop bot — with the paste-ready
            # reply formats (`bal ...` / JSON) baked into the message.
            # Internally rate-limited (PROP_STATUS_REQUEST_MAX_AGE_HOURS /
            # PROP_STATUS_REQUEST_COOLDOWN_HOURS); best-effort.
            try:
                from src.prop.prop_status_request import run_prop_status_request
                run_prop_status_request()
            except Exception:  # noqa: BLE001
                logger.exception("prop_status_request tick failed")

            # When an open prop trade's current price crosses its SL or TP
            # level, fire a one-shot Telegram + FCM alert prompting the
            # operator to check whether the trade closed and report back.
            # One alert per level per open-position lifetime; best-effort.
            try:
                from src.prop.prop_sl_tp_alert import run_prop_sl_tp_alert
                run_prop_sl_tp_alert()
            except Exception:  # noqa: BLE001
                logger.exception("prop_sl_tp_alert tick failed")

            # The account-status request above chases a stale BALANCE; nothing
            # chased stale FILLS, which is why three days of terminal prop
            # trades went unrecorded when the report-back path itself broke
            # (BL-20260823-PROP-JOURNAL-MISSING-THREE-DAYS-OF-TERMINAL-TRADES).
            # Two proof-anchored detectors: a bracket ALREADY announced as
            # crossed whose position is still open in the journal, and a
            # balance that moved between two operator reports with zero fills
            # reported in between. Deliberately NOT keyed on unacted tickets —
            # on a manual bridge an unanswered ticket is expected, and alerting
            # on it would be the desensitized-alarm P1 (operator, 2026-08-23).
            # Cadence-gated internally; best-effort.
            try:
                from src.prop.prop_fills_staleness import run_prop_fills_staleness
                run_prop_fills_staleness()
            except Exception:  # noqa: BLE001
                logger.exception("prop_fills_staleness tick failed")

            # While the bot is still waiting for the operator's place-decision on
            # a freshly-emitted prop ticket, price can move beyond the ticket's
            # brackets — the setup is no longer placeable. Proactively warn ("do
            # NOT place it if you haven't") and re-ask the Yes/No, before the
            # slower valid_until timeout would. Once per tick; internally
            # idempotent (prompted exactly once via its status flip); best-effort.
            try:
                from src.prop.prop_invalidation_prompt import run_prop_invalidation_prompts
                run_prop_invalidation_prompts()
            except Exception:  # noqa: BLE001
                logger.exception("prop_invalidation_prompt tick failed")

            # A supposed-to-be-live broker account reading unreachable (IB
            # gateway logged out, exchange API 401-ing, creds rotated out)
            # is a money-at-risk condition that must surface LOUDLY, not sit
            # quietly in a report body — the IB gateway was dark across
            # reviews and went unflagged. Latched per-account: one DOWN ping
            # on a confirmed cross-into-down (>= N consecutive checks), one
            # OK ping on recovery. Internally cadence-gated
            # (ACCOUNT_REACHABILITY_CHECK_SECONDS, default 10 min); reuses
            # the reconciler's reachability primitive; best-effort.
            try:
                from src.runtime.account_reachability_alert import (
                    run_account_reachability_check,
                )
                run_account_reachability_check()
            except Exception:  # noqa: BLE001
                logger.exception("account_reachability_check tick failed")

            # The gap the check above CANNOT see: an account whose positions()
            # answers (so it reads UP) while balance() returns None, refusing
            # every signal routed to it. Measured 2026-08-14: alpaca_live threw
            # 120 refusals across 16 days and nothing alerted once. This reads
            # the journal the trader already wrote — NO broker round-trip, so
            # the sibling's "no new exchange round-trip" invariant holds.
            # Latched per (account, cause); internally cadence-gated
            # (SILENT_REFUSAL_CHECK_SECONDS, default hourly); best-effort.
            try:
                from src.runtime.silent_refusal_alert import (
                    run_silent_refusal_check,
                )
                run_silent_refusal_check()
            except Exception:  # noqa: BLE001
                logger.exception("silent_refusal_check tick failed")

            # Exit-path leg coverage (2026-08-18,
            # BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG). order_monitor
            # drives exits per PACKAGE and resolves ONE trade from
            # linked_trade_id, so a multi-account package's sibling legs are
            # never trailed and never closed — and once the linked leg closes,
            # the package flips to `closed` and the loop's status="open" filter
            # drops the survivors permanently. Neither condition is visible on
            # any existing surface: a stranded leg renders as a normal open
            # position and its package as a normal closed package. Reads the
            # journal only — NO broker round-trip, so the reachability sibling's
            # "no new exchange call on the tick" invariant holds. Latched per
            # (package, verdict); internally cadence-gated
            # (PACKAGE_LEG_CHECK_SECONDS, default hourly); best-effort.
            try:
                from src.runtime.package_leg_coverage import (
                    run_package_leg_check,
                )
                run_package_leg_check()
            except Exception:  # noqa: BLE001
                logger.exception("package_leg_check tick failed")

            # Trainer-VM-down alert (operator-requested 2026-07-08): the trainer
            # VM can go SSH-dead / OOM-hung and nothing fires a loud alert. The
            # trainer rsyncs trainer_status.json into the mirror every ~2 min, so
            # a mirror stale beyond TRAINER_DOWN_STALE_SECONDS (default 20 min) is
            # a confirmed DOWN. Latched: one 🔴 DOWN ping (Telegram + WARNING FCM)
            # + surfaced on /api/bot/notifications for the app banners, one 🟢 OK
            # on recovery. Internally cadence-gated (5 min); best-effort.
            try:
                from src.runtime.trainer_reachability_alert import (
                    run_trainer_reachability_check,
                )
                run_trainer_reachability_check()
            except Exception:  # noqa: BLE001
                logger.exception("trainer_reachability_check tick failed")

            # IB connection-state legibility (BL-20260707-IB-STATE-LEGIBILITY):
            # dump each live IBClient's non-blocking connection_state() to
            # runtime_logs/ib_state.json so the SEPARATE web-api process can
            # surface "connected vs down, transitory backoff vs real wedge" via
            # /api/diag/ib_state. Pure observability, best-effort — never
            # touches the socket, never gates a trade.
            try:
                from src.units.accounts.ib_client import write_ib_state_file
                write_ib_state_file()
            except Exception:  # noqa: BLE001
                logger.debug("write_ib_state_file tick hook skipped", exc_info=True)

            # Gross-exposure observation soak. The ceiling VALUES are Tier-3 and
            # cannot be chosen without a distribution of normal operation per
            # account (gross-exposure-governance-DESIGN.md § 6 needs the ceiling
            # ABOVE normal operation and BELOW the venue limit; § 7 forbids
            # shipping a value with no soak behind it). #8665 made the
            # measurement emittable and #8678 made it readable — neither
            # ACCUMULATES it, so the first read available was a single Sunday
            # snapshot of a held book. This samples it on a cadence
            # (EXPOSURE_SOAK_SECONDS, default 15 min; <= 0 pauses) and stamps
            # the US-equity session phase per row, so a later reader can tell a
            # quiet account that is VENUE-SHUT from one that is REFUSING —
            # indistinguishable from the trades table alone. Observe-only,
            # connection-free, internally cadence-gated, never raises.
            try:
                from src.runtime.exposure_soak import emit_exposure_soak
                emit_exposure_soak()
            except Exception:  # noqa: BLE001
                logger.debug("exposure_soak tick hook skipped", exc_info=True)

            # Close the per-tick cost measurement BEFORE the heartbeat write,
            # so the recorded duration covers the whole hook chain and nothing
            # else. Best-effort; a failure here leaves the tick untouched.
            try:
                from src.runtime.tick_cost import end_tick as _tick_cost_end
                _tick_cost_end()
            except Exception:  # noqa: BLE001
                pass

            # PR5: heartbeat is the single source of truth for "trader is
            # alive". Writes after a successful tick, not before — so a
            # tick that crashes mid-run doesn't refresh the heartbeat and
            # the watchdog will alert.
            last_tick_status = "ok"
            write_heartbeat(status=last_tick_status, tick=tick_count)
        except Exception as exc:
            logger.exception("Tick failed with unhandled exception: %s", exc)
            report(
                "pipeline_tick",
                "exception",
                level=Level.CRITICAL,
                reason=f"{type(exc).__name__}: {exc}",
            )
            # Heartbeat marker still gets written so monitors can
            # distinguish "process is running but ticks failing" from
            # "process is dead". The 'error' status is what the watchdog
            # surfaces.
            last_tick_status = "error"
            write_heartbeat(status=last_tick_status, tick=tick_count)
        # Hourly report + the liveness-watchdog piggyback were moved OUT
        # of the trader loop to the single flock-guarded producer
        # (scripts/send_hourly_now.py via ict-hourly-snapshot.timer) so
        # the operator gets EXACTLY ONE dispatch per hour. The old
        # in-loop should_send_summary path double-fired alongside the
        # timer ("hourly coming too often"); see TELEGRAM-SPEC.md § 4.1.
        # Running the watchdog from the timer is strictly better — it
        # fires on the wall-clock hour even if a tick is wedged.

        # Refresh the heartbeat between ticks so the dashboard / diag
        # liveness signal is "is this process responsive *right now*"
        # rather than "did the last tick complete in the last 15 min".
        # Cadence is HEARTBEAT_INTERVAL_SECONDS (default 60 s — one
        # write per minute is free on a loopback FS). A pipeline hang
        # still stops the heartbeat because SIGNAL GENERATION and the
        # reconcilers run inline on this thread; a daemon-thread writer
        # would falsely report alive.
        #
        # CORRECTED 2026-08-12: this used to say "a pipeline hang stops the
        # heartbeat" without qualification, which stopped being the whole
        # truth when exit evaluation moved to its own thread. An exit-loop
        # wedge does NOT stop this heartbeat — that is precisely the gap
        # `exit_loop_health` exists to cover, and it is checked in the tick
        # above. Leaving the old wording would have left a comment asserting
        # coverage the code no longer provides.
        heartbeat_interval = int(
            os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60")
        )
        if heartbeat_interval <= 0:
            heartbeat_interval = 60
        logger.info(
            "Sleeping %s seconds until next tick (heartbeat every %s s).",
            interval, heartbeat_interval,
        )
        end_time = time.monotonic() + interval
        while True:
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(heartbeat_interval, remaining))
            if time.monotonic() < end_time:
                # Status reflects the last completed tick — refreshes
                # mtime so liveness checks see a fresh signal without
                # losing the "ok / error" state of the most recent run.
                write_heartbeat(status=last_tick_status, tick=tick_count)


if __name__ == "__main__":
    main()
