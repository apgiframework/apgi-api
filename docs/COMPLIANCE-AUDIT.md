# apgi-api compliance audit against the project register

Audited: branch `claude/brave-bardeen-dji3hw` (includes the screening / tier / form-order work).
Scope: the 453 register rows supplied, grouped into 24 clusters below.

**Method and limits.** Evidence comes from reading code, migrations, Terraform and CI
config, plus grep for the named artifacts. I did not run every behaviour. The existing
unit and integration suite passes, but passing tests are not acceptance evidence for
rows whose criteria demand something the repo does not contain. Rows that need cloud
state (GCP projects, WIF, Cloud Tasks queues, dashboards live) cannot be verified from
a checkout; I mark them *unverifiable here* where the repo has no code or Terraform for
them at all, and *Not compliant* only where the repo contradicts the task.

Legend: **C** compliant · **P** partial · **N** not compliant (absent or contradicted) ·
**E** external or blocked (decision, other repo, cloud state, real participants).

## Headline

Of the 20 requirement clusters below, none is fully compliant. Eleven are partial
(§1, 5, 7, 8, 9, 10, 11, 14, 18, 19, 20), six are not compliant (§2, 3, 4, 6, 12, 16),
and three are mixed partial/not-compliant or depend on external work (§13, 15, 17).

Nothing in the register is *fully* met by apgi-api today. The strongest areas are
participant-session ingestion, per-purpose consent plumbing, cascading erasure, row-level
security and the K7 gate concept. The largest gaps are **the hash-chained audit log,
the ExposureGate / gate table, the contract-first layer (OpenAPI v1, JSON schemas, version
lattice, instrument registry), the module/directory layout, and idempotent ingestion
(`event_id` / `seq_no` / `payload_sha256`)**.

---

## 1. Audit log: S0-PR0-03, -04, -06; S2-SCH-09; S2-AUD-01.1–.6, S2-AUD-02 — **P**

Found: `audit_logs` table (not `audit_events`) with an HMAC-SHA256 `signature`
(`app/services/audit_signing.py`, `AuditLog.signature`), written by `log_audit_event`.
Verification function exists (`verify_audit_entry`).

Gaps:
- Table is named `audit_logs`, and has no `prev_record_sha256` or `signing_key_version` column. **No chain.**
- Signing key is the `AUDIT_SIGNING_KEY` environment variable, not a Secret Manager key
  with a published version. Records cannot "verify against the published key version".
- `signature` is nullable ("legacy rows"); no `REVOKE UPDATE, DELETE`; no migration enforcing append-only.
- No chain-verification tool; no acceptance test that tampering breaks a chain.
- Per-score record with input hash, stamps, output hash (S2-AUD-01.1) does not exist; scoring writes no audit record.
- Report reads are not logged (no report module).

## 2. Exposure gate, K7, latent routes: S1-PR2b-04…08; S2-GATE-01…06 — **N** (concept present, requirements unmet)

Found: `app/services/k7_gate.py` per-parameter `is_cleared()`; `/state-vector` returns 403 unless `settings.k7b_cleared`; other state routes check `is_cleared` inline.

Gaps:
- No `ExposureGate` class, no `GateViolation`, no `app/modules/scoring/exposure.py`, no allow-list, no `gate_table_version`, no `_gate_table_version` on outputs. S1-PR2b-04/05/08 and S2-GATE-01.x: **not met**; no CI test for "field absent from allow-list".
- Clearance is an **environment variable** (`k7_cleared_parameters`, `k7b_cleared`) — exactly what S2-GATE-02.1 says to replace. No evidence records (`validation_id`, `dataset_sha256`, reviewer).
- Latent routes (`/state-vector`, `/interoception`, `/prediction-errors`, `/somatic-markers`, `/ignition-history`, under `/v1/sessions/{id}/…`) return 403 or withheld fields, not an `identifiability_gate` state object (S1-PR2b-06, S1-EP-08.1–.5).
- Heart rate / cortisol / temperature still sit under K7 (`INTEROCEPTIVE_BODY_STATE`), not consent scope + role (S2-GATE-02.5).
- Scoring: `score_session()` uses the *active* ModelVersion at scoring time — the exact defect S2-GATE-03.2 names; there is no binding at authorisation (S2-GATE-03.1). Scores are server-side only (S2-GATE-04: **C** — clients post trials; scores are computed in `scoring.py`).
- Completion response (`SessionCompletionResponse`) has no model-free indices, stamps or `apgi_state.exposed` (S2-GATE-05). No rename of `reversal_learning_rate` / `ignition_proxy` (these names are absent, so the renames are vacuous, but the target fields are also absent).

