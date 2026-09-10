"""Liveness for the DECOUPLED exit-evaluation loop — section 6.1's missing half.

WHY THIS HAS TO EXIST BEFORE THE LOOP DOES.

`heartbeat.txt`'s mtime is the canonical "is the trader responsive" signal, and it
works because the pipeline runs INLINE on the main thread: a hang anywhere in the
tick stops the heartbeat, so `scripts/check_heartbeat.py` sees it. **That coverage
IS the inline execution.** Move exit evaluation onto its own loop and the coupling
breaks in the worst possible direction — the main loop keeps writing a healthy
heartbeat while no open trade is being evaluated. The watchdog stays quiet, the
apps stay green, and the failure is invisible for as long as it lasts. Trading a
measured 31.78 s of coverage margin for an unbounded silent wedge would be a bad
deal at any margin.

So the exit loop gets its own signal and something reads it.

THREE STATES, NEVER COLLAPSED (`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed
states"). "We have not looked", "it has never run", and "it ran and is late" are
three different facts and a reader must be able to tell them apart:

  * `unknown`     — the state file is unreadable/garbled. We did not look; this is
                    NOT health, and it is NOT staleness either.
  * `never_ran`   — no pass has completed since this process started. Expected for
                    the first seconds of a boot; alarming after that.
  * `fresh`       — a pass completed within the staleness window.
  * `stale`       — a pass completed, but too long ago. THE wedge signal.

It also records `last_pass_ms` / `max_pass_ms`, which is the point beyond alerting:
the whole decouple rests on a claim (a pass costs ~28 s, so coverage clears 60 s
with ~53% margin) measured OFFLINE on seven ticks. These fields are how that claim
gets checked in production, per-process, against the real book — and `max_pass_ms`
is the load-bearing one, exactly as in `tick_cost` and `exposure_soak`: a mean that
looks fine while the peak blows the window is the 2026-06-09 incident's shape.

Best-effort throughout: this is observability for the money loop and must never
raise into it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

STATE_FILE_NAME = "exit_loop_health.json"

_STALE_ENV = "EXIT_LOOP_STALE_SECONDS"
# 180s = 3x the 60s exit-evaluation target, against a measured ~28s pass. Wide
# enough that a slow pass or a paused venue never cries wolf; narrow enough that a
# genuine wedge is caught inside a few minutes rather than a few hours.
_DEFAULT_STALE_S = 180.0

# --- the REQUIREMENT, which is a different question from liveness -------------
#
# `_STALE_ENV` answers "is the loop alive". The operator's requirement — the thing
# M20 was built to guarantee — is that no live trade goes more than 60s without
# re-evaluation. Those are not the same threshold and must not share one:
# `stale_seconds()` is 180s, so a 59s interval and a 179s interval BOTH read
# `fresh`, and the requirement could be missed by 3x without any surface saying so.
#
# Measured 2026-08-16 (BL-20260816-EXIT-EVAL-INTERVAL-AT-60S-REQUIREMENT): the
# worst pass on one process was 58940.8ms at n=694 — 1.1s inside the requirement,
# graded `fresh`, alarming nowhere. That is the gap this closes.
#
# ⚠️ READ `requirement_state` BESIDE `intervals_measured`, never alone — the same
# discipline as `max_multiple` beside `measured_n` on the exposure soak. Both the
# max and the grade are PER-PROCESS and reset on restart, and the live trader
# restarts often: three processes in ~8.5h on 2026-08-16 (23:06 → 06:24 → 07:34),
# because `ict-git-sync` auto-deploys every merge to `main`. The tail needs a large
# n to be drawn at all — the 58940.8 ms observation came from an n=694 process that
# survived a quiet overnight window, while the two daytime processes reached only
# n=38 and n=23. So on a busy day `within` can mean "no process lived long enough to
# draw the tail", NOT "the requirement was met today".
#
# Per-process is still the correct SCOPE: a max cannot be pooled across processes
# without pooling their distributions, and a latch that never reset would go silent
# forever after the first breach. A cross-process view would need a durable
# accumulator — a different design, deliberately not this one.
_REQUIREMENT_ENV = "EXIT_EVAL_MAX_INTERVAL_SECONDS"
_DEFAULT_REQUIREMENT_S = 60.0

# --- the NEAR-MISS band, which is a third question again ----------------------
#
# `requirement_state` was BINARY about the thing that matters: a max of 59951.2ms
# against a 60000ms requirement graded `within`, exactly like a max of 20000ms.
# Measured 2026-09-09 (audit F-50) that is not a hypothetical margin -- it is the
# actual reading. Population: `exit_interval_soak`, n=989 intervals over 12
# processes, 08:36:14Z-16:54:41Z; mean 29880.6, median 29993.1, p95 34886.9,
# MAX 59951.2, `over_requirement` 0 of 1000. The promise cleared by **48.8ms**
# and every instrument said `within`.
#
# It is not a margin that has been shrinking, and this comment must not be read
# as saying so: against 2026-08-25 (n=991, same window shape) the BODY is
# unmoved -- mean 29879 -> 29880.6, median 29970 -> 29993.1. Only the tail moved
# (MAX 45034 -> 59951.2). And the requirement was already BREACHED once, on
# 2026-09-07T22:48:54Z: recorded fact from `requirement_breach_process` in the
# alert latch, which is written ONLY when a breach fires the alert.
#
# So the band is a WARN, deliberately a DISTINCT state from `breached` rather
# than a widening of it. A near miss is not a broken promise -- no trade went
# unevaluated past the requirement -- and grading it as one would train the
# operator past the alert that means a trade DID (the desensitized-alarm P1).
# Nor may it collapse into `within`, which is the defect above.
#
# 0.9 is the operator's number, chosen on 2026-09-09
# (`WO-20260909-DECISION-M20-EXIT-EVAL-MARGIN-COLLAPSED`,
# `chosen: near_miss_and_restart_gap`). It is a CHOSEN threshold, not a tuned
# one, and is stated as such rather than dressed up as measured.
#
# Deliberately NOT an env knob. Every other threshold in this module is one
# because it is a cadence or a window an operator may need to move on a live VM
# without a redeploy; this is the recorded content of a decision, and a knob
# would make it silently disableable -- which is the one direction that must not
# be cheap. CLAUDE.md's env table is explicitly "a curated subset", with other
# load-bearing flags "documented at their call sites"; this is that.
NEAR_MISS_FRACTION = 0.9

_lock = threading.Lock()
_passes: int = 0
_last_pass_monotonic: Optional[float] = None
_last_pass_utc: Optional[str] = None
_last_pass_ms: Optional[float] = None
_max_pass_ms: Optional[float] = None
_max_pass_at_utc: Optional[str] = None
_started_utc: Optional[str] = None
# Completion-to-completion gaps. MEASURED, not derived from the pass duration —
# see `record_pass`.
_intervals_measured: int = 0
_max_interval_ms: Optional[float] = None
_max_interval_at_utc: Optional[str] = None
_breaches: int = 0
_last_breach_utc: Optional[str] = None
# Graded at most ONCE per process — the restart gap is a fact about this
# process's START, so re-grading it every tick would re-read a file to
# recompute a value that cannot change.
_restart_gap_graded: bool = False

logger = logging.getLogger(__name__)


def stale_seconds() -> float:
    """Resolve the staleness window. Unparseable or non-positive → the default.

    A typo must not silently switch the wedge detector OFF — the same fail-ON
    reasoning as `exposure_soak.cadence_seconds` and `tick_cost.write_cadence_seconds`.
    Disabling this is not offered as a config option at all: the loop it watches has
    no other liveness coverage.
    """
    try:
        v = float(os.environ.get(_STALE_ENV, "") or _DEFAULT_STALE_S)
    except (TypeError, ValueError):
        return _DEFAULT_STALE_S
    return v if v > 0 else _DEFAULT_STALE_S


def requirement_seconds() -> float:
    """Resolve the max-interval requirement. Unparseable/non-positive → default.

    Same fail-ON discipline as `stale_seconds`, and for a sharper reason: this is
    the number the whole M20 decouple exists to satisfy. A typo that silently
    widened or disabled it would remove the only check on the guarantee.
    """
    try:
        v = float(os.environ.get(_REQUIREMENT_ENV, "") or _DEFAULT_REQUIREMENT_S)
    except (TypeError, ValueError):
        return _DEFAULT_REQUIREMENT_S
    return v if v > 0 else _DEFAULT_REQUIREMENT_S


def record_pass(duration_ms: float, extra_fields: Optional[Dict[str, Any]] = None) -> None:
    """Record one COMPLETED exit-evaluation pass. Never raises.

    Called after the pass returns, deliberately — a pass that started and hung
    must NOT refresh liveness, which is the entire condition being detected.

    ``extra_fields`` are stamped onto the durable per-pass soak row verbatim
    (default: nothing, so the row is unchanged). It exists so a pass can record
    WHY it took the time it did — today the per-pass IB circuit breaker's
    verdict, without which a short reset-window pass cannot be told from a pass
    that simply met a healthy queue.

    It also closes out the INTERVAL that just ended. The requirement is written
    about the gap between two evaluations, and that gap is `sleep + next pass`,
    so it is measured here as completion-to-completion rather than derived as
    `max(EXIT_LOOP_INTERVAL_SECONDS, pass_ms)`. The derivation is a model of the
    loop; this is the loop. They agree only while the loop behaves as designed,
    which is exactly the assumption a breach would violate — a pass that stalls
    between the sleep and the next completion shows up here and does not show up
    in the derivation at all.
    """
    global _passes, _last_pass_monotonic, _last_pass_utc, _last_pass_ms
    global _max_pass_ms, _max_pass_at_utc, _started_utc
    global _intervals_measured, _max_interval_ms, _max_interval_at_utc
    global _breaches, _last_breach_utc
    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        now_mono = time.monotonic()
        soak_interval_ms: Optional[float] = None
        with _lock:
            if _started_utc is None:
                _started_utc = now_utc
            # The first pass of a process closes no interval — there is no prior
            # completion to measure from. Counting it (as 0, or as the time since
            # boot) would be inventing a sample, so it is simply not one.
            if _last_pass_monotonic is not None:
                interval_ms = (now_mono - _last_pass_monotonic) * 1000.0
                soak_interval_ms = interval_ms
                _intervals_measured += 1
                if _max_interval_ms is None or interval_ms > _max_interval_ms:
                    _max_interval_ms = interval_ms
                    _max_interval_at_utc = now_utc
                if interval_ms > requirement_seconds() * 1000.0:
                    _breaches += 1
                    _last_breach_utc = now_utc
            _passes += 1
            _last_pass_monotonic = now_mono
            _last_pass_utc = now_utc
            _last_pass_ms = duration_ms
            if _max_pass_ms is None or duration_ms > _max_pass_ms:
                _max_pass_ms = duration_ms
                _max_pass_at_utc = now_utc
            snap_passes, snap_started = _passes, _started_utc
    except Exception:  # noqa: BLE001 — observability must never break the loop
        return

    # Durable, cross-process record. OUTSIDE the lock on purpose: this does file
    # I/O, and holding the state lock across it would let a slow disk stall the
    # exit loop — the one thing this module exists to detect. Best-effort, so a
    # failed append loses an observation and nothing else.
    #
    # WHY IT IS SEPARATE FROM THE FIELDS ABOVE: `_max_interval_ms` is scoped to
    # this process and is reset by every deploy, and the trader redeploys off
    # `main` via the `ict-git-sync` timer (five OBSERVED processes in ~10h,
    # measured 2026-08-16 from `process_started_utc`, NOT from merge times --
    # a merge does not promptly restart the trader, and counting merges
    # over-counted this by one). A max
    # over a short window is systematically LOW, so the in-memory grade is most
    # reassuring exactly when the system is busiest. This append is what makes
    # the max a property of the DATA rather than of a process's lifetime.
    try:
        from src.runtime.exit_interval_soak import (
            build_exit_interval_record, record_exit_interval,
        )
        # `extra_fields` rides through the builder's **fields. It defaults to
        # None so every existing caller writes a byte-identical row; the builder
        # drops None values, so an annotation that could not be gathered is
        # ABSENT rather than fabricated as a zero.
        record_exit_interval(build_exit_interval_record(
            interval_ms=soak_interval_ms,
            pass_ms=duration_ms,
            requirement_s=requirement_seconds(),
            process_started_utc=snap_started,
            passes=snap_passes,
            **(extra_fields or {}),
        ))
    except Exception:  # noqa: BLE001 — never raise into the exit loop
        return


def status() -> Dict[str, Any]:
    """Current health. Never raises; every field honest about what it knows."""
    try:
        requirement_s = requirement_seconds()
        with _lock:
            passes = _passes
            last_mono = _last_pass_monotonic
            intervals = _intervals_measured
            max_interval = _max_interval_ms
            snap = {
                "passes": passes,
                "last_pass_utc": _last_pass_utc,
                "last_pass_ms": (round(_last_pass_ms, 1)
                                 if _last_pass_ms is not None else None),
                "max_pass_ms": (round(_max_pass_ms, 1)
                                if _max_pass_ms is not None else None),
                "max_pass_at_utc": _max_pass_at_utc,
                "process_started_utc": _started_utc,
                "stale_threshold_s": stale_seconds(),
                # --- the requirement, graded explicitly ---
                "requirement_s": requirement_s,
                "intervals_measured": intervals,
                "max_interval_ms": (round(max_interval, 1)
                                    if max_interval is not None else None),
                "max_interval_at_utc": _max_interval_at_utc,
                "interval_breaches": _breaches,
                "last_breach_utc": _last_breach_utc,
            }
        # FOUR states, never collapsed — the same discipline as `state` above.
        # `not_measured` is the one that earns its keep: with fewer than two
        # completed passes there IS no interval, and reporting that as `within`
        # would let a process that has evaluated nothing read as compliant.
        # FIVE states, never collapsed — the same discipline as `state` above.
        # `not_measured` is the one that earns its keep: with fewer than two
        # completed passes there IS no interval, and reporting that as `within`
        # would let a process that has evaluated nothing read as compliant.
        # `near_miss` is the one added 2026-09-09: `within` covered both a
        # comfortable 20s max and a 59951.2ms max against a 60s requirement, and
        # those are not the same fact. ORDER MATTERS — `breached` is tested
        # FIRST, so a genuine breach can never be downgraded to a warning by the
        # band that sits just underneath it.
        requirement_ms = requirement_s * 1000.0
        if intervals < 1 or max_interval is None:
            snap["requirement_state"] = "not_measured"
            # None, never 0.0. A ratio of zero is a real reading (a max of 0ms);
            # "no interval exists" is not a ratio at all.
            snap["requirement_ratio"] = None
        else:
            snap["requirement_ratio"] = round(max_interval / requirement_ms, 4)
            if max_interval > requirement_ms:
                snap["requirement_state"] = "breached"
            elif max_interval >= requirement_ms * NEAR_MISS_FRACTION:
                snap["requirement_state"] = "near_miss"
            else:
                snap["requirement_state"] = "within"
        if last_mono is None:
            # NOT stale — nothing has run yet. Collapsing these would make a
            # booting process indistinguishable from a wedged one.
            snap.update(state="never_ran", age_seconds=None, stale=False)
            return snap
        age = time.monotonic() - last_mono
        snap.update(
            age_seconds=round(age, 1),
            stale=age > snap["stale_threshold_s"],
            state="stale" if age > snap["stale_threshold_s"] else "fresh",
        )
        return snap
    except Exception:  # noqa: BLE001
        # We could not look. Emphatically not "healthy", and not "stale" either —
        # and the requirement is likewise UNKNOWN, never "within". A read failure
        # must not be able to report compliance.
        return {"state": "unknown", "stale": False, "passes": None,
                "age_seconds": None, "last_pass_utc": None,
                "requirement_state": "unknown", "max_interval_ms": None,
                "requirement_ratio": None, "intervals_measured": None}


def write_state_file(runtime_dir: Optional[str] = None) -> Optional[str]:
    """Persist `status()` for the diag surface. Never raises; returns the path."""
    try:
        if runtime_dir is None:
            from src.utils.paths import runtime_logs_dir
            runtime_dir = str(runtime_logs_dir())
        os.makedirs(runtime_dir, exist_ok=True)
        path = os.path.join(runtime_dir, STATE_FILE_NAME)
        payload = dict(status(), generated_at=datetime.now(timezone.utc).isoformat())
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, path)   # atomic: a reader never sees a half-written file
        return path
    except Exception:  # noqa: BLE001
        return None


def write_disabled_state_file(runtime_dir: Optional[str] = None) -> Optional[str]:
    """Write the state file for a process where the decouple is DISABLED.

    WHY THIS EXISTS (live-verified 2026-08-14, the same day #9233 rolled the
    decouple back).

    Only `_exit_loop` ever called `write_state_file`, and `run_exit_loop_health_check`
    only ran inside `if decoupled:`. So with `EXIT_LOOP_DECOUPLE_DISABLED=1`
    NOTHING rewrote the file and NOTHING re-read it — the payload from the
    PREVIOUS process survived untouched, and it said `"state": "fresh"`.

    Measured on the live trader: `exit_loop_health.json` carried
    `generated_at 2026-08-14T11:46:13Z` while the running process had started at
    `11:46:29Z` — a file stamped 16 seconds BEFORE the process it appeared to
    describe, reporting a healthy loop that did not exist. Every downstream
    reader (the diag surface, a session, the operator) saw `fresh`.

    That is precisely the collapse this module's own docstring says it prevents:
    "we have not looked" and "it ran and is fine" became indistinguishable, in
    the one surface built to tell them apart. `CLAUDE.md` states that `never_ran`
    "is also what a set `EXIT_LOOP_DECOUPLE_DISABLED` produces" — a true
    statement about the vocabulary that the code did not implement, because the
    producer of that state never ran either.

    The fix keeps the four-state vocabulary rather than inventing a fifth: the
    loop genuinely HAS NOT RUN in this process, so `never_ran` is the honest
    grade. `decouple_disabled` says WHY, so a reader can tell a deliberate
    rollback from a thread that failed to start — which `_start_exit_loop`
    already treats as two different conditions and which would otherwise
    re-collapse the moment this file started reporting `never_ran` for both.

    Called every tick on the disabled branch, not once at startup: a
    `generated_at` that stops advancing is itself a fossil, so refreshing it is
    what keeps "disabled since boot" distinguishable from "this file is stale".
    """
    try:
        if runtime_dir is None:
            from src.utils.paths import runtime_logs_dir
            runtime_dir = str(runtime_logs_dir())
        os.makedirs(runtime_dir, exist_ok=True)
        path = os.path.join(runtime_dir, STATE_FILE_NAME)
        payload = {
            "state": "never_ran",
            "stale": False,
            "decouple_disabled": True,
            "passes": 0,
            "age_seconds": None,
            "last_pass_utc": None,
            "last_pass_ms": None,
            "max_pass_ms": None,
            "max_pass_at_utc": None,
            "process_started_utc": None,
            "stale_threshold_s": stale_seconds(),
            # `not_measured`, NOT `breached` — even though this mode is known not
            # to meet the target. The grade reports what was MEASURED, and nothing
            # measured an interval here; the note carries the knowledge. Stamping
            # a verdict we did not observe would make the field mean two different
            # things depending on mode.
            "requirement_s": requirement_seconds(),
            "requirement_state": "not_measured",
            "requirement_ratio": None,
            "intervals_measured": 0,
            "max_interval_ms": None,
            "max_interval_at_utc": None,
            "interval_breaches": 0,
            "last_breach_utc": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": (
                "EXIT_LOOP_DECOUPLE_DISABLED is set: exit evaluation rides the "
                "main tick, so there is no decoupled loop to be healthy. This is "
                "NOT health — the 60s exit-evaluation target is not met in this "
                "mode."
            ),
        }
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, path)   # atomic: a reader never sees a half-written file
        return path
    except Exception:  # noqa: BLE001
        return None


def _reset_for_tests() -> None:
    global _passes, _last_pass_monotonic, _last_pass_utc, _last_pass_ms
    global _max_pass_ms, _max_pass_at_utc, _started_utc
    global _intervals_measured, _max_interval_ms, _max_interval_at_utc
    global _breaches, _last_breach_utc, _restart_gap_graded
    with _lock:
        _restart_gap_graded = False
        _passes = 0
        _last_pass_monotonic = None
        _last_pass_utc = None
        _last_pass_ms = None
        _max_pass_ms = None
        _max_pass_at_utc = None
        _started_utc = None
        _intervals_measured = 0
        _max_interval_ms = None
        _max_interval_at_utc = None
        _breaches = 0
        _last_breach_utc = None


# --------------------------------------------------------------------- alerting
#
# A liveness signal nobody reads is worse than no signal: a reviewer sees the
# field and assumes something acts on it (`provenance-consumer-guard`'s whole
# premise, and the defect PR #8665's exposure block shipped with). So the module
# that produces the state also owns the consumer.
#
# LATCHED, not per-tick. The stale condition persists by nature — once the exit
# loop wedges it is stale on every subsequent tick — and a per-tick alarm is the
# desensitized-alarm P1 this repo has an explicit rule about. One ALERT crossing
# into stale, one OK crossing back out.
#
# ALERT-ONLY, deliberately NOT auto-restart. `ict-liveness-watchdog` may restart
# the trader for a dead heartbeat because a dead heartbeat means the process is
# already not working. A stale exit loop is different: the rest of the trader is
# healthy and still managing risk, so killing it converts a partial degradation
# into a total outage plus a cold-start burst (the `BL-20260609-001` shape).
# Escalating this to autoheal is a separate operator decision with its own
# restart-loop containment, not something to fold into the loop's first version.

_ALERT_STATE_FILENAME = "exit_loop_health_alert_state.json"


def _alert_state_path():
    from src.utils.paths import runtime_logs_dir
    return runtime_logs_dir() / _ALERT_STATE_FILENAME


def _load_alert_state() -> dict:
    try:
        p = _alert_state_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_alert_state(state: dict) -> None:
    try:
        p = _alert_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        return


def _send(message: str) -> None:
    """Telegram + one typed WARNING push, mirroring account_reachability_alert."""
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(message, parse_mode=None, mirror_to_fcm=False)
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, title="Exit loop", body=message)
    except Exception:  # noqa: BLE001
        pass


def _check_restart_gap(st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Grade THIS process's restart gap, once. Never raises; returns the grade.

    ⚠️ THE GAP THIS GRADES IS THE ONE ``status()`` CANNOT SEE. ``max_interval_ms``
    is a per-process accumulator, so the interval joining the previous process's
    last pass to this one's first is excluded by construction — and a deploy is
    exactly when the trader is least likely to be evaluating exits. See
    ``src/runtime/exit_restart_gap.py`` for the full argument.

    ONCE per process, deliberately, and cheaply: it is a fact about this process's
    start, so it cannot change, and re-deriving it every tick would re-read a file
    to recompute a constant. It runs from the MAIN tick rather than from
    ``record_pass`` on purpose — the grade needs a file read and can end in a
    Telegram send, and neither belongs on the exit loop's own thread, which is the
    thing whose latency this whole line of work is about.

    It waits for ``passes >= 1`` because the boundary needs BOTH endpoints, and
    this process's endpoint is its own first soak row.
    """
    global _restart_gap_graded
    try:
        if _restart_gap_graded:
            return None
        proc = st.get("process_started_utc")
        if not proc or not (st.get("passes") or 0):
            return None                      # our own first row is not on disk yet
        _restart_gap_graded = True           # set before the work: one attempt, win or lose

        from src.runtime.exit_restart_gap import (
            RESTART_GAP_BREACHED, RESTART_GAP_NEAR_MISS, RESTART_GAP_NOT_MEASURED,
            RESTART_GAP_UNKNOWN, RESTART_GAP_WITHIN, grade_this_process,
        )
        g = grade_this_process(proc)
        state = g.get("restart_gap_state")
        gap_s = round((g.get("max_gap_ms") or 0) / 1000.0, 1)
        req = g.get("requirement_s")

        # All five branched HERE, explicitly. Letting three of them fall into one
        # silent `else` is the collapse this vocabulary exists to prevent — the
        # three quiet states mean "it was fine", "there was no boundary" and "we
        # could not look", and only the first is good news.
        if state == RESTART_GAP_BREACHED:
            _send(
                "\U0001F534 [ALERT] EXIT-EVAL RESTART GAP BREACHED - the trader "
                f"restarted and open trades went {gap_s}s without re-evaluation "
                f"across the boundary (requirement {req}s). This is the interval "
                "max_interval_ms CANNOT see: it resets on restart, so every "
                "per-process surface will still read 'within'."
            )
        elif state == RESTART_GAP_NEAR_MISS:
            _send(
                "\U0001F7E1 [WARN] EXIT-EVAL RESTART GAP NEAR MISS - the gap "
                f"across the last restart was {gap_s}s against a {req}s "
                f"requirement ({round((g.get('max_gap_ratio') or 0) * 100, 1)}% of "
                "it). NOT a breach. Per-process surfaces cannot see this interval "
                "at all, so it will not appear in max_interval_ms."
            )
        elif state == RESTART_GAP_NOT_MEASURED:
            # No boundary in the tail — the first process to write the log, or a
            # freshly rotated one. Correct, and NOT compliance.
            logger.info("exit restart gap: not_measured (%s) - no prior process "
                        "boundary in the population, so no gap exists to grade",
                        g.get("population"))
        elif state == RESTART_GAP_UNKNOWN:
            # WE COULD NOT LOOK, and this is logged rather than passed over
            # precisely because a permanently-unknown grade reads exactly like a
            # working one — the lesson the IB venue-session gate records (its
            # `unknown` is fail-permissive AND logged, for that exact reason; the
            # env-var name is not cited here on purpose, because doing so makes
            # this file match that contract's consumer token and fire the
            # collapsed-state guard on a coincidence). It does not
            # PAGE: it is an instrument gap, not a trade going unevaluated.
            logger.warning(
                "exit restart gap: UNKNOWN - the boundary could not be graded "
                "(%d ungradeable, %d overlapping, %d unattributed rows over %s). "
                "This is 'we did not look', NOT 'the gap was fine'.",
                g.get("ungradeable_gaps") or 0, g.get("overlapping_gaps") or 0,
                g.get("unattributed_rows") or 0, g.get("population"))
        elif state == RESTART_GAP_WITHIN:
            logger.info("exit restart gap: within - %sms across the restart "
                        "(requirement %ss)", g.get("max_gap_ms"), req)
        return g
    except Exception:  # noqa: BLE001 — observability must never break the tick
        return None


