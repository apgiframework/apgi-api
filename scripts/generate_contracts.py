"""Regenerate contracts/events/trial-event-1.0.0.json from the pydantic model.

Run with `PYTHONPATH=. python scripts/generate_contracts.py`; CI fails on drift via
tests/unit/test_contracts.py. Schemas are frozen at v1.0.0: a change requires
a new version, never an edit in place.
"""

import json
from pathlib import Path

from app.schemas.research import TrialEventIngestRequest

ROOT = Path(__file__).resolve().parent.parent / "contracts" / "events"


def trial_event_schema() -> dict:
    schema = TrialEventIngestRequest.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://api.apgiframework.com/contracts/events/trial-event-1.0.0.json"
    schema["title"] = "TrialEvent"
    return schema


def _schema(model: type, title: str, filename: str) -> tuple[str, dict]:
    schema = model.model_json_schema()  # type: ignore[attr-defined]
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://api.apgiframework.com/contracts/events/{filename}"
    schema["title"] = title
    return filename, schema


def all_schemas() -> dict[str, dict]:
    """filename -> schema for every contract generated from a live pydantic model."""
    from app.routes.instruments import ItemResponseBatch
    from app.schemas.reporting import NormsResponse
    from app.schemas.research import SessionCompletionResponse

    out = {"trial-event-1.0.0.json": trial_event_schema()}
    for model, title, filename in (
        (ItemResponseBatch, "ItemResponseBatch", "item-response-batch-1.0.0.json"),
        (SessionCompletionResponse, "CompletionResponse", "completion-response-1.0.0.json"),
        (NormsResponse, "NormResponse", "norm-response-1.0.0.json"),
    ):
        name, schema = _schema(model, title, filename)
        out[name] = schema
    return out


if __name__ == "__main__":
    for filename, schema in all_schemas().items():
        (ROOT / filename).write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
