# The exit-eval 60 s breaches — root cause

> **Doc status:** `live` · category `evidence` · last verified `2026-09-10` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)
> Produced 2026-09-10 by `session_01Ww2pZVK5VUqV8xFFtTs9B1` under
> `WO-20260910-ROOT-CAUSE-THE-EXIT-EVAL-60S-BREACHES` /
> `IN-20260903-TRADING-SYSTEM-HEALTH` (cycle `CY-20260906-TRADING-TRUTH`).
>
> **Scope: INVESTIGATE AND PROPOSE.** Nothing here was armed, flipped or applied.
> No env knob was set, no order path changed, no order placed, modified or
> cancelled on any account. Every live read was a GET.

---

## 0. The one-paragraph answer

**The two breach populations have DIFFERENT causes and must not be given one
story.** The **within-process** breaches are 1157 of 1218 (95.0 %) a closed four-day episode
(2026-08-19 → 08-22) whose fix landed, plus a small live residual confined to
the **IBKR nightly reset window**, where each IB-routed open package costs a
serialized **29.0 s** pinned-thread queue timeout so a pass costs
`n_IB_packages × 29 s`. The **restart-boundary** breaches are not an episode
at all and are not a deploy-speed problem: the gap is
`residual cadence sleep (0–30 s) + systemd downtime (~0.3 s) + startup-to-thread
(6–15 s) + COLD first pass (12.7–16.9 s)`, which sums to a range of **≈19–60 s
against a 60 s requirement**. The system has, by construction, **almost no
headroom on a restart**, and at ~18 restarts/day it breaches regularly. That
arithmetic — not any single defect — is the cause.

---

## 1. Population — stated first, because it is why this only surfaced now

All figures below are **MEASURED** from
`GET https://ict-bot.duckdns.org/api/bot/exit-interval/soak` (whole-file
`summary`, plus `breached_only=true&limit=2000`), **read 2026-09-10T06:13Z**,
served by the Caddy host directly. The route's `summary` is computed over
**every row on disk**; the `records` page with `breached_only` returned
**1218 of 1218** breaching rows and the `restart_gap.gaps` array returned
**444 of 444** measured boundaries — so both populations below are **COMPLETE
CENSUSES, not samples**.

| | |
|---|---|
| rows on disk | 68,312 |
| intervals measured | 67,867 |
| processes seen | 445 |
| within-process breaches | 1,218 (**1.795 %**) |
| max interval | 120,189.1 ms (2026-09-04T04:24:12Z) |
| restart boundaries | 444 measured · `ungradeable 0` · `overlapping 0` · `unattributed_rows 0` |
| restart-boundary breaches | 77 (**17.3 %**) · max 210,409.2 ms (`3.51×`) |
| span | 2026-08-16T11:48Z → 2026-09-10T06:12Z (24.7 days) |

Cross-check (RULE ONE, arithmetic rather than re-reading):
`68,312 − 67,867 = 445 = processes_seen` — every first-pass row is excluded from
the interval denominator exactly once. The file reconciles.

---

## 2. Within-process breaches — 1157 of 1218 (95.0 %) are HISTORY, and the row's headline needs correcting

### 2.1 The mechanism, established over the complete breach population (n = 1218)

Every breaching interval was measured against its own `pass_ms`:

| test | result |
|---|---|
| breaching intervals whose `pass_ms > 30 s` | **1218 / 1218 (100 %)** |
| median `pass_ms / interval_ms` | **1.000** |
| `interval_ms − pass_ms` — max | **29,351 ms** |
| rows where that residual exceeds 31 s | **0** |
| breaches whose pass alone exceeded 60 s | 1128 / 1218 |

The loop is `slack = EXIT_LOOP_INTERVAL_SECONDS − elapsed`, so
`interval = (30 s − prev_pass) + cur_pass`. The residual never once exceeds one
cadence period. **There is no scheduler stall, no thread starvation and no
sleep overrun anywhere in the population — every within-process breach is a
slow PASS.** That closes off an entire class of hypothesis.

### 2.2 It is not a steady rate — it is one four-day episode

