"""Launch Intelligence command line. Run `launch-intel <command> -h` for options."""
import argparse
import json
import os
import sys

from . import __version__
from .db import connect
from .ingest import QuotaExceeded, YouTubeClient, import_csv, ingest_youtube
from .metrics import build_facts
from .narrator import fmt, ignore_list, render, template_narrative, verify


def open_db(a):
    if getattr(a, "demo", False):
        from .sample_data import seed_all
        conn = connect(check_same_thread=False)
        seed_all(conn)
        return conn
    return connect(a.db, check_same_thread=False)


def _title_id(conn, name):
    row = conn.execute("SELECT id FROM titles WHERE name=?", (name,)).fetchone()
    if not row:
        raise SystemExit(f"unknown title: {name}")
    return row["id"]


def _report(conn, title_id, name):
    ids = [r["id"] for r in conn.execute("SELECT id FROM titles")]
    facts = build_facts(conn, title_id, [i for i in ids if i != title_id])
    text = render(template_narrative(name, facts, conn, title_id), facts)
    rep = verify(text, facts, ignore=ignore_list(conn, title_id, name))
    print(text, "\n")
    for f in facts:
        print(f"  {f.id:<16} {fmt(f):>10}   {f.query}")
    print(f"\n  claims checked: {rep.n_numbers} numbers, ok={rep.ok}  {rep.reasons()}")


def build_parser():
    p = argparse.ArgumentParser(prog="launch-intel", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=os.environ.get("DATABASE_URL", "launch_intel.db"), help="SQLite path or postgresql:// DSN")
    common.add_argument("--demo", action="store_true", help="use in-memory SYNTHETIC demo data")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", parents=[common], help="print a grounded launch report").add_argument("--title", default="Iron Meridian")
    w = sub.add_parser("serve", parents=[common], help="read-only dashboard"); w.add_argument("--host", default="127.0.0.1"); w.add_argument("--port", type=int, default=8000)
    d = sub.add_parser("deck", parents=[common], help="executive .pptx for a title"); d.add_argument("--title", required=True); d.add_argument("--out", default="launch_report.pptx")
    sub.add_parser("ingest-csv", parents=[common], help="post_id,title,platform,published_at,captured_at,views[,likes,comments,shares,kind,genre]").add_argument("path")
    sub.add_parser("import-events", parents=[common], help="title,occurred_at,kind,label").add_argument("path")
    y = sub.add_parser("ingest-youtube", parents=[common]); y.add_argument("--title", required=True); y.add_argument("--ids", required=True)
    c = sub.add_parser("collect", parents=[common], help="one collection pass from a titles.json config"); c.add_argument("--config", required=True)
    s = sub.add_parser("schedule", parents=[common], help="long-running collector"); s.add_argument("--config", required=True)
    s.add_argument("--every-hours", type=float, default=6); s.add_argument("--max-runs", type=int)
    sub.add_parser("check-youtube", help="live smoke test of YOUTUBE_API_KEY")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        _dispatch(a)
    except (RuntimeError, ValueError, OSError) as exc:
        raise SystemExit(f"error: {exc}")


def _dispatch(a):
    if a.cmd == "demo":
        a.demo = True
        conn = open_db(a); _report(conn, _title_id(conn, a.title), a.title)
    elif a.cmd == "serve":
        from .web import serve
        serve(open_db(a), a.host, a.port)
    elif a.cmd == "deck":
        from .deck import build_deck
        conn = open_db(a); print("wrote", build_deck(conn, _title_id(conn, a.title), a.out))
    elif a.cmd == "ingest-csv":
        res = import_csv(open_db(a), a.path)
        print(f"accepted={res['accepted']} rejected={len(res['rejected'])}")
        for r in res["rejected"][:20]:
            print(f"  line {r['line']}: {r['reason']}")
    elif a.cmd == "import-events":
        from .catalog import import_events_csv
        r = import_events_csv(open_db(a), a.path); print(f"accepted={r['accepted']} rejected={len(r['rejected'])}")
        for x in r["rejected"][:20]:
            print(f"  line {x['line']}: {x['reason']}")
    elif a.cmd == "ingest-youtube":
        client = YouTubeClient()
        try:
            print(f"stored {ingest_youtube(open_db(a), client, a.title, a.ids.split(','))} videos, quota used {client.used}")
        except QuotaExceeded as exc:
            raise SystemExit(f"quota exhausted: {exc}")
    elif a.cmd in ("collect", "schedule"):
        from .pipeline import collect_once, load_config, schedule
        conn, cfg, client = open_db(a), load_config(a.config), YouTubeClient()
        if a.cmd == "collect":
            s = collect_once(conn, client, cfg)
            print(json.dumps(s, indent=2)); sys.exit(1 if s["errors"] and not s["titles"] else 0)
        schedule(conn, client, cfg, a.every_hours * 3600, max_runs=a.max_runs)
    elif a.cmd == "check-youtube":
        client = YouTubeClient()
        if not client.api_key:
            raise SystemExit("YOUTUBE_API_KEY is not set")
        stats = client.video_stats(["dQw4w9WgXcQ"])
        print(f"OK: key works, got {len(stats)} video, quota used {client.used}" if stats else "Key accepted but no video returned")


if __name__ == "__main__":
    main()
