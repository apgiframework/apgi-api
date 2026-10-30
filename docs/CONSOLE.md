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

## Use

Sign in at `/console` with an admin account. Tests: `pytest tests/unit/test_ops_console.py`.