| day | breaches | | day | breaches |
|---|--:|---|---|--:|
| 2026-08-16 | 14 | | 2026-08-29 | 1 |
| 2026-08-17 | 10 | | 2026-09-01 | 2 |
| 2026-08-18 | 1 | | 2026-09-03 | 1 |
| **2026-08-19** | **190** | | 2026-09-04 | 3 |
| **2026-08-20** | **518** | | 2026-09-05 | 2 |
| **2026-08-21** | **449** | | 2026-09-06 | 1 |
| 2026-08-22 | 22 | | 2026-09-07 | 1 |
| 2026-08-23 → 08-28 | **0** | | 2026-09-10 | 3 |

**1182 of 1218 (97.0 %) fall before 2026-08-22**; 1157 (95.0 %) in the four days
2026-08-19 → 08-22. Since 2026-08-23: **14 breaches in 18 days**, on 8 of those
18 days, zero on the other 10.

⚠️ **THE LIFETIME 1.8 % IS A POOLED RATE OVER A BIMODAL POPULATION AND MUST NOT
BE QUOTED AS A CURRENT RATE.** Against a denominator of ~2,800 intervals/day the
post-2026-08-23 rate is of order **0.03 %**, roughly **60× lower** than the
lifetime figure.

⚠️ **CORRECTION TO THIS WORK'S OWN INHERITED FRAMING.**
`BL-20260910-EXIT-EVAL-60S-BREACHES-DID-NOT-CEASE-1216-LIFETIME-MAX-120S-AND-EVERY-PRIOR-READ-WAS-A-TAIL`
reads the move
1204 → 1216 as "the breaches did not cease". That is true **of the count** and
misleading **about the rate**: +14 over 16 days is the residual, not a
continuation of the episode. `BL-20260825-…-CESSATION-UNCONFIRMED` was closed
on a cessation claim that was **substantially correct** — its clause 2
("sustained absence over a population that could have shown it") is what was
never satisfied, and this document supplies the population it asked for.
Equally: the cessation is **not total**, so closing it outright would also have
been wrong. Both halves are recorded.

### 2.3 The live residual is the IBKR reset window, and it was already filed

The 14 post-fix breaches cluster where the 1204 pre-fix ones do not:

| population | share in the 03–05 h UTC hours |
|---|--:|
| pre-2026-08-23 (n = 1204) | **8.6 %** (flat across all 24 hours) |
| post-2026-08-23 (n = 14) | **71.4 %** (10 of 14, all in 04:00–05:00 Z) |

That is an ~8× concentration into IBKR's documented ~03:45–05:45 UTC reset
window — the same window `CLAUDE.md` already gives
`--suppress-window-utc 03:45-05:45` for.

**Direct evidence, not inference.** `/api/diag/journalctl?unit=ict-trader-live`
over `2026-09-10T04:19:25Z → 04:22:15Z` — the window of the `pass_ms 91,187 ms`
breach at 04:21:01Z:

```
04:19:31  Error 1100, reqId -1: Connectivity between IBKR and Trader Workstation has been lost.
04:20:00  IBMarketData.get_ohlcv timed out waiting for the pinned IB thread
          (symbol=MES timeframe=1d, waited 29.0s) — another IB request is still ...
04:20:00  order_monitor: mes_trend_long_1d ... candles=None (monitor will short-circuit)
04:20:29  ... timed out waiting for the pinned IB thread (symbol=MHG timeframe=1d, waited 29.0s)
04:20:30  ... timed out waiting for the pinned IB thread (symbol=MES timeframe=5m, waited 29.0s)
04:20:58  ... timed out waiting for the pinned IB thread (symbol=MGC timeframe=1h, waited 29.0s)
04:20:58 →04:21:01  the eleven NON-IB packages (GLD, IEF, QQQ, USO, SLV, GDX, IAUM, ADAUSDT …)
                    each complete in well under a second
```

**Arithmetic reconciliation:** `3 × 29.0 s + ~4 s of non-IB work = 91 s`,
against the recorded `pass_ms` of **91,187 ms**. The 29.0 s is not a guess — it
is printed by the log line itself, and it is
`_IB_FETCH_QUEUE_TIMEOUT_S = IB_FETCH_TIMEOUT_S × 3.0 + 5.0 = 8 × 3 + 5`
(`src/exchange/ib_connector.py:113-117`).

