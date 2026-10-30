# APGI Operations Console

`/console` is the operator dashboard; `/v1/ops/*` is its read API (admin only, `system:admin`).
First release scope: operational health, ingestion and job status, failure investigation, freshness.

## What it shows

| Screen | Source today | Notes |
|---|---|---|
| Home panel 1 Ingestion | `participant_sessions`, `trial_events`, `acquisition_uploads` | counts link to Catalog / Failures |
| Home panel 2 Jobs | `tasks`, `webhook_deliveries` | queue backend reported as `celery` |
| Home panel 3 Data origin | `participant_sessions.data_origin` | synthetic / human-observed / unclassified (NULL, never inferred); links to filtered Catalog; origin filter also scopes panels 1 and 4 |
| Home panel 4 Scientific quality | sessions, trial events | headline indicators; full list on the Data quality screen (`GET /v1/ops/data-quality`) |
| Home panel 5 Retention/deletion | none | unknown until the deletion ledger (S3-GOV-04) exists |
| Home panel 6 Platform health | local probes; Cloud Monitoring; Cloud SQL Admin | see *Platform health* below; unconfigured or unreadable sources stay unknown |
| Catalog | sessions, chunk uploads, jobs | search, filters, session timeline |
| Failures | failed/stuck tasks, quarantined/rejected chunks, unverified chunks, unscored sessions, failed webhooks | grouped, owner role, age, retry |

Rules: unmeasurable values are `null` in the API and "unknown" in the UI (never 0); a source with
no rows, or none within twice its expected interval, is `unknown` / `stale`; every response carries
`as_of` per source; operational and scientific panels are separate (panel 4 is visually distinct).

## Phase 1 reconciliation

**§1.7 "six-panel specification".** It is not in the supplied *Data Processing System* document; it
belongs to the separate *Unified Data Platform proposal (2026-10-28)*, which the task list cites.
The panels here follow the task list's own references (panel 3 Failures-facing jobs, 4 quality,
5 retention expiries, 6 platform health: S3-GOV-05, XC-MNT-02/03, S2-OPS-02) and the brief's
"ingestion / queue / failures / storage / backups / freshness" list. **Panel numbering and contents
need confirming against §1.7** once the proposal is available.

**Cloud Tasks vs Celery.** The Data Processing System plans Cloud Tasks + Cloud Run Jobs and says to
keep Celery only if code depends on it (D6). Code does: `app/celery_app.py`, `app/tasks/*`, and
`.delay()` calls in `participants`, `trials`, `acquisition`. This release is queue-agnostic at the
UI/API layer (jobs are identified `kind:id`; Retry calls an idempotent enqueue) and reports
`queue_backend: "celery"`. Migrating means changing only `ops_monitor.retry_job` and the queue-age
source. Recommended: record D6 as "Celery now, behind a thin enqueue interface" in ADR-DP1.

## Gaps against the task list (not built)

- Catalog/ops schemas, outbox, durable job records, reconciliation (S2-CAT-01…09).
  Consequences: failures are *detected from state*
  (stuck/failed rows) rather than recorded by a watchdog; Track A/B and administration-mode filters omitted;
  correlation IDs are stored on sessions only (not yet on uploads/jobs/outbox/Cloud Tasks headers).
- Console sign-in keeps the JWT in page memory (no HttpOnly cookie, no MFA-specific UI beyond the code field); EN only.
- Role model has three roles (admin/researcher/viewer), not the eight in §1.6; ops routes are admin-only.
- Alert delivery, 14-indicator data-quality API, lineage, signed-URL download, export, deletion tracking.
- Cross-study RLS tests (S3-GOV-01) do not apply yet because ops views are admin-only and aggregate-first;
  session IDs are shown, participant IDs are not.

## Origin and correlation IDs (batch 2)

- Migration `add_session_data_origin` adds `participant_sessions.data_origin` (`synthetic`|`human_observed`|NULL)
  and `correlation_id`. Existing rows stay NULL (unclassified); run `alembic upgrade head`.
- `POST /v1/participant-sessions` accepts optional `data_origin`; `POST /v1/ops/sessions/{id}:classify`
  records it for existing sessions (admin, audited, returns the previous value). Origin is a factual
  record independent of consent.
- `CorrelationIdMiddleware` reads `X-APGI-Correlation-Id` (8-64 chars `[A-Za-z0-9._-]`, otherwise a
  UUIDv4 is minted), puts it in JSON logs and echoes it on the response. Search Cloud Logging for
  `jsonPayload.correlation_id`. Clients (web, Explorer, upload agent) still need to send the header.

## Durable jobs and correlation on uploads (batch 3)

