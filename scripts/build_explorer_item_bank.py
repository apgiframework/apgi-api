"""Regenerate app/data/instruments/apgi-explorer-35.json from the Explorer source.

Usage: python scripts/build_explorer_item_bank.py path/to/QuizModel+Questions.swift

Only item text, response-option text and option value are carried over. The
Swift `note` strings (construct-named hints) are deliberately dropped: they
must never reach a participant. Edit the JSON directly once it is the source
of truth; this script exists to make the first import reproducible.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "app/data/instruments/apgi-explorer-35.json"
_ITEM = re.compile(
    r"QuizQuestion\(\s*id:\s*(\d+),\s*text:\s*(\"(?:[^\"\\]|\\.)*\"),\s*options:\s*\[(.*?)\],\s*parameter:",
    re.S,
)
_OPTION = re.compile(r"QuizOption\(\s*text:\s*(\"(?:[^\"\\]|\\.)*\")\s*,\s*note:\s*\"(?:[^\"\\]|\\.)*\"\s*,\s*value:\s*(\d)\s*\)", re.S)
_SUBSCALES = [
    "epsilon", "precision", "threshold", "somatic", "interactions", "temporal", "environment",
]


def main(source: Path) -> None:
    items = []
    for match in _ITEM.finditer(source.read_text(encoding="utf-8")):
        item_id = int(match.group(1))
        options = [
            {"value": int(v), "text": json.loads(t)} for t, v in _OPTION.findall(match.group(3))
        ]
        items.append(
            {
                "item_id": item_id,
                "subscale": _SUBSCALES[(item_id - 1) // 5],
                "scoring": "nominal" if item_id in (7, 9) else "ordinal",
                "text": json.loads(match.group(2)),
                "options": options,
            }
        )
    items.sort(key=lambda i: i["item_id"])
    assert [i["item_id"] for i in items] == list(range(1, 36)), "expected items 1..35"
    assert all([o["value"] for o in i["options"]] == [1, 2, 3, 4] for i in items)
    bank = {"instrument_id": "apgi-explorer-35", "bank_version": "1.0.0", "locale": "en", "items": items}
    OUT.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(items)} items)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