## 3. Dev routes in production: S1-PR2b-07, S1-EP-12.5 — **N**

`POST /v1/users/create-default` exists, guarded only by `USER_CREATE` permission, not removed from production. Profiling routes live in `app/routes/metrics.py` (`/profiling/memory/start`, …) and `ProfilingMiddleware` is opt-in by env. Neither is stripped from the production OpenAPI. (They are mounted under `/v1/metrics/…`, not `/v1/profiling/*`; the intent is the same.)

## 4. Shared contracts and version lattice: S1-CON-01…10, S1-PR3-01…12 — **N**

- No `contracts/`, `platform-spec/`, or JSON Schema files; no TypeScript client or fixture parity tests.
- Pydantic schemas exist for requests/responses but not the named versioned `TrialEvent`, `SessionManifest`, `AcquisitionManifest`, `SignalChunkManifest`, `DatasetManifest`, `AnalysisRunManifest`, `ValidationResult`, `CompletionResponse`, `NormResponse` v1.0.0 artifacts.
- Lattice stamps (S1-CON-03.1–.17): only `release_state` (via `identifiers.yaml`) and, newly, `tier_config_version` (on sessions). Absent: `platform_release`, `api_contract`, `schema_version`, protocol id/version/sha256, `battery_version` (only on `Battery`), instrument id/version/sha256, `feature_version`, `model_id`+`model_version` (`ModelVersion` row only), `reference_dataset_version`, `gate_table_version`, per-purpose `consent_version` (single `version` per consent, no purpose lattice), `scoring_spec_version`, `preprocessing_version`, `quality_rules_version`, `client_build`, `analysis_manifest_sha256`. No write-time rejection of results missing a stamp (S1-CON-03).
- S1-CON-05 (three instrument IDs kept distinct) and S1-CON-06: no instrument registry exists; nothing to enforce.
- S1-CON-10 (no composite cross-layer score): **C by absence** — no composite score exists in any schema.
- S1-CON-09 (data classification): `app/services/data_lifecycle.py` has four levels (public/internal/confidential/restricted) mapped to *generic* tables, not the research domain, and not named per the task (Public/Internal/Restricted/Highly restricted). **P**.
- S1-CON-04 is a plan erratum, not repo code (**E**).

## 5. Endpoint reconciliation: S1-EP-01…13 — **P**

Present and matching the plan: `POST /v1/participant-sessions`, `…/{id}/trials`, `…/{id}/complete`
(S1-EP-06.1/.2 and S1-EP-02 partly), consents (`POST …/consents`, `/consents/revoke`, `GET /consents/status` — but two consent types, not four),
`GET /v1/norms`, `GET /v1/instrument/psychometrics`, `GET /v1/longitudinal/metrics`, `GET /v1/meta`,
`DELETE /v1/participants/{id}` (cascading erasure, S1-EP-13.1: **C** route-wise), payments routes.

Present under a different path (rename pending): `/v1/dataset-card` → `/v1/reference-datasets/{version}/card`; `/v1/n-of-1/experiments` → `/v1/personal-experiments`; `/v1/acquisition/uploads[...]` vs planned `/v1/acquisitions` + `chunks:initiate|commit` + `status`.

Absent: `GET /v1/participants/{id}/profile`, `/{id}/norms`, `/{id}/export` (portability; the existing `/v1/sessions/{id}/export` is for simulation sessions), `POST …/events` (non-trial markers), `POST …/responses`, `GET /v1/instruments/{id}/versions/{version}`, `GET /v1/studies/{id}/manifest`, `GET /v1/protocols[...]`, `POST /v1/datasets`, `/v1/analysis-runs`, `GET /v1/validation/{run_id}`, `GET /v1/reports/{id}`, `GET /v1/admin/data-quality`, `POST /v1/client-diagnostics`.
(`GET /v1/studies/{id}/exclusion-report` was added by this work and is not a planned route.)

