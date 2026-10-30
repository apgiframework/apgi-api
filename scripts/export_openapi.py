"""Export the frozen OpenAPI v1 contract (production routing: dev routes stripped).

`PYTHONPATH=. python scripts/export_openapi.py`          rewrite openapi/openapi-v1.json
`PYTHONPATH=. python scripts/export_openapi.py --check`  exit 1 on drift (used by CI)

A deliberate API change regenerates the file in the same commit, so the diff is
the review. Clients (Python/TypeScript/Swift) are generated from this file by
scripts/generate_clients.sh and must never be hand-edited.
"""

import json
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "openapi" / "openapi-v1.json"


def build_spec() -> dict:
    from app import main as app_main

    previous = app_main.settings.environment
    app_main.settings.environment = "production"  # type: ignore[assignment]
    try:
        return json.loads(json.dumps(app_main.create_app(test_mode=True).openapi(), sort_keys=True))
    finally:
        app_main.settings.environment = previous  # type: ignore[assignment]


def main() -> int:
    rendered = json.dumps(build_spec(), indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != rendered:
            print("OpenAPI drift: run `python scripts/export_openapi.py` and commit the result")
            return 1
        return 0
    TARGET.write_text(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
