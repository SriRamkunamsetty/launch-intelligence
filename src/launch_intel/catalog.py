"""Importer for logged EVENTS: the 'why' layer (TV spot, cast post, algorithm change)."""
import csv

from .db import iso, parse_ts


def _read(path, required):
    fh = open(path, newline="", encoding="utf-8-sig")
    rd = csv.DictReader(fh)
    miss = [c for c in required if c not in (rd.fieldnames or [])]
    if miss:
        fh.close()
        raise ValueError(f"CSV is missing required columns: {miss}")
    return fh, rd


def import_events_csv(conn, path):
    """Columns: title, occurred_at, kind, label. The title must already exist (typos must not create phantom titles)."""
    ok, rejected = 0, []
    fh, rd = _read(path, ("title", "occurred_at", "kind", "label"))
    with fh:
        for n, r in enumerate(rd, start=2):
            try:
                t = conn.execute("SELECT id FROM titles WHERE name=?", (r["title"].strip(),)).fetchone()
                if not t:
                    raise ValueError(f"unknown title '{r['title'].strip()}'")
                if not r["label"].strip() or not r["kind"].strip():
                    raise ValueError("kind and label are required")
                when = iso(parse_ts(r["occurred_at"]))
                if not conn.execute("SELECT 1 FROM events WHERE title_id=? AND occurred_at=? AND label=?", (t["id"], when, r["label"].strip())).fetchone():
                    conn.execute("INSERT INTO events (title_id, occurred_at, kind, label) VALUES (?,?,?,?)",
                                 (t["id"], when, r["kind"].strip(), r["label"].strip()))
                ok += 1
            except (ValueError, KeyError) as exc:
                rejected.append({"line": n, "reason": str(exc)})
    conn.commit()
    return {"accepted": ok, "rejected": rejected}
