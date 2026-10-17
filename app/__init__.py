"""
Standalone APGI API Application

This package contains the standalone REST API for the APGI system.
"""

__version__ = "1.1.0"

from typing import Any, Dict, Optional, Union


class MockSubSystem:
    """Mock subsystem with save/load state methods."""

    def __init__(self, name: str):
        self.name = name
        self._state: Dict[str, Any] = {}

    def save_state(self) -> Dict[str, Any]:
        """Save subsystem state."""
        return self._state.copy()

    def load_state(self, state: Dict[str, Any]) -> None:
        """Load subsystem state."""
        self._state = state


class APGISystem:
    """Mock APGI System implementation."""

    def __init__(self, config_path: Optional[Union[str, Dict[str, Any]]] = None):
        # Handle config_path being a string (file path) or dict
        if isinstance(config_path, str):
            # Load config from file path
            import os

            if os.path.exists(config_path):
                import yaml

                try:
                    with open(config_path, "r") as f:
                        loaded = yaml.safe_load(f)
                        self.config: Dict[str, Any] = loaded if loaded else {}
                except Exception:
                    self.config = {}
            else:
                self.config = {}
        elif isinstance(config_path, dict):
            self.config = config_path
        else:
            self.config = {}
        self.time = 0.0
        self.history: Dict[str, Any] = {}
        self.allostasis = MockSubSystem("allostasis")
        self.body = MockSubSystem("body")
        self.precision = MockSubSystem("precision")
        self.workspace = MockSubSystem("workspace")
        self.self_model = MockSubSystem("self_model")
        self.ignition = MockSubSystem("ignition")
        self._initialize_subsystems()

    def _initialize_subsystems(self) -> None:
        """Initialize all subsystems."""
        pass

    def get_state(self) -> Dict[str, Any]:
        """Get system state."""
        return {
            "time": self.time,
            "history": self.history,
            "config": self.config,
        }

    def step(self, extero_input: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute one simulation step."""
        self.time += 1.0
        return {
            "time": self.time,
            "history": self.history,
            "config": self.config,
            "ignition": {"total_signal": 0.0, "threshold": 2.0},
        }

    def reset(self) -> None:
        """Reset the system."""
        self.time = 0.0
        self.history = {}
        self.allostasis._state = {}
        self.body._state = {}
        self.precision._state = {}
        self.workspace._state = {}
        self.self_model._state = {}
        self.ignition._state = {}
