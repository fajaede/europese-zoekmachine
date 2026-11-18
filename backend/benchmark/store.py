"""SQLite storage for the benchmark (same table layout as a later Postgres version).
Every measurement carries a timestamp: scores are appended, never overwritten."""

from __future__ import annotations

import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from backend.benchmark import rules

DB_PATH = os.getenv("BENCHMARK_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "benchmark.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY,
    root_domain TEXT NOT NULL UNIQUE,
    hostname TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'onbekend',
    country TEXT NOT NULL,
    language TEXT,
    source TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_crawled_at TEXT,
    next_crawl_at TEXT NOT NULL,
    crawl_priority INTEGER NOT NULL DEFAULT 0,
    index_status TEXT NOT NULL DEFAULT 'new',     -- new | ok | blocked | redirect | failed
    fail_count INTEGER NOT NULL DEFAULT 0,         -- consecutive failures, for backoff
    homepage_hash TEXT                             -- sha256 of the last homepage HTML
);
CREATE INDEX IF NOT EXISTS domains_due ON domains(next_crawl_at, crawl_priority);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    http_status INTEGER,
    title TEXT, meta_description TEXT, h1 TEXT,
    word_count INTEGER, language TEXT, canonical_url TEXT, robots_directive TEXT,
    schema_types TEXT, seo_score INTEGER, geo_score INTEGER,
    content_hash TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(domain_id, url)
);

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id INTEGER PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',         -- queued | running | done | failed
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    scheduled_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT,
    pages_crawled INTEGER, pages_unchanged INTEGER
);
CREATE INDEX IF NOT EXISTS crawl_jobs_status ON crawl_jobs(status, priority);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    psa_score INTEGER, seo_score INTEGER, geo_score INTEGER, ai_visibility_score INTEGER,
    security_score INTEGER, overall_score INTEGER, pages_crawled INTEGER NOT NULL,
    calculated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scores_domain ON scores(domain_id, calculated_at);

