"""Item 11 on the page: the events list, a seasonality view, and the events
overlay that /timeline, /trends, /topics and /flashpoints draw when the
reader turns it on (?events=1).
"""

from __future__ import annotations

import collections

from flask import abort, render_template, request

from ..app import NavItem
from ..stages.events import CATEGORIES


def register(app) -> None:
    app.nav.append(NavItem("events", "Events", "events_page", "ic-event", 37))

    def events():
        def load():
            try:
                return [dict(r) for r in app.con().execute("SELECT * FROM events ORDER BY date")]
            except Exception:
                return []
        return app.cached("events", load)

    app.events = events

    def overlay(x_of):
        """Event marks for a chart, given its function from (year, month) to x."""
        if request.args.get("events") != "1":
            return []
        out = []
        for e in events():
            x = x_of(e["year"], e["month"])
            if x is not None:
                when = e["date"] + (f" to {e['end_date']}" if e["end_date"] else "")
                out.append({"x": round(x, 1), "label": e["label"], "category": e["category"], "when": when})
        return out

    app.event_overlay = overlay

    @app.context_processor
    def _ev_ctx():
        return {"events_on": request.args.get("events") == "1", "EVENT_CATEGORIES": CATEGORIES}

    @app.route("/events")
    def events_page():
        evs = events()
        if not evs:
            abort(404)
        months = app.cached("month_counts_ev", lambda: {(r[0], r[1]): r[2] for r in app.con().execute(
            "SELECT year, month, COUNT(*) FROM main.messages GROUP BY year, month")})
        # Seasonality: each calendar month's share of its year's messages, averaged over
        # the complete years (1993 starts in January; 2021 stops in May).
        full = range(1993, 2021)
        per_month = collections.defaultdict(list)
        for y in full:
            tot = sum(months.get((y, m), 0) for m in range(1, 13))
            for m in range(1, 13):
                per_month[m].append(months.get((y, m), 0) / tot if tot else 0)
        season = {m: sum(v) / len(v) for m, v in per_month.items()}
        # Relative to CCCC: volume in the months around each convention, as a share
        # of that year's monthly average.
        rel = collections.defaultdict(list)
        for e in evs:
            if e["category"] != "cccc" or not 1993 <= e["year"] <= 2020:
                continue
            avg = sum(months.get((e["year"], m), 0) for m in range(1, 13)) / 12 or 1
            for k in range(-4, 5):
                idx = e["year"] * 12 + e["month"] - 1 + k
                y, m = divmod(idx, 12)
                if (y, m + 1) in months:
                    rel[k].append(months[(y, m + 1)] / avg)
        around = {k: sum(v) / len(v) for k, v in sorted(rel.items()) if v}
        return render_template("events.html", evs=evs, season=season, around=around, active_nav="events")

    # Overlays on the core timeline (bars at W=3 per month from X0=52).
    def timeline_hook():
        return {"event_marks": overlay(lambda y, m: 52 + ((y - 1993) * 12 + m - 1) * 3 + 1
                                       if (1993, 1) <= (y, m) <= (2021, 5) else None)}

    app.hooks["timeline"].append(timeline_hook)
