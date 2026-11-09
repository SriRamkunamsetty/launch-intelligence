"""Ingestion: CSV import with validation, and a YouTube Data API client with
quota accounting and retry/backoff. Both write to the same normalised tables
and log every run in ingest_runs."""
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .db import get_or_create_title, iso, now_iso, parse_ts

REQUIRED = ("post_id", "title", "platform", "published_at", "captured_at", "views")


def _log_run(conn, source, started, status, ok, rejected, quota=0):
    conn.execute(
        "INSERT INTO ingest_runs (source, started_at, status, rows_ok, rows_rejected, quota_units) "
        "VALUES (?,?,?,?,?,?)", (source, started, status, ok, rejected, quota))


def store_row(conn, title_id, platform, native_id, kind, published_at, captured_at,
              views, likes=0, comments=0, shares=0, url=None, is_sample=0):
    pid = f"{platform}:{native_id}"
    conn.execute(
        "INSERT INTO posts (pid, platform, title_id, kind, published_at, url, is_sample) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT (pid) DO NOTHING",
        (pid, platform, title_id, kind, published_at, url, is_sample))
    conn.execute(
        "INSERT INTO snapshots (pid, captured_at, views, likes, comments, shares) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT (pid, captured_at) DO UPDATE SET views=excluded.views, likes=excluded.likes, "
        "comments=excluded.comments, shares=excluded.shares",
        (pid, captured_at, views, likes, comments, shares))


def import_csv(conn, path: str) -> dict:
    """Import platform exports. Invalid rows are rejected with a reason, never silently dropped."""
    started, ok, rejected = now_iso(), 0, []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            _log_run(conn, f"csv:{os.path.basename(path)}", started, "failed", 0, 0)
            raise ValueError(f"CSV is missing required columns: {missing}")
        for n, row in enumerate(reader, start=2):
            try:
                pub, cap = parse_ts(row["published_at"]), parse_ts(row["captured_at"])
                if cap < pub:
                    raise ValueError("captured_at is before published_at")
                nums = {k: int(float(row.get(k) or 0)) for k in ("views", "likes", "comments", "shares")}
                if min(nums.values()) < 0:
                    raise ValueError("negative counter")
                tid = get_or_create_title(conn, row["title"].strip(), row.get("genre") or None)
                store_row(conn, tid, row["platform"].strip().lower(), row["post_id"].strip(),
                          row.get("kind") or None, iso(pub), iso(cap), url=row.get("url") or None, **nums)
                ok += 1
            except (ValueError, KeyError, AttributeError) as exc:
                rejected.append({"line": n, "reason": str(exc)})
    _log_run(conn, f"csv:{os.path.basename(path)}", started, "ok", ok, len(rejected))
    conn.commit()
    return {"accepted": ok, "rejected": rejected}


class QuotaExceeded(RuntimeError):
    pass


class YouTubeError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"YouTube API error {status}: {body[:200]}")
        self.status, self.body = status, body


def _default_fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


class YouTubeClient:
    """Each list call costs 1 quota unit (videos: up to 50 ids; commentThreads: up to 100 comments)."""
    BASE = "https://www.googleapis.com/youtube/v3/"

    def __init__(self, api_key=None, fetch=None, daily_quota=10_000, max_retries=4, sleep=time.sleep):
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY", "")
        self.fetch, self.sleep = fetch or _default_fetch, sleep
        self.daily_quota, self.max_retries, self.used = daily_quota, max_retries, 0

    def _call(self, endpoint, params):
        if self.used + 1 > self.daily_quota:
            raise QuotaExceeded(f"{self.used}/{self.daily_quota} units used")
        url = self.BASE + endpoint + "?" + urllib.parse.urlencode({**params, "key": self.api_key})
        for attempt in range(self.max_retries + 1):
            status, body = self.fetch(url)
            if status == 200:
                self.used += 1
                return json.loads(body)
            if status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                self.sleep(2 ** attempt)
                continue
            raise YouTubeError(status, body)

    def video_stats(self, video_ids):
        out = []
        for i in range(0, len(video_ids), 50):
            data = self._call("videos", {"part": "snippet,statistics", "id": ",".join(video_ids[i:i + 50])})
            for item in data.get("items", []):
                st = item.get("statistics", {})
                out.append({"id": item["id"], "published_at": item["snippet"]["publishedAt"],
                            "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                            "comments": int(st.get("commentCount", 0))})
        return out


def ingest_youtube(conn, client: YouTubeClient, title_name: str, video_ids, kind="trailer", genre=None):
    """Take one snapshot of the given videos and store it against a title."""
    started = now_iso()
    video_ids = list(video_ids)
    try:
        rows = client.video_stats(video_ids)
    except Exception:
        _log_run(conn, "youtube", started, "failed", 0, 0, client.used)
        conn.commit()
        raise
    tid = get_or_create_title(conn, title_name, genre)
    for r in rows:
        store_row(conn, tid, "youtube", r["id"], kind, iso(parse_ts(r["published_at"])), now_iso(),
                  r["views"], r["likes"], r["comments"], 0, url=f"https://youtu.be/{r['id']}")
    _log_run(conn, "youtube", started, "ok", len(rows), len(video_ids) - len(rows), client.used)
    conn.commit()
    return len(rows)