def run_exit_loop_health_check() -> Dict[str, Any]:
    """Called once per MAIN tick. Latched alert on the exit loop going stale.

    Runs on the main thread on purpose: the main loop is the one thing still known
    to be alive when the exit loop is not, so it is the only place a check can
    observe the condition. Never raises.

    Returns the status dict plus `alerted` / `recovered` so a caller (and the
    tests) can see what it decided rather than inferring it from side effects.
    """
    try:
        st = status()
        state = st.get("state")
        # `unknown` and `never_ran` are NOT alertable: the first means we could not
        # look, the second that a boot has not finished its first pass. Alerting on
        # either would fire on every restart and teach the operator to ignore this.
        if state not in ("fresh", "stale"):
            return dict(st, alerted=False, recovered=False,
                        requirement_alerted=False, requirement_warned=False)

        prev = _load_alert_state()
        was_stale = bool(prev.get("stale"))
        is_stale = bool(st.get("stale"))
        alerted = recovered = False

        if is_stale and not was_stale:
            _send(
                "\U0001F534 [ALERT] EXIT LOOP STALE - no exit-evaluation pass "
                f"completed for {st.get('age_seconds')}s "
                f"(threshold {st.get('stale_threshold_s')}s). Open trades are NOT "
                "being evaluated for exits; SL/TP resting at the broker still "
                "apply. The main tick is alive, so this is the exit loop alone."
            )
            alerted = True
        elif was_stale and not is_stale:
            _send(
                "\U0001F7E2 [OK] Exit loop recovered - passes resumed "
                f"(last {st.get('last_pass_ms')}ms)."
            )
            recovered = True

        # --- the REQUIREMENT breach, a separate condition from staleness -------
        #
        # Latched per PROCESS, not globally. `max_interval_ms` is a per-process
        # maximum and resets on restart, so a latch keyed only on "have we alerted"
        # would go permanently silent after the first breach ever — and the live
        # trader restarts often enough (three processes in the 2026-08-15/16 window
        # alone) that this would have suppressed nearly every real breach. Keying
        # on `process_started_utc` makes a new process's breach new information.
        #
        # There is deliberately NO recovery ping: a maximum cannot decrease, so a
        # breach is a fact about the process, not a condition it can leave. That
        # also makes the alert inherently once-per-process — no rate limiter needed.
        grade = st.get("requirement_state")
        breached = grade == "breached"
        proc = st.get("process_started_utc")
        already = prev.get("requirement_breach_process")
        requirement_alerted = False
        if breached and proc is not None and already != proc:
            _send(
                "\U0001F534 [ALERT] EXIT-EVAL INTERVAL BREACHED - a live trade "
                f"went {round((st.get('max_interval_ms') or 0) / 1000.0, 1)}s "
                f"without re-evaluation (requirement {st.get('requirement_s')}s). "
                f"{st.get('interval_breaches')} breach(es) this process. The loop "
                "is ALIVE and reads 'fresh' - this is the requirement, not liveness."
            )
            requirement_alerted = True

        # --- the NEAR-MISS band, a THIRD condition -----------------------------
        #
        # A separate latch key from the breach, deliberately. A process can warn
        # first and breach later, and both are news: folding them into one key
        # would let the earlier warning suppress the later ALERT, which is the
        # dangerous direction. The reverse cannot happen -- `requirement_state`
        # holds ONE value and `breached` is tested first -- so a breached process
        # never also warns.
        #
        # WARN, not ALERT: no trade went unevaluated past the requirement, and
        # spending the ALERT vocabulary on a promise that was KEPT is how the
        # operator gets trained past the one that means it was not.
        #
        # No recovery ping, for the same reason the breach has none: a maximum
        # cannot decrease, so this is a fact about the process rather than a
        # condition it can leave. That also makes it inherently once-per-process,
        # with no rate limiter needed.
        near_miss = grade == "near_miss"
        warned_proc = prev.get("requirement_near_miss_process")
        requirement_warned = False
        if near_miss and proc is not None and warned_proc != proc:
            _send(
                "\U0001F7E1 [WARN] EXIT-EVAL INTERVAL NEAR MISS - the worst gap "
                f"between exit evaluations was "
                f"{round((st.get('max_interval_ms') or 0) / 1000.0, 1)}s against a "
                f"{st.get('requirement_s')}s requirement "
                f"({round((st.get('requirement_ratio') or 0) * 100, 1)}% of it, "
                f"n={st.get('intervals_measured')} this process). NOT a breach - "
                "the requirement was met. Read it beside n: the max is "
                "per-process and a short-lived process has not drawn the tail."
            )
            requirement_warned = True

        if is_stale != was_stale or requirement_alerted or requirement_warned:
            new_state = {"stale": is_stale,
                         "at": datetime.now(timezone.utc).isoformat()}
            # Carry BOTH latches forward across any write, or a recovery ping
            # would silently re-arm them for the same process and they would fire
            # twice. This bit me once already on the breach key; the near-miss
            # key is carried the same way rather than being trusted to a
            # different pattern.
            new_state["requirement_breach_process"] = (
                proc if requirement_alerted else already
            )
            new_state["requirement_near_miss_process"] = (
                proc if requirement_warned else warned_proc
            )
            _save_alert_state(new_state)
        gap = _check_restart_gap(st)
        return dict(st, alerted=alerted, recovered=recovered,
                    requirement_alerted=requirement_alerted,
                    requirement_warned=requirement_warned,
                    restart_gap=gap)
    except Exception:  # noqa: BLE001
        return {"state": "unknown", "stale": False, "alerted": False,
                "recovered": False, "requirement_alerted": False,
                "requirement_warned": False,
                "requirement_state": "unknown"}
