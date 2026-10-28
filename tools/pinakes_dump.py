"""Dump the Pinakes work records the citation stage matches against (item 9).

    python tools/pinakes_dump.py [--base https://pinakes.wacclearinghouse.org]

Pages through Pinakes' public /api/articles (200 per request, about one
request a second) and writes data/reference/pinakes/works.jsonl with only the
fields the matcher needs. Re-run it to refresh; the citation stage records the
dump date. Pinakes ids are shared, so a match links straight to
{base}/article/{id}.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

KEEP = ["id", "doi", "title", "authors", "pub_date", "journal", "internal_cited_by_count",
        "crossref_cited_by_count", "openalex_cited_by_count"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://pinakes.wacclearinghouse.org")
    ap.add_argument("--out", default="data/reference/pinakes/works.jsonl")
    ap.add_argument("--pause", type=float, default=0.9)
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    offset, total, n = 0, None, 0
    with open(tmp, "w", encoding="utf-8") as fh:
        while total is None or offset < total:
            url = f"{a.base}/api/articles?limit=200&offset={offset}"
            for attempt in range(6):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "wpa-l-archive citation matcher"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = json.load(r)
                    break
                except Exception as e:
                    print("retry", offset, e, flush=True)
                    time.sleep(10 * (attempt + 1))
            else:
                raise SystemExit(f"gave up at offset {offset}")
            total = data["total"]
            for art in data["articles"]:
                fh.write(json.dumps({k: art.get(k) for k in KEEP}, ensure_ascii=False) + "\n")
                n += 1
            offset += 200
            if offset % 4000 == 0:
                print(f"{n:,} / {total:,}", flush=True)
            time.sleep(a.pause)
    tmp.replace(out)
    (out.parent / "dumped_at.txt").write_text(time.strftime("%Y-%m-%d") + f" from {a.base}\n", encoding="utf-8")
    print(f"{n:,} works -> {out}")


if __name__ == "__main__":
    main()