**So: IB fetches are serialized on one process-wide pinned thread, the exit pass
walks packages one at a time, and during the reset window EVERY IB fetch is
doomed in advance (Error 1100). The pass therefore costs
`n_IB_packages × 29 s`, and crosses the 60 s requirement at THREE IB packages.**

⚠️ **THIS IS A RECURRENCE AGAINST A ROW MARKED `resolved`.**
`BL-20260816-IB-QUEUE-TIMEOUT-EXCEEDS-EXIT-BUDGET` states this mechanism
exactly ("*a SINGLE queue timeout inside one exit pass costs 48.3 % of the
operator's 60s requirement; … three exceed it outright at 87s*") and its
**criterion 1 was "COUNT IT FIRST — how OFTEN it fires is UNMEASURED"**. The row
is `resolved` with no `resolved_at` and no recorded resolution, while the
mechanism is still firing. **This document supplies the denominator criterion 1
asked for**, and the row is re-opened rather than a fourth id being minted.

---

## 3. Restart-boundary breaches — a persistent structural condition, and NOT a deploy-speed problem

### 3.1 It did not go away with the within-process episode

| window | restarts | breached | rate | max |
|---|--:|--:|--:|--:|
| 2026-08-16 → 08-22 | 119 | 57 | 47.9 % | 127.9 s |
| **2026-08-23 → 09-10** | **325** | **20** | **6.2 %** | **210.4 s** |

Post-fix the distribution is: min 17.5 s · p25 33.2 s · **median 40.5 s** ·
p75 49.6 s · p90 56.0 s · p95 60.9 s · max 210.4 s. Eighteen of the twenty
post-fix breaches sit in a narrow **60.0 – 71.4 s** band; only two are large
(112.6 s on 2026-09-07, 210.4 s on 2026-08-26).

⚠️ **`OI-20260910-…`'s expectation is CONTRADICTED and should be corrected, not
treated as an instrument fault.** It records the 2026-09-09 reading of "11 gaps
of 25.3–53.9 s, 0 of 11 over 60 s" and predicts `within`. Over the complete
444-boundary population the true rate is 17.3 % lifetime / 6.2 % post-fix. The
2026-09-09 reading was a **one-day sample of a distribution whose 94th
percentile is the requirement** — it was not wrong, it was under-powered. The
mechanism started working; `0 → non-zero` here is the instrument speaking, not
a regression.

### 3.2 The decomposition — measured end to end on four consecutive restarts

`/api/diag/journalctl?unit=ict-trader-live` with tight `since`/`until` windows
around four restart boundaries on 2026-09-10, joined to the soak's own
`from_utc` / `to_utc` and to the successor's `first_pass_of_process` `pass_ms`
(`/api/diag/log_file?name=exit_interval_soak&lines=1000`):

| term | what it is | w1 | w2 | w3 | w4 |
|---|---|--:|--:|--:|--:|
| **A** | predecessor's last completed pass → systemd `Stopping` — the **un-slept remainder of the 30 s cadence** | 30.3 s | 2.1 s | 19.0 s | 14.1 s |
| **B** | `Stopping` → `Started` — **actual systemd downtime** | ~0.3 s | ~0.3 s | ~0.3 s | ~0.3 s |
| **C** | `Started` → `exit-evaluation loop DECOUPLED` — python import, startup validation, DB init, boot audit, boot reconcile, **per-account leverage pre-flight** | 7.1 s | 7.0 s | 15.0 s | 6.0 s |
| **D** | thread start → first pass completes — the **COLD first pass** (warm mean is ~2.2 s) | 16.1 s | 12.7 s | 12.8 s | 16.9 s |
| | **= observed gap** | **53.5 s** | **21.8 s** | **46.8 s** | **37.0 s** |

**A + B + C + D reconciles to the observed gap on all four boundaries** (53.5,
21.8, 46.8, 37.0 — exact). That is the arithmetic cross-check, not a re-read.

**What each term means for a remedy:**

- **A is the dominant variable term and it is not downtime.** The exit loop had
  just evaluated and was correctly idle; the deploy lands at a phase
  uncorrelated with the loop, so A is ~uniform on `[0, 30 s]`. This is what
  gives the gap distribution its ~30 s-wide plateau (the 5 s histogram is flat
  from 20 s to 55 s). **Even a zero-downtime deploy would not remove it.**
- **B ≈ 0.3 s on all four.** The process exits cleanly on SIGTERM
  (`Deactivated successfully` in the same second; no 90 s stop timeout).
  **Deploy speed is NOT the cause and must not be proposed as the fix.**
- **C is 6–15 s, and the tail is identifiable.** In w3 (15 s), the segment from
  `boot_reconcile` to `DECOUPLED` was **10.0 s** and consisted of **15
  `set_leverage` pre-flight calls across 3 Bybit accounts — every one returning
  `retCode=110043 already set (idempotent)`** — including a 5 s stall on a
  Bybit `recv_window` timestamp error at 04:45:42. In w1/w2/w4 the same segment
  was 3.0 / 3.0 / 2.0 s. **The exit loop waits on a block of idempotent no-op
  leverage calls it does not depend on.**
- **D is the largest FIXED term**: the cold first pass is 12.7–16.9 s against a
  ~2.2 s warm pass — the `BL-20260609-001` cold-cache shape, every market-data
  cache, connector memo and IB probe cache empty.

### 3.3 The cause, stated

`C + D` is a fixed floor of **≈19–30 s**. `A` adds a uniform **0–30 s**. So the
restart gap is **≈19–60 s by construction, against a 60 s requirement** — the
top of the system's own designed range **is** the requirement. At ~18.1
restarts/day, 6.2 % of them land in the overlap. **The 20 post-fix breaches are
the right tail of a distribution the design puts flush against the limit; they
are not a separate defect, and hunting one per breach would find nothing.**

### 3.4 The exposure figure nobody had

Summing every measured boundary: **20,784 s of restart-boundary gap over the
24.7-day span = 0.973 % of wall clock, a mean 841 s/day.** Post-fix:
**13,584 s over 18.0 days = 756 s/day (12.6 min/day), 0.875 % of wall clock,
across 18.1 restarts/day.** That is time in which **no open position's exit was
evaluated by anything** — the broker-side brackets still rest, but the bot-side
exit decision does not run. Reported here because the breach *rate* understates
it: 93.8 % of restarts are compliant and still cost ~40 s each.

---

## 4. What I could NOT establish

Stated plainly rather than inferred, per RULE ONE.

1. **The two large post-fix outliers (210.4 s on 2026-08-26, 112.6 s on
   2026-09-07) are UNEXPLAINED.** The `A+B+C+D` model caps a nominal restart
   near 60 s, so both exceed it by a term this work has not named. The
   systemd journal does not retain back to either date (a `since=2026-08-26`
   query returned one line), so the decomposition that worked for §3.2 is not
   available for them. **They may be a different mechanism entirely.** They are
   2 of 325 post-fix boundaries.
2. **Whether C's `set_leverage` block is the tail driver in the breaching
   restarts specifically.** It was measured on four boundaries, none of which
   breached (53.5 / 21.8 / 46.8 / 37.0 s). The 10 s tail is real and observed;
   attributing the 60–72 s breaches to it is an **INFERRED** step from the
   decomposition, not a measurement of those events.
3. **A per-day denominator for the within-process rate.** `breached_only` does
   not return the non-breaching rows and the route has no offset, so the
   ~2,800 intervals/day denominator in §2.2 is derived from
   `68,312 rows / 24.7 days`, not counted per day.
4. **The firing RATE of `_IB_USAGE_LOCK_WAIT_S` (24.0 s)**, the sibling
   log-only path named in `BL-20260816`. Only the `_IB_FETCH_QUEUE_TIMEOUT_S`
   path appears in the window I read.
5. **Why the trader restarts 18.1×/day.** The non-runtime skip in
   `scripts/deploy_pull_restart.sh` (docs/ tests/ .claude/ .github/ top-level
   *.md) exists and works; whether the remaining restarts are all genuinely
   runtime-affecting was not measured against the merge history.

---

## 5. A third finding, FILED not built — `process_started_utc` does not record when the process started

`exit_loop_health.record_pass` sets `_started_utc` on its **first invocation**:

```python
now_utc = datetime.now(timezone.utc).isoformat()
...
if _started_utc is None:
    _started_utc = now_utc      # <- the FIRST PASS'S COMPLETION, not the process start
```

**MEASURED over the complete 444-boundary population**: `to_utc − to_process`
is 0.280 – 55.121 ms (mean 2.026 ms). The field equals the first pass's
completion time to within milliseconds, always.

**Consequence, measured**: in w1 the process actually started at
`00:18:48` and `process_started_utc` reads `00:19:11.215` — **23.2 s late**,
i.e. late by exactly `C + D`. Any session dating a process from this field
dates it late by the boot-plus-cold-pass time, and any session trying to
measure startup cost from it gets ~0. `exit_interval_soak.py`'s own docstring
dates its five observed processes from these values.

This is `UNPROVENANCED DIAGNOSTIC OUTPUT` **sub-class A** as CLAUDE.md defines
it — the label names a quantity the code does not compute. **It does not
invalidate the gap measurement**, which correctly joins last-completion to
first-completion; it invalidates the field's NAME. Filed as
`BL-20260910-PROCESS-STARTED-UTC-IS-THE-FIRST-PASS-COMPLETION-NOT-THE-PROCESS-START`.
Not fixed here: renaming a soak field is a schema change with existing readers,
and this session's object is investigate-and-propose.

---

## 6. The remedies — PROPOSED, NOT APPLIED. Tier-2, operator's call.

> ⚠️ **ANSWERED 2026-09-10T07:52Z, AND THIS SECTION'S HEADING IS NOW HALF STALE — do not
> re-quote "PROPOSED, NOT APPLIED" as covering both remedies.** The operator chose
> **`r2_only`** on `DEC-20260910-EXIT-EVAL-60S-REMEDY` from the four-option popup.
>
> * **R2 is APPROVED AND BUILT** — MI-240, PR #11738. Read that PR, not this section,
>   for what actually shipped; this text is the PROPOSAL and the two can drift.
> * **R1 is DECLINED.** It was offered and not chosen. It must not be armed, must not
>   ride along on another change, and **`both` is not a safer reading of `r2_only`.**
> * **The fourth option — whether the restart boundary is in scope for the 60 s promise
>   at all — was NOT CHOSEN, so §7's question is only PARTLY answered.** It is open, not
>   settled by implication. Putting it back to the operator would be a NEW decision
>   request.
>
> ⚠️ **AND R2 BEING BUILT IS NOT THE 60 s PROMISE BEING KEPT.** It targets the
> within-process reset-window residual of §2.3 only. The restart-boundary population of
> §3 — 444 of 444 measured boundaries, 77 breaching (17.3 %), max 210,409.2 ms — is
> **untouched** by it. Merged is not deployed and deployed is not observed:
> `OI-20260910-IB-PER-PASS-BREAKER-ARMED-AND-HAS-SKIPPED-NOTHING` carries the fleet half.
>
> *The section below is preserved verbatim as the record of what was put to the operator.*


Each is exact, targets a term measured above, and carries a **one-env-flip
rollback with no redeploy** in the sanctioned `EXIT_LOOP_DECOUPLE_DISABLED`
shape (a default-OFF kill-switch over an ON capability — **not** a default-off
`*_ENABLED` gate in front of a required capability, which the Prime Directive
forbids).

### R1 — for the restart boundary: start the exit loop before the leverage pre-flight

**Change.** In `src/main.py::main()`, move `_start_exit_loop(settings)` from its
current position (after `_apply_per_account_leverage()`,
`_build_exchange_adapter()`, `_build_telegram_client()`) to **immediately after
the boot reconcile and immediately before `_apply_per_account_leverage()`**.

**Why it is safe in that exact position, and not one line earlier.** The exit
loop needs only `settings` — `_build_monitor_ohlcv_fetcher` builds its own
connectors lazily and never touches `exchange_client` or `telegram_client`. But
it must stay **after** `boot_reconcile`: starting exit evaluation while ghost
trades and untracked positions are still being reconciled would let a pass
evaluate an exit for a journal row the reconciler is about to void. **Do not
move it earlier than boot_reconcile for a few more seconds.**

**Measured effect.** Removes the `boot_reconcile → DECOUPLED` segment from term
C: **2.0 / 3.0 / 3.0 / 10.0 s** on the four measured boundaries (median ~3 s,
tail 10 s). It targets the tail, which is what breaches. **Predicted effect is
INFERRED, not measured**: a ~3 s median leftward shift clears roughly the 6 of
20 post-fix breaches sitting at 60.0–63.0 s, and more whenever the 10 s
leverage tail was the cause.

**Rollback.** `EXIT_LOOP_EARLY_START_DISABLED` truthy → the original ordering,
byte-for-byte. One env flip plus a restart, no redeploy.

**Cost.** The cold first pass then overlaps the leverage pre-flight on a 2-core
box. Both are network-blocked rather than CPU-bound, but this is the one real
risk and it should be watched on `tick_cost` after arming.

### R2 — for the IBKR reset window: a per-pass IB circuit breaker

**Change.** In the exit-evaluation pass, once **one** IB-routed fetch has
returned a pinned-thread queue timeout, **skip the remaining IB-routed packages
in THAT pass** and hand them `candles=None`.

**Why this and not a shorter timeout.** `BL-20260816`'s own criterion 2 warns:
*"do not optimise the interval into a MONITOR BLIND."* Lowering
`IB_FETCH_QUEUE_TIMEOUT_S` would start discarding genuinely queued healthy
fetches. This changes **nothing about what any package receives** — a package
skipped by the breaker gets exactly the `candles=None` it would have received
29 s later, and `order_monitor` already short-circuits on it, logging the same
line. It bounds the **cost** of an outcome that is already determined. Same
shape as `EXIT_ANCHOR_FETCHES_PER_TICK`'s per-tick budget.

**Predicted effect (INFERRED from the §2.3 arithmetic).** Pass cost during the
reset window falls from `n × 29 s` to `29 s + non-IB work` ≈ 35 s, i.e. inside
the requirement at any number of IB packages. On the 2026-09-10T04:21 event
that is 91.2 s → ~33 s.

**Rollback.** `EXIT_LOOP_IB_BREAKER_DISABLED` truthy → every IB package pays its
own queue timeout, byte-for-byte today's behaviour. One env flip, no redeploy.

**Cost, stated rather than hidden.** During a *transient* single-fetch queue
timeout that is not a venue outage, the breaker would skip later IB packages
that might have succeeded — they get `candles=None` for one pass and are
retried 30 s later. That is a real, bounded degradation and is the reason this
is Tier-2 and not a session's own call.

### Considered and REJECTED, with the reason

| option | why not |
|---|---|
| **Shorten `EXIT_LOOP_INTERVAL_SECONDS` 30 → 15/20** | It halves term A, the dominant one — genuinely the biggest single lever. But it also **doubles the pass rate**, hence the fetch and broker load, and multiplies the reset-window exposure in §2.3 by the same factor. Two June 2026 wedges came from exactly this direction. Not proposable without measuring the load first. |
| **Speed up the deploy / blue-green restart** | Term B is **~0.3 s**. There is nothing to win. |
| **Pre-warm the market-data caches at boot** | Attacks term D, the largest fixed term — but a fetch burst at boot on a cold process is the `BL-20260609-001` cold-start wedge shape verbatim. |
| **Have the deploy wait for the loop to complete a pass before restarting** | Would remove term A almost entirely and is elegant, but couples `deploy_pull_restart.sh` to runtime state and needs its own timeout-and-restart-anyway path. Named here so it is not lost; not proposed. |
| **Record a DECIDED that the restart boundary is excluded from the 60 s requirement** | A legitimate operator answer and deliberately left on the table — but it should be a **recorded decision with its consequence stated** (756 s/day unevaluated), never a silent redefinition. |

---

## 7. Open question for the operator

> ⚠️ **PARTLY ANSWERED — see the note at the head of §6.** *"Which, if any, of R1 / R2 to
> arm"* was answered `r2_only` at 2026-09-10T07:52Z. *"Whether the restart boundary is in
> scope for the 60 s promise at all"* was **NOT** answered and remains open.


**Which, if any, of R1 / R2 to arm — and whether the restart boundary is in
scope for the 60 s promise at all.** R1 and R2 are independent and target
different populations; R2 is the larger effect on the larger residual, R1 is the
smaller effect on the persistent one. Both are Tier-2. Neither has been applied.