D1 conflict: the legacy simulation API at `/v1/sessions` still coexists with `/v1/participant-sessions`; "only the canonical family exists" (S1-EP-02) is not met until D1 is decided and the old family removed or redirected.
S1-EP-01, S1-EP-04: there is **no frozen OpenAPI v1 file** and no generated Python/TypeScript/Swift clients (**N**). Blocked on S1-DEC-01 for the session-path rows.

## 6. Module layout and boundaries: S2-CORE-07…10 — **N**

None of `app/modules/*`, `app/api/v1/*`, `app/domain/`, `app/workers/`, `app/observability/`, top-level `migrations/`, `openapi/` exist. The repo uses `app/routes`, `app/services`, `app/tasks`, `app/middleware`, `app/alembic` (and `app/tests/`). Present as-is: `app/services/`, `app/database/`, `tests/`, `deployment/`. No `import-linter` config or CI step (S2-CORE-10). Note S2-CORE-09.6 says `migrations/`; the repo uses `app/alembic/` with `alembic.ini`. Decide whether to move or amend the task.

## 7. Deploy units and infrastructure: S2-CORE-01…06, 11…15; S2-DEL-03/04; S2-OBS-01…03; S3-SEC-01.4–.6/.11/.16; S4-DATA-03 — **P / unverifiable here**

Terraform exists for Cloud Run, Cloud SQL, Memorystore, Secret Manager, Artifact Registry, WIF, monitoring, storage.
- S2-CORE-01: `deployment/Dockerfile` exists (**P**; build/test pass in Artifact Registry unverified).
- S2-CORE-02: Cloud Run ingress is `INGRESS_TRAFFIC_ALL` and the comment says it is a public REST API; the task asks for "private … reachable only by authenticated callers" (**N** as configured; auth is enforced in-app, not at ingress).
- S2-CORE-04/05/06 (Cloud Run Jobs, `apgi-export` in a separate project, pre-deploy migration job): no Terraform for them (**N**).
- S2-CORE-12: Cloud Tasks queues — no Terraform; Celery + Redis is load-bearing (`celery_app.py`, `tasks/*`) and no removal plan exists (S2-CORE-12.5: **N**). Blocked on D6.
- S2-CORE-13: `deployment/docker-compose.yml` uses Postgres 14, Redis and Celery; task wants Postgres 17, fake GCS, inline tasks (**N**).
- S2-CORE-15 / S3-SEC-01.4: storage bucket has versioning, but CMEK is commented as "add later" (**P**).
- S2-CORE-11 (LLM service has no write grant): no LLM service exists; **C by absence**, no IAM evidence.
- S2-DEL-03.1 PITR, S2-OBS-01…02: Terraform has uptime and error-rate alert policies only; no dashboards, no SLO objects, no OpenTelemetry→Cloud Trace wiring beyond `app/tracing.py` (**P**).
- WIF dependency S0-INF-06.4: `wif.tf` exists (**P**; live state unverifiable).

## 8. Database schema: S2-SCH-01…25 — **P**