- Migration `add_ops_jobs`: table `ops_jobs` and `acquisition_uploads.correlation_id`.
- `app/services/job_tracker.py`: a job row is written **before** dispatch (upload verification, scoring),
  so a crash before the queue call leaves a visible `queued` row. Workers mark start (30-minute lease)
  and finish; tracking is best-effort and never breaks the work. The worker restores the job's
  correlation ID into its log context.
- Watchdog `ops.sweep_jobs` (Celery beat, every 60 s) persists expired leases as `failed`, or `dead`
  once `max_attempts` (3) is spent. The console also derives `lease_expired` and `stale_queued`
  (queued > 15 min) at read time, so failures stay visible even if the sweep is not running.
- Failures groups: dead-lettered, failed, worker lost, queued-never-started; all retryable
  (`job:{id}`), carry the correlation ID, and an operator retry restores the attempt budget.
  Pre-tracking heuristics (unverified chunk, unscored session) still apply to rows with no job record.
- Not yet covered: task creation idempotency at the broker (Cloud Tasks / Celery acks), the outbox
  pattern (record and dispatch are two commits, not one), crash-injection tests (S2-CAT-05 done-when),
  and job kinds beyond upload verification and scoring (erasure, webhooks).
- Fix included: `scoring_tasks` and `deletion_tasks` were missing from Celery's `include` list, so a
  worker would not have registered `score_participant_session` or `erase_participant_data`.

## Alerting (batch 4)

- Migration `add_ops_alerts`; `app/services/ops_alerts.py`; Celery beat `ops.evaluate_alerts` every 5 min.
- Rules: stale source (a source with no data at all stays *unknown*, not an alert), dead-lettered job,
  worker lost / never started, quarantined+rejected chunks >= `ALERT_QUARANTINE_THRESHOLD` (5) in 24 h,
  failed tasks >= `ALERT_FAILED_TASK_THRESHOLD` (5) in 1 h.
- Lifecycle: one alert per standing condition (dedup key); unacknowledged alerts remind every
  `ALERT_REMINDER_HOURS` (24); acknowledged alerts stay quiet; clearing sends a "Recovered" notice. An alert
  no channel accepted is retried next cycle.
- Delivery reuses `app/middleware/alerting.py` channels: log (default on), webhooks (`ALERT_WEBHOOK_URLS`),
  email (`SMTP_SERVER` + `ALERT_EMAIL_RECIPIENTS`). The existing Cloud Monitoring email channel from the
  plan is **not** wired; Terraform alert policies (S0-DPA-03) remain to do.
- Console: Alerts screen (acknowledge), Home banner. API: `GET /v1/ops/alerts`, `POST /v1/ops/alerts/{id}:ack`.
- Tests (`tests/unit/test_ops_alerts.py`) inject faults and check routing, dedup, acknowledgment, recovery
  and retry. Not done: staging fault injection with real email delivery, overdue-purge alerts (no ledger),
  and severity-based escalation.

## Platform health (batch 5)

`GET /v1/ops/platform` (and Home panel 6). Cached for `PLATFORM_HEALTH_TTL_S` (60 s); every source has a hard 4 s limit
so a hung broker or API cannot stall the console; failures are listed by source and exception type only.

| Reading | Source | Needs |
|---|---|---|
| Request success (non-5xx), 5xx ratio, request count, last hour | Cloud Monitoring `run.googleapis.com/request_count` | `GCP_PROJECT_ID`, `CLOUD_RUN_SERVICE`, role `roles/monitoring.viewer` |
| p95 latency (worst instance) | `request_latencies`, `ALIGN_PERCENTILE_95` then `REDUCE_MAX` | same |
| Last successful backup | Cloud SQL Admin `backupRuns` | `GCP_PROJECT_ID`, `CLOUD_SQL_INSTANCE`, role `roles/cloudsql.viewer` |
| Database round-trip, Celery workers online | in-process probes | none |

Notes: "request success" is a request-based ratio, **not** uptime-check availability; no traffic gives unknown, not 100%;
worst-instance p95 is conservative, not a fleet-wide percentile. Restore-drill date stays unknown until the recovery record exists.
New alert rules: 5xx ratio >= `ALERT_5XX_RATIO` (5%, min 20 requests) and last backup older than `ALERT_BACKUP_MAX_AGE_HOURS` (36).
**Not verified against a live GCP project**: the REST calls are covered by unit tests with fake responses only; check
the filters and IAM in staging. Cloud Tasks queue age is not applicable while the queue is Celery.

## Scientific data quality (batch 6)

`GET /v1/ops/data-quality?study_id&battery_id&origin` and the **Data quality** screen return the master plan's fourteen
indicators (Monitoring chapter, "Scientific quality dashboard"), each `measured`, `partial` or `unmeasured` with the reason.
Aggregates only: no participant or session identifiers (tested). 12 of 14 have a data source today.

