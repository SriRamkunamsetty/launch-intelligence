"""Scheduled collection. Config (JSON):
{"titles": [{"name": "X", "genre": "action", "videos": {"trailer": ["<youtube id>"], "teaser": ["<id>"]}}]}
Run `launch_intel collect` from cron/systemd/Kubernetes CronJob, or `launch_intel schedule` as a long-running worker."""
import json
import time

from .ingest import QuotaExceeded, ingest_youtube


def load_config(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for t in cfg.get("titles", []):
        if not t.get("name") or not isinstance(t.get("videos"), dict) or not t["videos"]:
            raise ValueError(f"bad title entry (needs name + videos map): {t}")
    return cfg


def collect_once(conn, client, config):
    summary = {"titles": 0, "videos": 0, "errors": []}
    for t in config["titles"]:
        try:
            for kind, ids in t["videos"].items():
                summary["videos"] += ingest_youtube(conn, client, t["name"], ids, kind=kind, genre=t.get("genre"))
            summary["titles"] += 1
        except QuotaExceeded:
            raise
        except Exception as exc:  # one bad title must not stop the run
            summary["errors"].append(f"{t['name']}: {exc}")
    return summary


def schedule(conn, client, config, every_s, max_runs=None, sleep=time.sleep, log=print, max_consecutive_failures=5):
    """Long-running worker. Stops on quota exhaustion or repeated total failure (so a supervisor can alert)."""
    runs = fails = 0
    while max_runs is None or runs < max_runs:
        try:
            s = collect_once(conn, client, config)
            log(f"run {runs + 1}: {s['videos']} videos, {len(s['errors'])} errors, quota {client.used}")
            fails = fails + 1 if s["titles"] == 0 and s["errors"] else 0
        except QuotaExceeded as exc:
            log(f"quota exhausted, stopping: {exc}")
            return runs
        runs += 1
        if fails >= max_consecutive_failures:
            log("too many consecutive failures, stopping")
            return runs
        if max_runs is None or runs < max_runs:
            sleep(every_s)
    return runs
