"""Add the reader's indexes to an existing archive database, in place.

    python -m wpal.migrate [path]   (default: $WPAL_DB or data/archive.db)

Lets a deployed database pick up new indexes without a rebuild and re-upload.
Only adds indexes and statistics; message rows are never touched.
"""

import sqlite3
import sys
import time

from .build import INDEXES
from .db import archive_path


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else archive_path()
    t = time.time()
    con = sqlite3.connect(path)
    con.executescript("DROP INDEX IF EXISTS messages_cover_ym; DROP INDEX IF EXISTS messages_cover_name;")
    con.executescript(INDEXES)
    con.execute("ANALYZE")
    con.commit()
    con.close()
    print(f"indexes ready on {path} in {time.time() - t:.1f}s")


if __name__ == "__main__":
    main()
