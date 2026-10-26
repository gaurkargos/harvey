# Harvey heartbeat health — 24-48h check

As of: **2026-10-26 15:12 IST** (window: 2026-10-24 15:12 → 2026-10-26 15:12 IST).
Sources: `heartbeat-run.log` (4,728 lines, 2026-10-24 11:27 → 2026-10-26 15:00 IST) and read-only queries of `data/harvey.db` (DB timestamps are UTC; shown here converted to IST).
Read-only task: no config changed, no sends (`channels.email.enabled` remains `false`, verified), no `.env` values read or printed. Nothing under `harvey/` modified except this report.

## 1. Cycle completion rate: 100%

| Window | Cycles started | Cycles completed | Rate |
|---|---|---|---|
| Last 24h | 58 | 58 | **100%** |
| Last 48h | 64 | 64 | **100%** |

- Only inter-cycle gap in 48h: 2026-10-25 21:49 → 2026-10-26 07:04 (9.3 h) = configured quiet hours (22:00–07:00 IST). Not a failure.
- Zero `Traceback` / ERROR / CRITICAL lines anywhere in the 48h window. Every started cycle logged `Cycle complete. Sleeping for 15 minutes.`
- Recent cycles decide `write_campaign`; Writer correctly no-ops ("No prospects with deliverable (verified/risky) emails yet") and Sender skips (email channel disabled).

## 2. Error counts by type (per calendar day, IST)

| Error | 10-24 | 10-25 | 10-26 | Trend |
|---|---|---|---|---|
| **Serper HTTP 400** | 12 | 90 | 75 — all at/before 13:11 IST | **Stopped.** Last 400 at 13:11:30; from the 13:24:36 restart, 12/12 calls returned **200** (last 14:13:46) |
| **DDG HTTP 202 (throttle)** | 11 | 88 | 84 | Persistent — nearly every DDG call; non-fatal (Bing fallback 200; Serper now the working primary) |
| Site HTTP 403 (crawler-blocked) | 16 | 0 | 12 | Non-fatal (e.g. kreatecube.com, re-thinkingthefuture.com → "unreachable") |
| Site HTTP 429 (rate limit) | 0 | 0 | 10 | Non-fatal (e.g. marble-lotus.com) |
| Site unreachable (other) | – | – | 5 | Non-fatal |
| App errors (Traceback/ERROR/CRITICAL) | 0 | 0 | 0 | — |

**Serper root cause (corrected diagnosis):** the key was never rejected. Free Serper accounts reject `num > 10` (`"Query pattern not allowed for free accounts."`); Scout sent `num=20`. The fix — cap Serper `num` at 10 in `harvey/agents/scout.py` + `harvey/collectors/discover.py` — is in the working tree but **uncommitted** (git status confirms both files modified). Heartbeat restarted 13:24:36 IST; every Serper call since is 200.

## 3. New observations: YES — but in waves, not per cycle

- DB now: **138 companies / 1,399 observations / 3 prospects** (vs 69 / 610 / 0 on 2026-10-25).
- +69 companies, +789 observations and +3 prospects all landed **2026-10-26 14:06–14:17 IST** (`runs` table: `discover` via serper, 16 queries, 84 records, est. $0.048, then two `profile` runs totalling 705 records).
- Observations come only from discover/profile runs — routine cycles add none (nothing left to observe). Last observation: 14:17 IST.
- The 3 prospects are Navyug Infosolutions' CEO/CTO/COO, all `email_status = guess` → not sendable without a verifier key. 0 conversations, 0 outbox items.

## 4. What this means for the pending decision

1. **Serper: no decision, no new key.** The 400s were `num>10` on a free account, already fixed — but the fix is uncommitted; commit `scout.py`/`discover.py` so it isn't lost.
2. **The real blocker is email verification:** Hunter/Instantly keys empty and no Reoon/ZeroBounce → every contact stays `guess` and is never sent. Decide: add ONE verifier key (Reoon 600 free/mo or Hunter 50 free/mo) via ArgosVault → `.env` sync.
3. ICP is still mis-aimed (queries quote the literal industry label + "Student" titles → 0 LinkedIn hits); apply the ICP rewrite from `reports/2026-10-26-pipeline-review.md`.
4. **Instantly: no decision needed now** — sending stays OFF until a mailbox exists and Gauransh approves.
5. Pool refill works via manual `harvey discover --city ...` (~$0.05 per ~69 companies) — schedule it periodically; the heartbeat won't fill the pool by itself.

## Notify

Harness notify **unavailable** from this session (the notify tool is permission-denied in don't-ask mode), so it was **not sent** — recorded here as the task requires. The message it would have carried: "64/64 cycles completed (100%; only pause = quiet hours), 0 app errors; Serper 400s stopped 13:24 IST (12/12 calls 200 since); DB now 138 companies / 1,399 obs / 3 prospects, all 'guess' emails — decision needed: one verifier key (Reoon or Hunter)."
