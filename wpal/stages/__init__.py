"""Build stages that run after messages are parsed and threaded.

Each stage module has `run(db, ctx)`, where `db` is the sqlite3 connection to
the database being built and `ctx` a Context. Stages run in STAGES order and
may depend on earlier ones. They read curated judgments from decisions/
(PLAN.md) and write only derived tables, so a rebuild reproduces them.

    python -m wpal.build                     # parse everything, then all stages
    python -m wpal.build --stages identity   # re-run named stages (and those
                                             # after them) on the existing DB
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path

STAGES = ["fresh", "identity", "trends", "confidence", "genre", "events",
          "network", "flashpoints", "institutions", "citations", "topics"]


# Stages written so far; the rest of STAGES are skipped until their modules exist.
AVAILABLE = [s for s in STAGES if (Path(__file__).parent / f"{s}.py").exists()]


@dataclass
class Context:
    decisions: Path = Path("decisions")
    previous_db: Path | None = None   # the archive.db being replaced, for id redirects
    reference: Path = Path("data/reference")  # downloaded public datasets (IPEDS, Pinakes dump)
    log: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        print(f"  [stage] {msg}", flush=True)
        self.log.append(msg)


def add_column(db, table: str, column: str, decl: str) -> None:
    """Add a column if it isn't there (stages can re-run on a finished build)."""
    if column not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def run(db, ctx: Context, only: list[str] | None = None, skip: list[str] | None = None) -> None:
    names = STAGES
    if only:
        first = min(STAGES.index(n) for n in only)
        names = STAGES[first:]
    names = [n for n in names if n not in (skip or []) and n in AVAILABLE]
    for name in names:
        mod = importlib.import_module(f"wpal.stages.{name}")
        ctx.note(f"{name} ...")
        mod.run(db, ctx)
        db.commit()
