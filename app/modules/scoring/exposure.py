"""Exposure gate (PR 2b): latent APGI quantities leave the API only as gate state.

The allow-list is loaded once at start-up, keyed by `gate_table_version`.
`ExposureGate.check` raises (never nulls out) on any outbound field that is
not allow-listed, so a new field cannot leak by omission.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel

GATE_TABLE_PATH = Path(__file__).with_name("gate_table.yaml")


class GateViolation(RuntimeError):
    """An outbound payload carried a field absent from the allow-list."""


class GateTableError(RuntimeError):
    """The gate table is missing or invalid; the service must not start."""


class GateParameterState(BaseModel):
    parameter: str
    status: str
    exposed: bool
    reason: str


class GateStateResponse(BaseModel):
    session_id: str
    identifiability_gate: List[GateParameterState]
    gate_table_version: str


@lru_cache(maxsize=1)
def load_gate_table() -> Dict[str, Any]:
    try:
        table = yaml.safe_load(GATE_TABLE_PATH.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise GateTableError(f"Cannot load gate table {GATE_TABLE_PATH}: {exc}") from exc
    if (
        not isinstance(table, dict)
        or not table.get("gate_table_version")
        or not table.get("outbound_allow_list")
        or not isinstance(table.get("latent_parameters"), dict)
    ):
        raise GateTableError(
            "Gate table needs gate_table_version, latent_parameters and an allow-list"
        )
    return table


class ExposureGate:
    def __init__(self, table: Dict[str, Any] | None = None) -> None:
        self.table = table or load_gate_table()
        self.version: str = self.table["gate_table_version"]
        self.allowed = frozenset(self.table["outbound_allow_list"])

    def check(self, payload: Any) -> Any:
        """Raise GateViolation on any non-allow-listed key; stamp top-level dicts."""
        self._walk(payload)
        if isinstance(payload, dict):
            return {**payload, "_gate_table_version": self.version}
        return payload

    def _walk(self, node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key not in self.allowed:
                    raise GateViolation(f"Outbound field {key!r} is not in the allow-list")
                self._walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                self._walk(item)

    def gate_state(self, session_id: str, *parameters: str) -> GateStateResponse:
        """Gate state for `parameters` (all latent parameters if none given). Never values."""
        latent = self.table["latent_parameters"]
        states = []
        for name in parameters or tuple(latent):
            entry = latent[name]
            exposed = entry.get("status") == "cleared" and bool(entry.get("evidence"))
            states.append(
                GateParameterState(
                    parameter=name,
                    status="cleared" if exposed else "withheld",
                    exposed=exposed,
                    reason="" if exposed else "No identifiability evidence record in gate table",
                )
            )
        response = GateStateResponse(
            session_id=session_id, identifiability_gate=states, gate_table_version=self.version
        )
        self.check(response.model_dump())
        return response
