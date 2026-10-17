# Changelog

All notable changes to the APGI System API are recorded here. Versions follow
the apgiframework/apgi-framework release they ship alongside.

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

## [1.0.0]

Initial release.
