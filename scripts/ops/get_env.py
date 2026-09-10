#!/usr/bin/env python3
"""Tier-1 read-only: report the LIVE value of an allowlisted env var on the VM.

Why this exists
---------------
`set-env` could WRITE an env var on the live VM and nothing could READ one back.
That asymmetry is the shape `provenance-consumer-guard` exists to catch — a
signal that is written and never read — one level up, at the ops surface. Its
concrete cost, measured 2026-08-10: `CONVICTION_SIZING_ACCOUNTS` scopes a Tier-3
size multiplier, an EMPTY value means *every* account including real money, and
no session surface could establish its live value. Sampling the soak log cannot
substitute — `bybit_2` having no qualifying signal in a window is
indistinguishable from `bybit_2` being excluded from the allowlist (an unasserted
denominator, sub-class C).

Two sources, and the disagreement is the point
----------------------------------------------
* **process** — `/proc/<MainPID>/environ` of the running unit. AUTHORITATIVE:
  what the process actually holds. A restart is the only thing that changes it.
* **declared** — the unit's inline `Environment=` directives MERGED with the
  contents of its `EnvironmentFiles=`, both asked of systemd rather than
  hardcoded (field beats comment: the unit declares its own configuration).
  What the next restart will pick up.

**Both halves of `declared` are required.** The first version read only
`EnvironmentFiles`, so `TICK_INTERVAL_SECONDS` / `HEARTBEAT_INTERVAL_SECONDS`
— pinned inline on `ict-trader-live.service` and absent from `.env` — reported
`declared: <unset>` against a live process holding `60`, and the tool announced
a **pending restart that did not exist** (first real run, issue #8755). A
diagnostic that invents a discrepancy is worse than one that reports nothing,
and this one would have sent a reader to look for a phantom deploy. Each row
now also carries `declared_source` so the origin is visible rather than assumed.

They can differ, and that difference is a real, otherwise-invisible condition:
the `.env` was edited and the service never re-read it. This is exactly how
`BYBIT_TPSL_MODE` had to be verified by hand in a prior session (three ways,
`/proc/<MainPID>/environ` being the one that settled it). So both are reported
side by side and a mismatch is called out as `pending_restart`.

Four states per source, NEVER collapsed
---------------------------------------
`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" — can this field say "we
did not look"?

* ``set``        — present and non-empty.
* ``set_empty``  — present and EMPTY. Distinct from ``unset`` and load-bearing:
                   for an allowlist var, empty is the WIDEST setting, not the
                   absence of a setting. Collapsing these two is the bug.
* ``unset``      — we looked; the key is not there.
* ``unreadable`` — we could NOT look (no MainPID, /proc denied, file missing).
                   Never rendered as ``unset``; "no data here" and "no value
                   here" are opposite claims.

Secrets are never printed — and THE OUTPUT IS PUBLIC
----------------------------------------------------
This runs under `system-actions`, which comments the script's stdout back onto
the originating GitHub issue, and **this repo is public** (guarded by the
collaborator-only interaction limit, not by privacy). So the binding rule for
`ALLOWED_KEYS` is: **a key belongs here only if its value is safe to publish.**
That is a property of the key, decided when it is added, not something a
reviewer can infer later from a run log.

Belt-and-braces on top of that: a key whose NAME matches the secret pattern is
served presence + fingerprint only (sha256 prefix + length), never its value —
so misjudging one entry cannot leak it. That still answers the question that
actually gets asked ("is `DASHBOARD_API_TOKEN` set on the VM?",
BL-20260705-DASHBOARD-API-TOKEN-UNSET, where a dropped value silently reopened
an anonymous write hole) and lets two environments be compared for equality
without either value leaving the box.

Reads only. Opens no socket, writes nothing, restarts nothing.

Exit codes: 0 report produced (even if a key is unset/unreadable — that IS the
report), 1 bad usage / disallowed key or unit, 2 could not measure anything at
all (an ABSENT result, not a clean one).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys

# --- Allowlist ------------------------------------------------------------
# Fixed and explicit, exactly like the workflow's action allowlist: there is no
# freeform key input by design, so a compromised or mistaken issue body cannot
# turn this into an arbitrary-environment dump. Adding a key is a one-line edit
# here plus a docs row — deliberately a code change, reviewed like any other.
#
# Scope rule: keys that GOVERN live behaviour (order-path modes, scope
# allowlists, kill-switches, cadences) — the ones whose live value a review
# session has to be able to check against the docs. Not a general env dump.
ALLOWED_KEYS: tuple[str, ...] = (
    # --- Order-path modes + scope allowlists (the reason this exists) ---
    "CONVICTION_SIZING_MODE",
    "CONVICTION_SIZING_DIRECTION",
    "CONVICTION_SIZING_ACCOUNTS",
    "NETTING_ATTRIBUTION_MODE",
    "NETTING_ATTRIBUTION_ACCOUNTS",
    # ARBITRATION_FANOUT_* (2026-08-31): per-account arbitration — which ACCOUNT
    # an order routes to when several contend for one symbol. Same mode + scope
    # shape as the pairs above, and allowlisted in the SAME change that made the
    # apply path real rather than after arming it, which is the whole lesson of
    # the PROP_SCREENSHOT_BACKEND note below.
    # ⚠️ `..._ACCOUNTS` EMPTY MEANS **NONE** (the PROTECTION_REASSERT polarity,
    # deliberately inverted from CONVICTION_SIZING_ACCOUNTS / NETTING_*, where
    # empty means ALL). So "unset" and "armed everywhere" are opposite readings
    # of the same blank here too — and worse, arming is TWO coordinated settings
    # (mode=apply AND a non-empty allowlist), so a reader who can see only one
    # of them cannot tell an armed system from an inert one. Both keys or
    # neither.
    # Both values are safe to publish: a fixed mode string, and an account-id
    # CSV — the same class as ACCOUNT_DOWN_ALERT_SKIP, which carries no secret.
    "ARBITRATION_FANOUT_MODE",
    "ARBITRATION_FANOUT_ACCOUNTS",
    # PROTECTION_REASSERT_* (2026-08-23): re-asserts a diverged protective leg at
    # its journal-declared level — an order-path mutation gated by a mode + a
    # scope allowlist, the same shape as the two above. `..._ACCOUNTS` is the
    # reason these are here on day one rather than after an incident: its EMPTY
    # value means NONE (deliberately inverted from its siblings, where empty
    # means ALL), so "unset" and "armed for every account" are opposite readings
    # of the same blank, and only a read surface can tell them apart.
    # ⚠️ ADDED 2026-08-23, AND THE GAP WAS SELF-INFLICTED ON THE SAME DAY.
    # `PROP_SCREENSHOT_BACKEND` shipped that morning as the gate deciding whether
    # a prop screenshot — carrying account balance, equity, the broker account
    # number and open positions — may be sent to a hosted model. Four
    # PROTECTION_REASSERT_* siblings were allowlisted correctly in the same
    # session and this one was not, so the one env var governing a live-data
    # EGRESS decision was the one that could not be read back from outside.
    # That is exactly BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE, and
    # "the default is safe" is not a substitute for being able to CHECK it:
    # the value is `local`/`external`/`off`, none of which is a secret.
    "PROP_SCREENSHOT_BACKEND",
    # `INSIGHTS_MODEL_MODE` is the M13 analyst's provider switch —
    # `template` (no hosted call) / an Anthropic mode / a Gemini mode. Measured
    # 2026-08-23 as template:v1 with 28,808 calls month-to-date at 0 tokens and
    # $0.00, so nothing is leaving today — but that is a measurement of the
    # EFFECT, not a reading of the control, and one flip of this key starts
    # sending TRADE DATA to a hosted model. It was unreadable from outside too.
    # A mode name is not a secret.
    "INSIGHTS_MODEL_MODE",
    # --- Added 2026-08-27: EIGHT keys shipped 2026-08-13..08-26 with no read
    # surface. This is the THIRD recurrence of
    # BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE, and the comment on
    # PROP_SCREENSHOT_BACKEND above already records the second one as
    # "self-inflicted on the same day". The pattern is now the rule, not the
    # exception, so treat "does this key need an allowlist row?" as part of
    # shipping the key, not as follow-up.
    #
    # PROP_TICKET_RISK_GATE_MODE is the sharpest of the eight: it decides
    # whether a prop ticket whose risk EXCEEDS the account's remaining cushion
    # is merely annotated or actually capped. Measured 2026-08-27, breakout_1
    # sat $64.00 from its $4,700 static-DD floor with an open ticket risking
    # $90.12 — so which mode is live is the difference between a warning and a
    # permanently disabled account, and it could not be read from outside.
    # `off`/`annotate`/`enforce` is not a secret.
    "PROP_TICKET_RISK_GATE_MODE",
    # PROTECTION_STRAY_GROUP_* cancels a live position's resting protective
    # legs. Its EMPTY `..._ACCOUNTS` means NONE (inverted from its
    # CONVICTION_SIZING_/NETTING_ATTRIBUTION_ siblings, where empty means ALL),
    # so "unset" and "armed everywhere" are opposite readings of the same
    # blank — the same reason the PROTECTION_REASSERT_* pair is here.
    "PROTECTION_STRAY_GROUP_MODE",
    "PROTECTION_STRAY_GROUP_ACCOUNTS",
    # BYBIT_HEDGE_MODE_SYMBOLS decides which `positionIdx` a live order names.
    # Empty is the shipped state and is CORRECT; the point is being able to
    # show that it is still empty rather than asserting it.
    "BYBIT_HEDGE_MODE_SYMBOLS",
    # BYBIT_GRADED_COVERAGE_* decides whether the naked sweep's re-arm reads
    # the GRADED book or the side-blind sum, and on WHICH accounts. Its empty
    # `..._ACCOUNTS` means NONE (the PROTECTION_REASSERT_/PROTECTION_STRAY_
    # GROUP_ polarity, inverted from CONVICTION_SIZING_/NETTING_ATTRIBUTION_),
    # so "unset" and "armed everywhere" are opposite readings of the same
    # blank. It is also a TWO-KEY arm: `apply` with an empty allowlist binds
    # nothing, so reading either key alone cannot say whether the staged gate
    # is live on bybit_1. A mode word and an account-id list are not secrets.
    "BYBIT_GRADED_COVERAGE_MODE",
    "BYBIT_GRADED_COVERAGE_ACCOUNTS",
    # The IB venue-session gate + probe cache. IB_SESSION_CHECK_DISABLED is a
    # kill-switch on the only thing stopping a close firing into a shut venue;
    # IB_CLOSE_OUTSIDE_RTH decides whether the order acts on the field the gate
    # graded; IB_PROBE_CACHE_S bounds how long a liveness verdict stands in for
    # a probe. All three govern live behaviour on the exit path.
    "IB_SESSION_CHECK_DISABLED",
    "IB_CLOSE_OUTSIDE_RTH",
    "IB_PROBE_CACHE_S",
    # PROP_STATUS_REQUEST_MAX_AGE_HOURS is ONE threshold with FOUR consumers
    # (the status ask, the fill-ack nudge, prop_balance's sizing-freshness
    # refusal, and /api/bot/prop/status's `status_freshness` verdict). A single
    # unreadable value therefore governs whether sizing may run off a stale
    # balance on the account with the terminal breach rule.
    "PROP_STATUS_REQUEST_MAX_AGE_HOURS",
    "PROTECTION_REASSERT_MODE",
    "PROTECTION_REASSERT_ACCOUNTS",
    "PROTECTION_REASSERT_COOLDOWN_S",
    "PROTECTION_REASSERT_MAX_ATTEMPTS",
    # T+1 cash-settlement gate. Both are safe to publish (a mode word and an
    # account-id list) and BOTH must be readable: the mode alone cannot say
    # whether the gate BINDS, because an empty ACCOUNTS allowlist means NONE on
    # this knob -- deliberately the opposite polarity to CONVICTION_SIZING_
    # ACCOUNTS above -- so `mode=apply` with an empty list is an ARMED-LOOKING
    # gate that constrains nothing. Reading one without the other is exactly
    # the confusion BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE records.
    "ALPACA_CASH_SETTLEMENT_MODE",
    "ALPACA_CASH_SETTLEMENT_ACCOUNTS",
    "NEWS_INFLUENCE_MODE",
    "NEWS_VETO_ENABLED",
    "NEWS_SOURCE",
    "REGIME_ML_VERDICT_MODE",
    "ML_VOL_VERDICT_THRESHOLD",
    "BYBIT_TPSL_MODE",
    "FLIP_POLICY",
    "FLIP_CONFIDENCE_THRESHOLD",
    "FLIP_MIN_POSITION_AGE_HOURS",
    # --- Kill-switches (a dropped one silently disables a capability) ---
    "REGIME_ROUTER_DISABLED",
    "REGIME_BAR_SCORING_DISABLED",
    "ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED",
    "CROSS_ASSET_LIVE_DISABLED",
    "SIGNAL_DUAL_WRITE_DISABLED",
    # Added 2026-08-13. `exit_loop_health` grades a set kill-switch as
    # `never_ran`, which its own contract calls "emphatically NOT healthy" —
    # but nothing could read the switch back to say WHY, and the decoupled exit
    # loop is the one condition the liveness watchdog no longer covers.
    "EXIT_LOOP_DECOUPLE_DISABLED",
    # Added 2026-09-10 with the key itself (MI-240), deliberately in the SAME
    # change rather than after the fact — CANDLE_CACHE_TTL_MAX_S's note below
    # records what the other order costs. The per-pass IB circuit breaker (R2,
    # Tier-2, DEC-20260910-EXIT-EVAL-60S-REMEDY) is ARMED BY DEFAULT, so an
    # UNSET value is the armed state and there is no `set-env` to read back.
    # That is exactly why it must be readable: the only way to establish the
    # breaker is armed on the running trader is to read this key off
    # /proc/<MainPID>/environ and see it absent. Without it, "armed" would be an
    # inference from the code rather than an observation of the fleet — and the
    # .env says only what the NEXT restart picks up.
    "EXIT_LOOP_IB_BREAKER_DISABLED",
    # --- Cadence / budget knobs (an unparseable one changes behaviour) ---
    "TICK_INTERVAL_SECONDS",
    "HEARTBEAT_INTERVAL_SECONDS",
    "TICK_COST_WRITE_SECONDS",
    "EXPOSURE_SOAK_SECONDS",
    "CANDLE_CACHE_TTL_FRACTION",
    # Added 2026-08-13, and the reason is a miss worth naming: the MAX_S key
    # shipped in #8815 WITHOUT being added here, so the very first Tier-3 write
    # of it (#8949, 60 -> 300) could not be read back — a write-without-a-reader
    # on an order-path value, which is the exact asymmetry this action exists to
    # close. It bounds how stale the price behind a live order may be, so it is
    # squarely in the "reason this exists" category, not a cadence nicety.
    "CANDLE_CACHE_TTL_MAX_S",
    "EXIT_LOOP_INTERVAL_SECONDS",
    "EXIT_LOOP_STALE_SECONDS",
    # The requirement the M20 decouple exists to satisfy, distinct from the
    # staleness window above (180s liveness vs 60s requirement — a 59s interval
    # and a 179s one both read `fresh`, which is why they are separate keys).
    "EXIT_EVAL_MAX_INTERVAL_SECONDS",
    "REGIME_BAR_SCORING_BUDGET_S",
    "ACCOUNT_REACHABILITY_CHECK_SECONDS",
    # --- Alert SKIP allowlists ---------------------------------------------
    # These do not tune a cadence: they DISABLE a specific alarm for a named
    # account, so their live value is the difference between "nothing is wrong"
    # and "the only thing watching this was switched off". A skip that is
    # writable by set-env and unreadable by anything is precisely
    # BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE, one level worse than the
    # cadence keys above, because the failure it hides is silence.
    #
    # Added 2026-08-21 when alpaca_live (real money, 127 of 127 orders refused
    # for zero balance) was deliberately silenced by operator decision — a
    # defensible call, but one no future review could have discovered without
    # this read surface. Values are account-id CSVs and carry no secret.
    "ACCOUNT_DOWN_ALERT_SKIP",
    "SILENT_REFUSAL_SKIP",
    "TRAINER_HEARTBEAT_CHECK_SECONDS",
    "PROP_MONITOR_PULSE_SECONDS",
    "IB_BROKER_NAKED_CHECK_SECONDS",
    # --- IB connection knobs (load-bearing during a gateway wedge) ---
    "IB_FETCH_TIMEOUT_S",
    # Added 2026-08-16. Both are DERIVED from IB_FETCH_TIMEOUT_S when unset
    # (`* 3 + 5` = 29.0s and `* 3` = 24.0s at the 8.0s default) and both bound a
    # fetch that the exit loop makes, so they land directly inside the 60s
    # exit-evaluation requirement — one queue timeout is 48% of that budget. They
    # shipped overridable and unreadable, the same write-without-a-reader
    # asymmetry that CANDLE_CACHE_TTL_MAX_S hit above
    # (BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE). Reading them back is how
    # a session can tell a derived 29.0s from an overridden one.
    "IB_FETCH_QUEUE_TIMEOUT_S",
    "IB_USAGE_LOCK_WAIT_S",
    "IB_PROBE_TIMEOUT_S",
    "IB_ACCOUNT_WARMUP_TIMEOUT_S",
    "IB_PLACE_CONFIRM_S",
    "IB_CLOSE_CONFIRM_S",
    # IB_MD_CLIENT_ID pins the clientId the WEB-API's market-data socket uses so
    # it cannot collide with the trader's own (exec 497 / md 498 on `ib_paper`).
    # It is here because the collision is INVISIBLE and the fallback is silent:
    # `market_data._ib_connection_identity` resolves
    # `settings -> env -> exec_client_id + 1`, so a caller that passes no
    # settings (`local_pnl.last_mark_price` passes `{}`) lands on **498** the
    # moment this var is unset — the trader's live socket — and IB answers
    # error 326 rather than anything a reader would recognise. Nothing in the
    # repo provisions this var: the ONLY thing supplying it is a hardcoded
    # `"600"` default inside `routers/candles.py::_settings()`, which protects
    # that one caller and no other. So `/api/bot/candles` works while the uPnL
    # mark-price fallback returns `unavailable`, which is exactly the live
    # 2026-08-25 reading on all three `ib_paper` legs.
    # A clientId integer is safe to publish. Cheap to allow, and without it the
    # question "is the reservation actually set?" has no answer at all
    # (BL-20260813-ENV-VARS-SHIP-WITHOUT-A-READ-SURFACE).
    "IB_MD_CLIENT_ID",
    # --- Paths (a wrong one is the stray-duplicate-journal class) ---
    "TRADE_JOURNAL_DB",
    "TRAINER_STORE_DB",
    "DATA_DIR",
    # --- Presence-only (fingerprinted, never printed) ---
    "DASHBOARD_API_TOKEN",
    "DIAG_READ_TOKEN",
)

#: Units whose process environment may be read. Bounded for the same reason the
#: `set-env` restart list is bounded.
ALLOWED_UNITS: tuple[str, ...] = (
    "ict-trader-live.service",
    "ict-web-api.service",
    "ict-claude-bridge.service",
    "ict-telegram-bot.service",
)

#: A key whose NAME matches is served presence + fingerprint only. Matching on
#: the name, not the value, so a secret can never leak by being unrecognised.
SECRET_NAME = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|_KEY$|^KEY_|PRIVATE|"
    r"CREDENTIAL|WEBHOOK|DSN)",
    re.IGNORECASE,
)

# The four states. Named so a reader cannot mistake one for another.
SET = "set"
SET_EMPTY = "set_empty"
UNSET = "unset"
UNREADABLE = "unreadable"


def _fingerprint(value: str) -> str:
    """A comparable, non-reversible stand-in for a secret value."""
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
    return f"sha256:{digest} (len={len(value)})"


def _render(key: str, state: str, value: str | None) -> str | None:
    """The reportable form of a value. Secret-named keys never yield one."""
    if state not in (SET, SET_EMPTY):
        return None
    if state == SET_EMPTY:
        return ""
    assert value is not None
    if SECRET_NAME.search(key):
        return _fingerprint(value)
    return value


def _systemctl_show(unit: str, prop: str) -> str | None:
    """One `systemctl show -p <prop>` value, or None when we could not ask."""
    try:
        r = subprocess.run(
            ["systemctl", "show", unit, "-p", prop, "--value"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def read_process_env(unit: str) -> tuple[dict[str, str] | None, str | None]:
    """The unit's LIVE process environment, or (None, why-we-could-not-look).

    Returning None rather than {} is the whole point: an empty dict would read
    as "the process has no env vars", which is never true and would make every
    key report ``unset``.
    """
    main_pid = _systemctl_show(unit, "MainPID")
    if main_pid is None:
        return None, "systemctl unavailable or unit unknown"
    if not main_pid.isdigit() or main_pid == "0":
        return None, f"unit has no running MainPID (MainPID={main_pid!r})"
    path = f"/proc/{main_pid}/environ"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except PermissionError:
        return None, f"{path} not readable (permission denied)"
    except FileNotFoundError:
        return None, f"{path} gone (process exited between calls)"
    except OSError as exc:
        return None, f"{path} unreadable: {exc}"
    env: dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        if not chunk:
            continue
        text = chunk.decode("utf-8", "replace")
        name, sep, value = text.partition("=")
        if sep:
            env[name] = value
    if not env:
        # A live process always has SOME environment. An empty parse means the
        # read did not work, not that the environment is empty.
        return None, f"{path} parsed to zero variables (unexpected)"
    return env, None


def _parse_env_file(path: str) -> dict[str, str]:
    """KEY=VALUE lines, last definition wins. Comments/blanks skipped."""
    out: dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if not sep:
                continue
            name = name.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[name] = value
    return out


def _parse_unit_environment(raw: str) -> dict[str, str]:
    """systemd's ``Environment=`` assignments, as rendered by `systemctl show`.

    One space-separated line of ``KEY=VALUE``; a value containing spaces is
    quoted. shlex handles the quoting rather than a naive split.
    """
    import shlex

    out: dict[str, str] = {}
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    for token in tokens:
        name, sep, value = token.partition("=")
        if sep:
            out[name] = value
    return out


def read_file_env(unit: str) -> tuple[dict[str, str] | None, list[str], str | None]:
    """The unit's DECLARED env: its inline ``Environment=`` directives merged
    with the contents of its ``EnvironmentFiles=``.

    **Both halves are required, and missing one produces a confident wrong
    answer.** The first version of this read only parsed EnvironmentFiles, so
    `TICK_INTERVAL_SECONDS` and `HEARTBEAT_INTERVAL_SECONDS` — pinned inline on
    `ict-trader-live.service` (lines 29-30) and absent from `.env` — reported
    ``declared: <unset>`` against a live process holding `60`, and the tool
    announced a **pending restart that did not exist** (observed on the first
    real run, issue #8755). A diagnostic that invents a discrepancy is worse
    than one that reports nothing.

    Both lists come from systemd, never from a constant here: the unit is the
    authority on its own configuration, and hardcoding either is how a doc
    drifts from the deployment.

    Precedence: inline ``Environment=`` is applied ON TOP of the files, which
    matches the ordering in this repo's units (EnvironmentFile= first). Because
    `systemctl show` does not expose directive ORDER, a key defined in both with
    DIFFERENT values is reported via ``declared_sources`` as a conflict rather
    than silently resolved — see :func:`declared_sources`.
    """
    raw_files = _systemctl_show(unit, "EnvironmentFiles")
    raw_inline = _systemctl_show(unit, "Environment")
    if raw_files is None and raw_inline is None:
        return None, [], "systemctl unavailable or unit unknown"

    paths: list[str] = []
    for token in (raw_files or "").split("\n"):
        token = token.strip()
        if not token:
            continue
        # systemd renders each as "/path/to/file (ignore_errors=no)".
        paths.append(re.sub(r"\s*\(ignore_errors=(?:yes|no)\)\s*$", "", token))

    inline = _parse_unit_environment(raw_inline or "")

    from_files: dict[str, str] = {}
    errors: list[str] = []
    for path in paths:
        try:
            from_files.update(_parse_env_file(path))
        except OSError as exc:
            errors.append(f"{path}: {exc}")

    if not paths and not inline:
        return None, [], "unit declares no Environment= and no EnvironmentFiles"
    if errors and not from_files and not inline:
        return None, paths, "; ".join(errors)

    merged = {**from_files, **inline}
    return merged, paths, ("; ".join(errors) or None)


def declared_sources(unit: str) -> dict[str, str]:
    """Per-key origin of the declared value: ``unit_environment`` /
    ``env_file`` / ``both_agree`` / ``both_conflict``.

    ``both_conflict`` is reported rather than resolved: systemd applies
    ``Environment=`` and ``EnvironmentFile=`` in DIRECTIVE ORDER, which
    `systemctl show` does not expose, so picking a winner would be a guess
    dressed as a fact.
    """
    raw_files = _systemctl_show(unit, "EnvironmentFiles")
    inline = _parse_unit_environment(_systemctl_show(unit, "Environment") or "")
    from_files: dict[str, str] = {}
    for token in (raw_files or "").split("\n"):
        token = token.strip()
        if not token:
            continue
        path = re.sub(r"\s*\(ignore_errors=(?:yes|no)\)\s*$", "", token)
        try:
            from_files.update(_parse_env_file(path))
        except OSError:
            continue
    out: dict[str, str] = {}
    for key in set(inline) | set(from_files):
        if key in inline and key in from_files:
            out[key] = "both_agree" if inline[key] == from_files[key] else "both_conflict"
        elif key in inline:
            out[key] = "unit_environment"
        else:
            out[key] = "env_file"
    return out


def classify(env: dict[str, str] | None, key: str, why: str | None) -> dict:
    """One source's verdict for one key, in the four-state vocabulary."""
    if env is None:
        return {"state": UNREADABLE, "value": None, "unreadable_reason": why}
    if key not in env:
        return {"state": UNSET, "value": None}
    value = env[key]
    state = SET_EMPTY if value == "" else SET
    return {"state": state, "value": _render(key, state, value)}


def agreement(process: dict, declared: dict) -> str:
    """Do the running process and the declared files hold the same value?

    Three outcomes, and ``undetermined`` is not a polite way of saying "agree":
    a disagreement is only assertable when BOTH sides were readable. Comparing a
    real value against an unreadable side would manufacture a finding out of a
    failed measurement.
    """
    if UNREADABLE in (process["state"], declared["state"]):
        return "undetermined"
    if (process["state"], process["value"]) == (declared["state"], declared["value"]):
        return "agree"
    return "pending_restart"


def build_report(unit: str, keys: list[str]) -> dict:
    proc_env, proc_why = read_process_env(unit)
    file_env, file_paths, file_why = read_file_env(unit)
    sources = declared_sources(unit)

    entries = []
    for key in keys:
        process = classify(proc_env, key, proc_why)
        declared = classify(file_env, key, file_why)
        entries.append({
            "key": key,
            "secret_name": bool(SECRET_NAME.search(key)),
            "process": process,
            "declared": declared,
            "declared_source": sources.get(key),
            "agreement": agreement(process, declared),
        })

    return {
        "unit": unit,
        "env_files": file_paths,
        "process_readable": proc_env is not None,
        "declared_readable": file_env is not None,
        "keys_requested": len(keys),
        "entries": entries,
    }


def _state_label(entry_side: dict) -> str:
    state = entry_side["state"]
    if state == SET:
        return repr(entry_side["value"])
    if state == SET_EMPTY:
        return "'' (SET BUT EMPTY — not the same as unset)"
    if state == UNSET:
        # NOT angle-bracketed. This action's stdout is posted back as a GitHub
        # issue comment, and GitHub strips <...> as HTML EVEN INSIDE A CODE
        # FENCE — so "<unset>" rendered as an EMPTY STRING on the only surface a
        # web session can read (action-output.txt is not reachable through the
        # GitHub MCP). Measured live on issue #10261: the comment showed
        # "process :" and "declared:" with nothing after either, and the real
        # answer had to be recovered by four-way elimination.
        #
        # It landed on exactly the two states this module's docstring says must
        # never be collapsed — "no data here" and "no value" are not the same —
        # because both were the <...>-wrapped ones. Keep every label
        # bracket-free; _self_test asserts it.
        # BL-20260825-GET-ENV-RENDERS-UNSET-AS-BLANK-ON-ITS-ONLY-WEB-CONSUMER
        return "(unset)"
    return f"(UNREADABLE: {entry_side.get('unreadable_reason')})"


def render_text(report: dict) -> str:
    lines = [
        f"get-env — unit={report['unit']}",
        f"  process env readable : {report['process_readable']}",
        f"  declared env readable: {report['declared_readable']} "
        f"(files: {', '.join(report['env_files']) or 'none'})",
        "",
        "  process = what the RUNNING process holds (authoritative)",
        "  declared = what the unit's EnvironmentFiles say (next restart)",
        "",
    ]
    for e in report["entries"]:
        lines.append(f"  {e['key']}")
        lines.append(f"      process : {_state_label(e['process'])}")
        lines.append(f"      declared: {_state_label(e['declared'])}")
        if e["agreement"] == "pending_restart":
            lines.append("      ** DIFFER — the file was edited and the service "
                         "has not re-read it (restart pending) **")
        elif e["agreement"] == "undetermined":
            lines.append("      (agreement undetermined — one side was unreadable, "
                         "which is NOT evidence they match)")
        if e.get("declared_source"):
            lines.append(f"      declared via: {e['declared_source']}")
        if e.get("declared_source") == "both_conflict":
            lines.append("      ** the unit's Environment= and its EnvironmentFile "
                         "disagree; systemd resolves by directive order, which "
                         "`systemctl show` does not expose — NOT resolved here **")
        if e["secret_name"]:
            lines.append("      (secret-named: fingerprint only, value never printed)")
    return "\n".join(lines)


def _self_test() -> int:
    """Prove the states are distinguishable and secrets never render.

    A guard that is never shown to catch a positive is not evidence. This runs
    the pure classifier over planted inputs — no VM needed.
    """
    ok = True

    def check(label: str, got, want) -> None:
        nonlocal ok
        if got != want:
            print(f"  self-test FAIL: {label}: got {got!r}, want {want!r}")
            ok = False
        else:
            print(f"  self-test ok: {label}")

    check("present non-empty -> set",
          classify({"A": "x"}, "A", None)["state"], SET)
    check("present empty -> set_empty (NOT unset)",
          classify({"A": ""}, "A", None)["state"], SET_EMPTY)
    check("absent -> unset",
          classify({"B": "x"}, "A", None)["state"], UNSET)
    check("could-not-look -> unreadable (NOT unset)",
          classify(None, "A", "denied")["state"], UNREADABLE)
    # The distinction that motivated the tool.
    check("set_empty and unset are different states",
          classify({"A": ""}, "A", None)["state"] != classify({}, "A", None)["state"],
          True)
    # Secrets: value must never survive rendering.
    rendered = _render("DASHBOARD_API_TOKEN", SET, "hunter2")
    check("secret-named key is fingerprinted", rendered.startswith("sha256:"), True)
    check("secret value never appears", "hunter2" in (rendered or ""), False)
    check("non-secret key renders its value",
          _render("FLIP_POLICY", SET, "hold"), "hold")

    # Agreement must never be asserted against an unreadable side.
    readable_set = {"state": SET, "value": "apply"}
    unreadable = {"state": UNREADABLE, "value": None}
    check("same value both sides -> agree",
          agreement(readable_set, {"state": SET, "value": "apply"}), "agree")
    check("differing values -> pending_restart",
          agreement(readable_set, {"state": SET, "value": "annotate"}), "pending_restart")
    check("unreadable side -> undetermined (NOT agree)",
          agreement(readable_set, unreadable), "undetermined")
    check("unreadable process side -> undetermined (NOT agree)",
          agreement(unreadable, readable_set), "undetermined")
    # set_empty vs unset must not silently read as agreement.
    check("set_empty vs unset -> pending_restart, not agree",
          agreement({"state": SET_EMPTY, "value": ""},
                    {"state": UNSET, "value": None}), "pending_restart")

    # RENDERED form, not just the data. The four states were always
    # distinguishable in `classify`; they stopped being distinguishable when
    # PRINTED, because GitHub ate the angle brackets. A label that cannot
    # survive the transport is not a label.
    _bracketed = re.compile(r"^<.*>$")
    for _state, _side in (
        (SET, {"state": SET, "value": "600"}),
        (SET_EMPTY, {"state": SET_EMPTY, "value": ""}),
        (UNSET, {"state": UNSET, "value": None}),
        (UNREADABLE, {"state": UNREADABLE, "value": None,
                      "unreadable_reason": "denied"}),
    ):
        _label = _state_label(_side)
        check(f"label for {_state} is not <...>-wrapped (GitHub strips it)",
              bool(_bracketed.match(_label)), False)
        check(f"label for {_state} is non-empty", bool(_label.strip()), True)
    # And they must still be distinguishable FROM EACH OTHER once rendered.
    _labels = [_state_label(x) for x in (
        {"state": SET, "value": "600"},
        {"state": SET_EMPTY, "value": ""},
        {"state": UNSET, "value": None},
        {"state": UNREADABLE, "value": None, "unreadable_reason": "denied"},
    )]
    check("all four rendered labels are distinct",
          len(set(_labels)) == 4, True)

    print("  self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default="",
                    help="allowlisted env var name, or ALL for every allowlisted key")
    ap.add_argument("--unit", default="ict-trader-live.service",
                    help=f"unit whose env to read (allowed: {', '.join(ALLOWED_UNITS)})")
    ap.add_argument("--json", action="store_true", help="emit the JSON report")
    ap.add_argument("--list-keys", action="store_true", help="print the allowlist and exit")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the four states are distinguishable; no VM needed")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if args.list_keys:
        for k in ALLOWED_KEYS:
            marker = "  (secret-named: fingerprint only)" if SECRET_NAME.search(k) else ""
            print(f"{k}{marker}")
        return 0

    if args.unit not in ALLOWED_UNITS:
        print(f"::error::unit {args.unit!r} is not allowlisted "
              f"(allowed: {', '.join(ALLOWED_UNITS)})", file=sys.stderr)
        return 1

    if not args.key:
        print("::error::--key is required (a name from --list-keys, or ALL)",
              file=sys.stderr)
        return 1

    if args.key == "ALL":
        keys = list(ALLOWED_KEYS)
    elif args.key in ALLOWED_KEYS:
        keys = [args.key]
    else:
        print(f"::error::key {args.key!r} is not allowlisted. This action has no "
              f"freeform key input by design — see --list-keys. Adding a key is a "
              f"one-line edit to ALLOWED_KEYS in scripts/ops/get_env.py.",
              file=sys.stderr)
        return 1

    report = build_report(args.unit, keys)
    print(json.dumps(report, indent=2) if args.json else render_text(report))

    if not report["process_readable"] and not report["declared_readable"]:
        # Neither source answered. That is an ABSENT result, not a clean one —
        # the `check_workflow_shell.py` exit-2 convention.
        print("::error::read NOTHING — neither the process env nor the declared "
              "env files were readable. This is an ABSENT result, not 'the key is "
              "unset'.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