Present (research domain, `add_research_pilot_domain`, `add_nof1_experiment_engine`, `add_acquisition_uploads`, and this branch's migration): `studies`, `participants`, `consents`, `study_enrollments`, `batteries`, `model_versions`, `participant_sessions`, `trial_events`, `acquisition_uploads`, `audit_logs`, n-of-1 tables.

Mapping gaps (the plan DDL names differ from the live tables, so "matches plan DDL" is **N** until the plan is reconciled):
- `sessions` (live: `participant_sessions`; has `session_index`, new `tier_config_version`/`device_tier`, but no `parallel_form`, `battery_version`, protocol stamps).
- `consent_records` append-only per purpose and action (live `consents` has `revoked_at` updates; revoke mutates rows — not append-only).
- `study_memberships` + holdout trigger (S2-SCH-03/21, S2-PR3b-02/05): **absent**; no `holdout` or `cohort_type` anywhere.
- `item_responses`, `signal_chunks` (live: `acquisition_uploads` only), `protocol_releases`, `instrument_versions`, `participant_identities` (restricted schema), `session_assignments`, `events`, `devices`, `quality_reports`, `scores` (scores are a JSON column), `analysis_runs`, `validation_results`, `data_deletions`: **absent**.
- `studies.release_state` (S2-SCH-23.7): absent on the table (release state is computed from `identifiers.yaml`).
- Corrections: `seq_no`, `payload_sha256` on `trial_events`, `UNIQUE(session_id, seq_no)`: absent. Live uniqueness is `(session, task_type, trial_index)`. (The "drop redundant `(session_id,event_id)`" item is moot: no `event_id` column.)
- `idx_trial_events_session_order`: absent (only `idx_trial_events_session`).
- Schema separation identity/research/ops (S2-SCH-24), monthly partitioning (S2-SCH-25.5), retention in DB: absent.
- RLS (S2-SCH-25.1): **P** — policies on `participants`, `consents`, `study_enrollments`, `participant_sessions`, `trial_events` keyed to owner-or-staff; not keyed to study scope. Identity-field encryption (S2-SCH-25.4): **C** for participant email/demographics (`EncryptedString`, Fernet).
- `REVOKE UPDATE, DELETE` on `consent_records`/`audit_events`: absent (S2-SCH-23.8).

## 9. Consent and authorisation: S2-CON-01…05, S2-PR3b-01 — **P**

- Two consent types (`research_participation`, `data_sharing`), not four purposes with scopes (S2-CON-01, S2-CON-01.1–.2, S2-PR3b-01: **N**). Blocked on S1-PRV-06 (consent texts) for S2-CON-01.3.
- `ConsentGrantRequest.consent_text` is still client-supplied (S2-CON-01.4: **N**); server hashes it (`hash_consent_text`) but does not serve canonical text by type/version/locale.
- Revoke closing every open grant, and no reactivation of older grants (S2-CON-01.5): `revoke` route exists; no test proving "no older grant reactivates" (**P**).
- Prospective re-consent on version change (S2-CON-01.6): `ConsentStatusResponse` uses "effective (unrevoked, current-version)" — **P**.
- Product works without research consent (S2-CON-01.7): research routes require consent, but no product/research split exists to verify (**N**).
- Cross-study isolation and RLS tests (S2-CON-02.1/.2): no such tests in `tests/` (**N**). Withdrawal-in-flight tests (.4–.6): none (**N**).
- Auth (S2-CON-03): one JWT scheme + API keys; no separate participant / researcher / machine paths with distinct tokens (**P**).
- Roles (S2-CON-04): `Role` enum in `authorization.py` has admin/researcher/viewer-style roles, not the eight named roles with resource scopes (study_id, site_id, data category, consent scope) (**P/N**).

## 10. Ingestion, journal, recovery: S2-ING-01, S2-ING-03, S2-PR4-01…08 — **P**

Found: batch trial ingestion with consent gating, ownership check, schema `extra="forbid"`, idempotent `/complete`; acquisition upload initiate / status / commit with signed-URL storage (`acquisition_ingest.py`, `acquisition_storage.py`, `acquisition_tasks.py`).

Gaps: no `event_id`, `seq_no`, or `payload_sha256` anywhere in the live trial schema (S2-ING-01.1–.9, S2-SCH-23.10: **N**); a duplicate trial hits the unique constraint and surfaces as a DB error rather than "acknowledge duplicate with original receipt" or HTTP 409; `/complete` never returns `awaiting_inputs`; no sequence-gap reporting; HTTP request time is not distinguished from event time and there is no `timing_provenance` (S2-ING-01.9). No transactional outbox, dispatcher, nightly reconciliation job, dead-letter handling beyond Celery defaults (S2-ING-03.x: **N**). `/complete` idempotency (S2-ING-03.3): **C**. No fault-injection tests (S2-PR4-07/08). Client-side journal/resume (S2-PR4-01…) lives in `apgi-system` (**E**).

## 11. Deletion, retention and backups: S2-DEL-01, S2-PR3b-03/04, S2-RET-01…07 — **P**

`app/tasks/deletion_tasks.py` cascades Cloud SQL rows, raw GCS objects and de-identified BigQuery rows, and tolerates BigQuery outage; `DELETE /v1/participants/{id}` triggers it.
Gaps: no `data_deletions` ledger row or append-only external ledger bucket (S2-DEL-01.1/.9); no signed zero-residual verification record (.7); no backup-expiry check or post-restore ledger replay (.6/.10); non-current object versions not explicitly deleted (.4 unverified); report/LLM-log stores do not exist yet (.8). No seeded-participant staging test (S2-PR3b-04, S2-DEL-02: **E**, needs staging).
Retention: `data_lifecycle.py` has generic categories (30 d / 365 d / 7 y / permanent) for the old simulation tables. None of the register's periods (5 y raw trials, 10 y derived, account + 90 d, 10 y consent, 3 y reports, local-copy rule) are implemented, and there is no enforcement job (S2-RET-01…07: **N**).

## 12. Instrument registry and item responses: S3-INS-01.x, S1-CON-05 — **N**

No `instrument_versions`, `item_responses`, `/responses` endpoint, instrument-hash check, or server-side subscale scoring.

## 13. Norms and reference data: S5-NRM-01…03, S1-EP-09, S4-REF*, S5-REF3-01 — **P / N**

`GET /v1/norms` exists and (new in this branch) restricts to norm-eligible sessions, one per participant per battery version. It returns empirical percentiles with a pilot `note`; it does **not** suppress cells under 200 records (S5-NRM-01.2: **N**), stratify by tier/age/language/cohort (.1), require `cohort_type` (.3), enforce K4b invariance (.4), publish between-cohort differences (.5) or attach an ICC/SEM/MDC block (.6). S5-NRM-03 is described as "In Progress": the route exists but returns percentiles, not a suppression response, until a reference exists. Partitions/reference versions (S4-REF1-01, S4-REF2-01, S5-REF3-01) are **E** (need participants).

## 14. Dataset card and metadata: S5-CARD-01.x, -02, -03, -04 — **P**

`GET /v1/meta` returns the identifier block and release state (**P**; ethics number is null as expected). `GET /v1/dataset-card` exists with a release-state note; the register wants `/v1/reference-datasets/{version}/card` and the listed fields (recruitment channels, countries, etc.). Exclusion and validity-screen rates are now computable via the new exclusion-report endpoint (S5-CARD-01.4.6/.11: **P**). `X-APGI-Release-State` / `X-APGI-Platform-Release` headers: absent (S5-CARD-04: **N**). `/v1/instrument/psychometrics` exists (**P**; live figures need real data).

## 15. Datasets, jobs, analytics: S4-DATA-01, -02, -04 — **N / E**

No dataset versioning layout, manifests, parquet outputs, worker images, or data-quality dashboard. Partly blocked on cloud state and real data.

## 16. Reports and LLM constraints: S5-COM-01…07, S2-GATE-01.2–.4 — **N**

No report module, LLM integration, report log, claim templates, forbidden-vocabulary checker, or K-gate-keyed feature flags. Payments routes exist (`/v1/payments/…`) and are not tied to `validation_results` (S5-COM-01: **N**; "Stripe products cannot be enabled by configuration alone" is not satisfied). Researcher portal and sharing integration (S5-COM-06/07) are **E** / other repos.

## 17. Longitudinal and personal experiments: S7-L3-*, S7-PX-* — **P / E**

`GET /v1/longitudinal/metrics` (ICC, SEM, MDC95) and `/v1/participants/{id}/change-report` exist, and `/v1/n-of-1/*` provides an alternating-phase engine. Not found: server-computed `exceeds_mdc` as the single source (S7-L3-16.1 — it is computed in the change-report route; no test that the report module never recomputes it), session-index fixed-effect practice slope, MNAR models, wave completion reporting, the eight per-participant metrics, ABAB randomisation with randomised day assignment and the ≥12-observation reporting rule (S7-PX-01.6/.8). Scheduling, UI, and the experiment types are largely **E** (apgiframework.com).

## 18. Security controls: S3-SEC-01.x — **P**

- TLS/HSTS: `SecurityHeadersMiddleware` (HSTS present) — **P** (TLS termination is infra).
- Passwords: bcrypt with SHA-256 pre-hash (S3-SEC-01.12: **C**).
- Server-side authorisation + RLS: **P** (see §8–9). Schema validation, parameterised SQL: **C** by SQLAlchemy and `extra="forbid"`.
- Token revocation: refresh tokens and API keys exist (**P**).
- Per-study access auditing, alert on linking-table reads: no linking table exists (**N**).
- Rate limiting is in-app (`RateLimitingMiddleware`); Cloud Armor / bot protection is not in Terraform (S3-SEC-01.11: **N**). The new form-layer bot flags exist only as screening data.
- Dependency/secret scanning: CI has Bandit and a vulnerability check (**P**); container and secret scanning absent.
- Managed secrets only: Secret Manager containers exist in Terraform, but `AUDIT_SIGNING_KEY` and `PII_ENCRYPTION_KEY` are consumed from environment variables (**P**).
- Native-client entitlements, restoration testing and breach procedures: **E**.

## 19. Participant routes behind authentication: S2-CORE-03 — **P**

`AuthenticationMiddleware` requires a bearer token for every path outside `PUBLIC_PATHS`, so unauthenticated participant requests receive 401 (**P**). There is no explicit "until step 14" toggle, and no test pinning it for the research routes specifically.

## 20. CI gates: XC-CI-06 and sub-gates — **P**

`.github/workflows/ci.yml` runs lint, mypy strict, Bandit, unit, integration, property, package build, identifiers divergence and docs checks. Missing as *named required gates*: migrations (no `alembic upgrade head` / drift check against the models), contract (no OpenAPI diff or client regeneration), auth, consent, ingestion, exposure-gate allow-list, import-linter boundaries, restore checks. Branch protection is a GitHub setting and cannot be verified from the repo (XC-CI-06: **P**).

## 21. Screening, device tiers, form order (new in this branch)

Not in this register's list, but relevant to S2-SCH-05, S3-BAT-01.6.5 and S5-CARD-01.4.6/.11: implemented in `app/services/screening.py` with migration `add_session_screening_and_form_order`. `tier_config_version` is stored per session; exclusion-rate reporting is available.

## 22. Known repo-health findings from this audit

1. Docs describe `app/alembic` migrations while the register expects `migrations/`; decide one.
2. `deployment/docker-compose.yml` still ships Redis/Celery, contradicting S2-CORE-13 and D6.
3. `AuditLog.signature` nullable and mutable: an attacker with DB write access can rewrite or delete audit rows undetected.
4. `score_session()` binds the model at scoring time (S2-GATE-03.2).
5. Clearance by environment variable can be flipped by anyone able to change deployment config, with no evidence trail.
6. Duplicate-trial retries currently fail on the unique constraint instead of being acknowledged.

## 23. Rows that cannot be assessed from this repository (E)

S2-CORE-14, S2-CORE-02 live state, S2-DEL-02, S2-PR3b-04, S2-DEL-03.3, S2-DEL-04, S2-OBS-02 SLO values, S3-SEC-01 live controls, S4-REF*, S5-REF3-01, S5-CARD-01.4.x values, S5-COM-06/07, S7-L3-01…14 scheduling, S7-PX-01.1–.5, S7-REF4-01: they depend on other repositories (`apgiframework.com`, `apgi-system`, `apgi-infrastructure`, `apgi-framework`), live cloud state, decisions (S1-DEC-01, S2-DEC-01/D6, S0-DEC-02/04) or participant data.

## 24. Recommended order of work (each independently shippable)

1. Hash-chained `audit_events` (§1), because S2-AUD-02 requires it before the first score.
2. `ExposureGate` + gate table + allow-list CI test, then gate the latent routes to return state objects (§2). Removes the env-var clearance path.
3. Strip `create-default` and profiling in production; add a production-OpenAPI test (§3).
4. Idempotent ingestion: `event_id`, `seq_no`, `payload_sha256`, 409-on-conflict, `awaiting_inputs` (§10). Unlocks S2-ING-03.
5. Four-purpose consent with server-served text and append-only history (§9).
6. Freeze OpenAPI v1 and add the version-lattice stamps with write-time rejection (§4–5).
7. Retention enforcement job and the `data_deletions` ledger (§11).
8. CI: migrations drift, contract diff, import-linter, exposure-gate gates (§20).
