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


if __name__ == "__main__":
    (ROOT / "trial-event-1.0.0.json").write_text(
        json.dumps(trial_event_schema(), indent=2, sort_keys=True) + "\n"
    )