CREATE TABLE IF NOT EXISTS site_audits (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    max_pages INTEGER NOT NULL,
    owner TEXT NOT NULL,                           -- 'psa' or 'dev:<api key id>'
    status TEXT NOT NULL DEFAULT 'queued',         -- queued | running | done | failed
    requested_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    result_json TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS site_audits_status ON site_audits(status, requested_at);

CREATE TABLE IF NOT EXISTS benchmarks (
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    cohort TEXT NOT NULL,
    metric TEXT NOT NULL,
    percentile INTEGER NOT NULL,
    rank_in_cohort INTEGER NOT NULL,
    comparison_group_size INTEGER NOT NULL,
    calculated_at TEXT NOT NULL,
    PRIMARY KEY (domain_id, cohort, metric)
);
"""

METRICS = ["psa_score", "seo_score", "geo_score", "ai_visibility_score", "security_score", "overall_score"]
FRESH_DAYS = 90  # scores older than this do not count in a benchmark


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first release: added in place to existing databases (no data loss).
MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "domains": [("fail_count", "INTEGER NOT NULL DEFAULT 0"), ("homepage_hash", "TEXT")],
    "pages": [("content_hash", "TEXT")],
    "crawl_jobs": [("pages_crawled", "INTEGER"), ("pages_unchanged", "INTEGER")],
}


def init(path: str | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def add_domains(conn: sqlite3.Connection, items: list[dict[str, Any]], source: str) -> int:
    """Insert new domains (existing ones are left alone) and queue a crawl job for each."""
    added = 0
    stamp = now_iso()
    for item in items:
        domain = rules.root_domain(str(item.get("domain", "")))
        if not domain or "." not in domain:
            continue
        category = item.get("category") if item.get("category") in rules.CATEGORIES else "onbekend"
        cursor = conn.execute(
            "INSERT OR IGNORE INTO domains (root_domain, hostname, category, country, source, first_seen_at,"
            " next_crawl_at, crawl_priority) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (domain, str(item.get("hostname") or domain), category, item.get("country") or rules.country_of(domain),
             source, stamp, stamp, int(item.get("priority") or 0)),
        )
        if cursor.rowcount:
            added += 1
            conn.execute("INSERT INTO crawl_jobs (domain_id, priority, scheduled_at) VALUES (?, ?, ?)",
                         (cursor.lastrowid, int(item.get("priority") or 0), stamp))
    return added


def schedule_due(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Queue a recrawl for domains whose snapshot is due and that have no open job."""
    stamp = now_iso(now)
    rows = conn.execute(
        "SELECT id, crawl_priority FROM domains d WHERE next_crawl_at <= ? AND NOT EXISTS"
        " (SELECT 1 FROM crawl_jobs j WHERE j.domain_id = d.id AND j.status IN ('queued', 'running'))",
        (stamp,),
    ).fetchall()
    conn.executemany("INSERT INTO crawl_jobs (domain_id, priority, scheduled_at) VALUES (?, ?, ?)",
                     [(row["id"], row["crawl_priority"], stamp) for row in rows])
    return len(rows)


def claim_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Atomically take the highest-priority queued job (single process, so a plain UPDATE is enough)."""
    row = conn.execute(
        "SELECT j.id AS job_id, d.* FROM crawl_jobs j JOIN domains d ON d.id = j.domain_id"
        " WHERE j.status = 'queued' ORDER BY j.priority DESC, j.id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE crawl_jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
                 (now_iso(), row["job_id"]))
    conn.commit()
    return row


def reset_stale_jobs(conn: sqlite3.Connection) -> None:
    """After a restart, jobs left 'running' go back to the queue."""
    conn.execute("UPDATE crawl_jobs SET status='queued' WHERE status='running'")


def save_page(conn: sqlite3.Connection, domain_id: int, page: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO pages (domain_id, url, http_status, title, meta_description, h1, word_count, language,"
        " canonical_url, robots_directive, schema_types, seo_score, geo_score, content_hash, fetched_at)"
        " VALUES (:domain_id, :url, :http_status, :title, :meta_description, :h1, :word_count, :language,"
        " :canonical_url, :robots_directive, :schema_types, :seo_score, :geo_score, :content_hash, :fetched_at)"
        " ON CONFLICT(domain_id, url) DO UPDATE SET http_status=excluded.http_status, title=excluded.title,"
        " meta_description=excluded.meta_description, h1=excluded.h1, word_count=excluded.word_count,"
        " language=excluded.language, canonical_url=excluded.canonical_url,"
        " robots_directive=excluded.robots_directive, schema_types=excluded.schema_types,"
        " seo_score=excluded.seo_score, geo_score=excluded.geo_score, content_hash=excluded.content_hash,"
        " fetched_at=excluded.fetched_at",
        {"domain_id": domain_id, "content_hash": None, **page},
    )


def touch_page(conn: sqlite3.Connection, domain_id: int, url: str, fetched_at: str) -> None:
    """Unchanged page (same content hash): only record that we saw it again."""
    conn.execute("UPDATE pages SET fetched_at=? WHERE domain_id=? AND url=?", (fetched_at, domain_id, url))


def known_pages(conn: sqlite3.Connection, domain_id: int) -> dict[str, dict[str, Any]]:
    """url -> {content_hash, http_status, word_count} from the previous crawl."""
    return {row["url"]: {"content_hash": row["content_hash"], "http_status": row["http_status"],
                         "word_count": row["word_count"]}
            for row in conn.execute("SELECT url, content_hash, http_status, word_count FROM pages WHERE domain_id=?",
                                    (domain_id,))}


def overall(scores: dict[str, int | None]) -> int | None:
    """Same weighting idea as the psa dashboard: trust 40%, visibility (SEO/GEO/AI) 40%, security 20%."""
    visibility = [s for s in (scores.get("seo_score"), scores.get("geo_score"), scores.get("ai_visibility_score")) if s is not None]
    parts = [(scores.get("psa_score"), 40), (sum(visibility) / len(visibility) if visibility else None, 40),
             (scores.get("security_score"), 20)]
    present = [(value, weight) for value, weight in parts if value is not None]
    total = sum(weight for _, weight in present)
    return round(sum(value * weight for value, weight in present) / total) if total else None


def finish_job(conn: sqlite3.Connection, job_id: int, domain_id: int, *, category: str, language: str | None,
               scores: dict[str, int | None] | None, pages_crawled: int, error: str | None = None,
               pages_unchanged: int = 0, homepage_hash: str | None = None,
               now: datetime | None = None) -> None:
    """Store the outcome. Success: scores + the category's recrawl interval. Failure: growing
    backoff (rules.backoff_days) based on the number of consecutive failures."""
    moment = now or datetime.now(timezone.utc)
    stamp = now_iso(moment)
    if scores is not None:
        values = {**scores, "overall_score": overall(scores)}
        conn.execute(
            "INSERT INTO scores (domain_id, psa_score, seo_score, geo_score, ai_visibility_score, security_score,"
            " overall_score, pages_crawled, calculated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (domain_id, values.get("psa_score"), values.get("seo_score"), values.get("geo_score"),
             values.get("ai_visibility_score"), values.get("security_score"), values["overall_score"],
             pages_crawled, stamp),
        )
    previous_failures = conn.execute("SELECT fail_count FROM domains WHERE id=?", (domain_id,)).fetchone()[0] or 0
    if error is None:
        days, fail_count = rules.recrawl_days(category), 0
    else:
        days, fail_count = rules.backoff_days(error, previous_failures), previous_failures + 1
    status = "ok" if error is None else next(
        (prefix for prefix in ("blocked", "redirect") if error.startswith(prefix)), "failed")
    conn.execute(
        "UPDATE domains SET category=?, language=COALESCE(?, language), last_crawled_at=?, next_crawl_at=?,"
        " index_status=?, fail_count=?, homepage_hash=COALESCE(?, homepage_hash) WHERE id=?",
        (category, language, stamp, now_iso(moment + timedelta(days=days)), status, fail_count, homepage_hash,
         domain_id),
    )
    conn.execute("UPDATE crawl_jobs SET status=?, finished_at=?, error=?, pages_crawled=?, pages_unchanged=?"
                 " WHERE id=?",
                 ("done" if error is None else "failed", stamp, error, pages_crawled, pages_unchanged, job_id))


LATEST_SCORES = """
SELECT d.id AS domain_id, d.root_domain, d.country, d.category, s.*
FROM domains d
JOIN scores s ON s.id = (SELECT id FROM scores WHERE domain_id = d.id ORDER BY calculated_at DESC, id DESC LIMIT 1)
WHERE s.calculated_at >= ?
"""


def latest_scores(conn: sqlite3.Connection, now: datetime | None = None) -> list[sqlite3.Row]:
    since = now_iso((now or datetime.now(timezone.utc)) - timedelta(days=FRESH_DAYS))
    return conn.execute(LATEST_SCORES, (since,)).fetchall()


def cohort_groups(rows: list[sqlite3.Row]) -> dict[tuple[str, str], list[float]]:
    """(cohort, metric) -> ascending scores, built in one pass."""
    groups: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        for cohort in rules.cohorts(row["country"], row["category"]):
            for metric in METRICS:
                if row[metric] is not None:
                    groups.setdefault((cohort, metric), []).append(row[metric])
    for values in groups.values():
        values.sort()
    return groups


def recalculate_benchmarks(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Store percentile and rank per domain, cohort and metric (fresh scores only)."""
    rows = latest_scores(conn, now)
    groups = cohort_groups(rows)
    spread = {key: rules.has_spread(values) for key, values in groups.items()}
    stamp = now_iso(now)
    records = []
    for row in rows:
        for cohort in rules.cohorts(row["country"], row["category"]):
            for metric in METRICS:
                values = groups.get((cohort, metric))
                if row[metric] is None or not values or not spread[(cohort, metric)]:
                    continue
                pct = rules.percentile(row[metric], values)
                if pct is not None:
                    records.append((row["domain_id"], cohort, metric, pct, rules.rank(row[metric], values),
                                    len(values), stamp))
    conn.execute("DELETE FROM benchmarks")
    conn.executemany("INSERT INTO benchmarks VALUES (?, ?, ?, ?, ?, ?, ?)", records)
    return len(records)


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]
    by_category = {row[0]: row[1] for row in conn.execute("SELECT category, COUNT(*) FROM domains GROUP BY category")}
    by_status = {row[0]: row[1] for row in conn.execute("SELECT index_status, COUNT(*) FROM domains GROUP BY index_status")}
    return {
        "domains": count("SELECT COUNT(*) FROM domains"),
        "pages": count("SELECT COUNT(*) FROM pages"),
        "jobs_queued": count("SELECT COUNT(*) FROM crawl_jobs WHERE status='queued'"),
        "jobs_failed": count("SELECT COUNT(*) FROM crawl_jobs WHERE status='failed'"),
        "scored_domains": count("SELECT COUNT(DISTINCT domain_id) FROM scores"),
        "by_category": by_category,
        "by_status": by_status,
    }


