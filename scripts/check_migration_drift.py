"""Fail when the migrated schema is structurally different from the ORM.

Run against a database already at `alembic upgrade head`. Compares tables,
columns and nullability in both directions; ignores comments, server-default
rendering and index naming, which `alembic check` flags but which carry no
behaviour. Exit 1 lists every difference.
"""

import os
import sys

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from app.database.models import Base

STRUCTURAL = {
    "add_table",
    "remove_table",
    "add_column",
    "remove_column",
    "modify_nullable",
}


def _flatten(diffs):  # type: ignore[no-untyped-def]
    for item in diffs:
        if isinstance(item, list):
            yield from _flatten(item)
        else:
            yield item


def main() -> int:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        diffs = [d for d in _flatten(compare_metadata(ctx, Base.metadata)) if d[0] in STRUCTURAL]
    for d in diffs:
        print("DRIFT:", d[0], getattr(d[1], "name", d[1:]))
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
