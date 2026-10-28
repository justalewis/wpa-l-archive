"""Registers the research tools on the app (SPEC.md items 1–11).

Each tool lives in its own module under wpal/tools/ with a `register(app)`
function. A tool whose tables are missing from archive.db (an older build)
must still let the rest of the site work.
"""

from __future__ import annotations

import importlib

TOOLS: list[str] = ["people", "data", "trends", "confidence"]


def register(app) -> None:
    for name in TOOLS:
        importlib.import_module(f"wpal.tools.{name}").register(app)
