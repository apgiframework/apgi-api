# APGI Operations Console

`/console` is the operator dashboard; `/v1/ops/*` is its read API (admin only, `system:admin`).
First release scope: operational health, ingestion and job status, failure investigation, freshness.

## What it shows

| Screen | Source today | Notes |
|---|---|---|
| Home panel 1 Ingestion | `participant_sessions`, `trial_events`, `acquisition_uploads` | counts link to Catalog / Failures |
| Home panel 2 Jobs | `tasks`, `webhook_deliveries` | queue backend reported as `celery` |
| Home panel 3 Data origin | `participant_sessions.data_origin` | synthetic / human-observed / unclassified (NULL, never inferred); links to filtered Catalog; origin filter also scopes panels 1 and 4 |
| Home panel 4 Scientific quality | sessions, trial events | 4 of 14 indicators measurable: completion, interruption, missing events (sequence gaps), device-tier distribution |
| Home panel 5 Retention/deletion | none | unknown until the deletion ledger (S3-GOV-04) exists |
| Home panel 6 Platform health | none | unknown until Cloud Monitoring / Cloud SQL Admin are connected (S2-OPS-02) |
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

## Use

Sign in at `/console` with an admin account. Tests: `pytest tests/unit/test_ops_console.py`.