def queue_overview(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, Any]:
    """Queue, next jobs, recent failure reasons and domains in backoff."""
    moment = now or datetime.now(timezone.utc)
    stamp = now_iso(moment)
    day_ago = now_iso(moment - timedelta(days=1))
    next_jobs = conn.execute(
        "SELECT d.root_domain, d.category, j.priority, j.attempts FROM crawl_jobs j JOIN domains d ON d.id=j.domain_id"
        " WHERE j.status='queued' ORDER BY j.priority DESC, j.id LIMIT 20").fetchall()
    backoff = {}
    for row in conn.execute(
            "SELECT next_crawl_at FROM domains WHERE index_status IN ('blocked','failed','redirect') AND next_crawl_at > ?",
            (stamp,)):
        days = math.ceil((datetime.fromisoformat(row[0]) - moment).total_seconds() / 86400)
        bucket = "≤1 dag" if days <= 1 else "≤7 dagen" if days <= 7 else "≤30 dagen" if days <= 30 else ">30 dagen"
        backoff[bucket] = backoff.get(bucket, 0) + 1
    return {
        "jobs_by_status": {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM crawl_jobs GROUP BY status")},
        "next_jobs": [dict(row) for row in next_jobs],
        "errors_last_24h": {row[0]: row[1] for row in conn.execute(
            "SELECT error, COUNT(*) FROM crawl_jobs WHERE status='failed' AND finished_at >= ?"
            " GROUP BY error ORDER BY 2 DESC LIMIT 10", (day_ago,))},
        "finished_last_hour": conn.execute("SELECT COUNT(*) FROM crawl_jobs WHERE finished_at >= ?",
                                           (now_iso(moment - timedelta(hours=1)),)).fetchone()[0],
        "domains_in_backoff": backoff,
    }


def index_stats(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, Any]:
    """Coverage and freshness of the benchmark index, plus cohort sizes."""
    moment = now or datetime.now(timezone.utc)
    total = conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0]

    def measured_since(days: int) -> int:
        return conn.execute("SELECT COUNT(*) FROM domains WHERE index_status='ok' AND last_crawled_at >= ?",
                            (now_iso(moment - timedelta(days=days)),)).fetchone()[0]

    unchanged = conn.execute(
        "SELECT SUM(pages_unchanged), SUM(pages_crawled) FROM crawl_jobs WHERE status='done' AND pages_crawled > 0"
        " AND finished_at >= ?", (now_iso(moment - timedelta(days=7)),)).fetchone()
    rows = latest_scores(conn, moment)
    cohort_sizes: dict[str, int] = {}
    for row in rows:
        for cohort in rules.cohorts(row["country"], row["category"]):
            cohort_sizes[cohort] = cohort_sizes.get(cohort, 0) + 1
    return {
        "domains": total,
        "by_status": {row[0]: row[1] for row in conn.execute("SELECT index_status, COUNT(*) FROM domains GROUP BY 1")},
        "by_category": {row[0]: row[1] for row in conn.execute(
            "SELECT category, COUNT(*) FROM domains WHERE index_status='ok' GROUP BY 1")},
        "fresh": {f"{d}d": measured_since(d) for d in (7, 30, 90)},
        "pages": conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
        "unchanged_page_share_7d": round(unchanged[0] * 100 / unchanged[1]) if unchanged[1] else None,
        "cohorts": dict(sorted(cohort_sizes.items(), key=lambda item: -item[1])),
        "cohorts_below_minimum": sorted(c for c, n in cohort_sizes.items() if n < rules.MIN_COHORT_SIZE),
    }
