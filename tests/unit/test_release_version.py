"""The release version is declared once in app.__version__; every copy must agree."""

import tomllib
from pathlib import Path

import yaml

from app import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_version_is_1_1_0() -> None:
    assert __version__ == "1.1.0"


def test_pyproject_matches_package_version() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__


def test_identifiers_yaml_matches_package_version() -> None:
    ids = yaml.safe_load((ROOT / "identifiers.yaml").read_text(encoding="utf-8"))
    assert ids["code"]["version"] == __version__
