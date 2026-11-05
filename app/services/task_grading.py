"""Server-side trial grading (NEW-04).

Correctness is derived here from the raw response and a server-controlled
answer key (app/data/task_keys.json). A client-supplied ``correct`` flag is
never authoritative: it is ignored at ingestion, so accuracy cannot be
inflated by posting ``correct=true``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_KEYS_PATH = Path(__file__).resolve().parent.parent / "data" / "task_keys.json"


@lru_cache(maxsize=1)
def load_task_keys() -> dict[str, dict[str, Any]]:
    with _KEYS_PATH.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    tasks = doc.get("tasks", {})
    if not isinstance(tasks, dict):
        raise ValueError("task_keys.json: 'tasks' must be an object")
    return tasks


def grade_trial(
    task_type: str,
    response_value: Optional[dict[str, Any]],
    keys: Optional[dict[str, dict[str, Any]]] = None,
) -> Optional[bool]:
    """Return True/False when the task has a server key and the response is
    gradable; None otherwise (ungraded, never assumed correct)."""
    key = (keys if keys is not None else load_task_keys()).get(task_type)
    if key is None or not response_value:
        return None
    stimulus = response_value.get(key.get("stimulus_field", "stimulus"))
    response = response_value.get(key.get("response_field", "response"))
    if stimulus is None or response is None:
        return None
    expected = key.get("answers", {}).get(str(stimulus))
    if expected is None:
        return None
    return bool(response == expected)
