# Changelog

All notable changes to the APGI System API are recorded here. Versions follow
the apgiframework/apgi-framework release they ship alongside.

## [Unreleased]

- Operations console at `/console` with a read-only `/v1/ops/*` API (home panels, catalog,
  failures with retry, per-source freshness). Unmeasurable values are `null`/unknown. See `docs/CONSOLE.md`.
- `participant_sessions.data_origin` / `correlation_id` (migration `add_session_data_origin`),
  `POST /v1/ops/sessions/{id}:classify`, and `X-APGI-Correlation-Id` middleware.
- Durable `ops_jobs` records for upload verification and scoring with a lease watchdog
  (`ops.sweep_jobs`); Celery now registers the scoring and erasure tasks.
- Operational alerts (`ops_alerts`): stale sources, dead/lost jobs, quarantine and task-failure rates,
  with dedup, acknowledgment, reminders and recovery notices; Alerts screen in the console.
- Platform-health panel: Cloud Monitoring request metrics, Cloud SQL backup status and local probes
  (`GET /v1/ops/platform`), plus 5xx-ratio and stale-backup alert rules.
- Data-quality screen and `GET /v1/ops/data-quality`: the fourteen scientific indicators with explicit
  measured / partial / unmeasured status and study, protocol and origin filters.
- Session metadata (administration mode, app version, browser family) and set-once enrollment partitions
  (migration `add_session_metadata`); data-quality indicators 6 and 11 now report.
- Console governance: separate `ops:read|write|quality` scopes (admin only), purpose-bound audited session reads
  that fail closed, withdrawal-aware research views, and an access-denial test matrix.
- Server-side effort/fraud/validity screening (`app/services/screening.py`):
  fast responses, invariant responding, straight-lining/zig-zagging, failed
  attention checks, form-layer bot signals, and duplicate device-fingerprint /
  email-hash flags (flag, never block). Outcomes are stored on the session
  (`screening`, `norm_eligible`); `POST /v1/participant-sessions/{id}/quality`
  submits telemetry; sessions completed without it are kept out of norms.
- Versioned `tier_config` device tiers (A/B/C) stored per session together with
  `tier_config_version` and the per-session timing-error distribution.
- Norms count only norm-eligible sessions: one per participant per battery
  version, no tier C, no bot or failed-attention sessions.
- `GET /v1/studies/{id}/exclusion-report` for the dataset card.
- Parallel-form order is counterbalanced (Williams square) and persisted at
  enrolment (`study_enrollments.form_order`); sessions report
  `expected_form_label`. Migration `add_session_screening_and_form_order`.
- `faker` added to `requirements-dev.txt` (unit tests import it).

## [1.1.0] — 2026-10-17

Aligned with apgi-framework 1.1.0 (protocol specification v1.1.0).

- Release version bumped to 1.1.0 in `app.__version__` and `pyproject.toml`.
- `app.__version__` is now the single in-code source: the FastAPI app, `GET /`,
  `GET /v1/version`, `Settings.api_version` and tracing `service.version`
  default to it instead of carrying their own `1.0.0` literals. The
  `API_VERSION` environment variable still overrides it where it did before.
- `identifiers.yaml` gains `code.version`; `CITATION.cff` now reports the
  software version (previously `0.0.0`, taken from the unset
  `release_state.model_version`) and `.zenodo.json` carries a `version` field.
- Deployment docs and `docker-compose.prod.yml` reference the 1.1.0 image tag.
- Dependencies: `sqlalchemy` capped below 2.1 (2.1 makes `postgresql://` use
  psycopg 3, but the service ships psycopg2); unused `python-jose` removed
  (JWTs are handled by PyJWT), which also drops the transitive `ecdsa`.
- Type-check fixes for mypy 2.x and current OpenTelemetry stubs.

## [1.0.0]

Initial release.