| # | Indicator | Status | Gap |
|---|---|---|---|
| 1 | Sessions by protocol (battery + version) | measured | |
| 2 | Complete vs interrupted | measured | |
| 3 | Missing trial events / sequence gaps | measured | |
| 4 | Signal chunk gaps | partial | acquisition sessions are not linked to participant sessions, so filters do not apply |
| 5 | Device / browser / tier | partial | tier and client-reported browser family; device model not stored |
| 6 | Administration mode and app version | measured | client-reported at session creation; sessions created before the migration, or by clients that do not send it, show "not reported" |
| 7 | Clock synchronisation | measured | from per-session timing-error summaries |
| 8 | Validity-screen exclusion rates | measured | unscreened sessions reported separately |
| 9 | Analysis completion | measured | includes durable scoring-job states |
| 10 | Protocol / battery / gate versions | partial | gate versions not recorded |
| 11 | Confirmatory / holdout separation | partial | per-participant partition is stored and set once; no assignment policy is automated |
| 12 | Prediction and K-gate classifications | **unmeasured** | needs S4-ANL-05 validation results |
| 13 | Reproduction success | **unmeasured** | needs dataset versions (S4-ANL-03) |
| 14 | Retention per wave | measured | counts completed `session_index`; wave schedule not stored |

Not done: dataset-version scoping (the plan scopes by dataset version; this scopes by study, protocol and origin),
small-cell suppression for tiny strata, and the Track A/B filter.

## Session metadata and partitions (batch 7)

- Migration `add_session_metadata`: `participant_sessions.administration_mode` (`web` | `native_macos`), `app_version`,
  `browser_family`; `study_enrollments.partition` (`development` | `confirmation` | `holdout`).
- `POST /v1/participant-sessions` accepts the three session fields (validated: fixed mode list, restricted character
  sets). **Clients must send them**: the web app and Explorer need a small change; until then indicator 6 reports "not reported".
- `POST /v1/ops/enrollments/{id}:partition` (admin, audited) assigns a partition **once**; a second call is 409. This is
  deliberate: re-assigning after data exist could move holdout participants into development analyses.
- Not decided here (a scientific call, not an engineering one): *who* is assigned to which partition and in what ratio.
  The plan's 70/30 holdout and by-participant stratification are not automated; no assignment is made automatically.

## Governance (batch 8, S3-GOV-01/02/03 slice)

**Release dependency.** The plan says no production console route opens on human data until isolation, purpose-specific
authorization, sensitive-access auditing, exposure gates and withdrawal handling pass their tests. This batch covers
the parts below; the **gaps listed after it must be closed first**.

*Scopes.* New permissions `ops:read`, `ops:write`, `ops:quality`, held by the admin role only. Every `/v1/ops` route needs
`ops:read`; state changes (retry, acknowledge, classify origin, assign partition) need `ops:write`; study-level scientific
data (`data-quality`, `studies`) needs `ops:quality`. `system:admin` alone (e.g. an API key) **no longer** opens the console.
`test_route_scopes_are_pinned` forces any new route to declare its scope.

*Purpose-bound, audited reads.* A session record needs `purpose` (`operations_support` | `research_qa` |
`security_investigation`); the console asks before opening one. Session reads, catalog searches (filters only, never the
search text), failure-list reads and data-quality reads write signed audit entries with actor, purpose and scope. The audit
write is **not** best-effort: if it fails the read returns 500 instead of being served unaudited.

*Withdrawal.* Research-purpose views (data-quality and Home panel 4) exclude (a) erased participants and (b) any session
that is not synthetic and lacks an active, current-version research-participation consent; unclassified sessions are not
assumed synthetic. The number withheld is shown. Origin is never changed by consent state. Operational views keep working
after research is declined or withdrawn (product processing continues); erased participants disappear from every view.

*Access-denial suite.* `researcher`, `viewer`, unknown role, no roles and unauthenticated requests are refused on
every `/v1/ops` route; a read-only key cannot write or see quality data.

**Not done (still blocking human-data release):**
- The plan's eight roles. Only admin/researcher/viewer exist; researchers get **no** console scope on purpose, because
  there is no study-ownership model, so granting `ops:quality` would let one researcher read every study.
- Study-level row security (S2-CAT-01 RLS) and cross-study isolation tests; today access is all-or-nothing admin.
- Exposure gates (`ExposureGate.emit()`) on console responses; responses are aggregates plus session IDs, but no gate runs.
- Withdrawal during in-flight work (workers re-checking consent before upload, scoring, export): the job code is unchanged.
- Aggregate-only operator endpoints (no session IDs) for a lesser operator role; small-cell suppression.
- Purpose is declared by the user, not authorised against a grant; it is recorded, not enforced.

## Use

Sign in at `/console` with an admin account. Tests: `pytest tests/unit/test_ops_console.py`.
