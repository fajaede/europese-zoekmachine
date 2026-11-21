"""Domain authority from the Common Crawl web graph (open data, free).

Common Crawl publishes a domain-level link graph every few months, with a ranks file of all
~135 million domains sorted by harmonic centrality (how close a domain is to the rest of the web
via links). We stream that file (~2.4 GB gzip, never stored on disk), keep only .nl and .be
domains, and store per domain:

  authority = 0-100 on a logarithmic scale of the domain's rank among the same extension's LINKED
              domains (harmonic centrality > 0): 100 = the strongest .nl (or .be) site, 0 = the
              weakest; every step up is harder, like Moz Domain Authority / Ahrefs Domain Rating.
              A percentile was tried first, but most linked domains have one or two weak links, so
              any real business site landed above 85. Domains without links score 0 and get no rank.
              Equal centrality values share a rank and a score.

Stored in a separate SQLite file (authority.db) so the import never locks the crawler's database;
a new import is written to a fresh table and swapped in at the end, so readers never see half data.

Command line (on the server):
  docker exec fajaede-backend python -m backend.benchmark.authority import          # latest release
  docker exec fajaede-backend python -m backend.benchmark.authority status
  docker exec fajaede-backend python -m backend.benchmark.authority rescore         # new scoring, no download
  docker exec fajaede-backend python -m backend.benchmark.authority seed 40000      # add top domains to the benchmark
"""

from __future__ import annotations

import gzip
import io
import json
import math
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

DB_PATH = os.getenv("AUTHORITY_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "authority.db"))
GRAPHINFO_URL = "https://index.commoncrawl.org/graphinfo.json"
RANKS_URL = ("https://data.commoncrawl.org/projects/hyperlinkgraph/{release}/domain/"
             "{release}-domain-ranks.txt.gz")
TLDS = ("nl", "be")
HEADER = "#harmonicc_pos\t#harmonicc_val\t#pr_pos\t#pr_val\t#host_rev\t#n_hosts"
USER_AGENT = "Mozilla/5.0 (compatible; FajaedeBot/1.0; +https://psa.fajaede.nl/bot)"
BATCH = 50_000
LOCK_HOURS = 6        # an import lock older than this is stale (container restarted mid-import)

SCHEMA = """
CREATE TABLE IF NOT EXISTS authority (
    domain TEXT PRIMARY KEY,
    tld TEXT NOT NULL,
    local_rank INTEGER NOT NULL,      -- 1 = strongest domain of this TLD
    hc_pos INTEGER NOT NULL,          -- global harmonic-centrality position (1 = strongest on the web)
    hc_val REAL NOT NULL,
    pr_pos INTEGER NOT NULL,
    pr_val REAL NOT NULL,
    n_hosts INTEGER NOT NULL,
    score INTEGER                     -- 0-100, filled in when the import finishes
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(path: str | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def _set_meta(conn: sqlite3.Connection, **values: Any) -> None:
    conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     [(k, json.dumps(v)) for k, v in values.items()])


def meta(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        return {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key, value FROM meta")}
    except sqlite3.OperationalError:
        return {}


def parse_line(line: str, tlds: tuple[str, ...] = TLDS) -> dict[str, Any] | None:
    """One line of the ranks file -> a record for a .nl/.be domain, else None.
    Columns: harmonicc_pos, harmonicc_val, pr_pos, pr_val, host_rev ('nl.voorbeeld'), n_hosts."""
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 6 or parts[0].startswith("#"):
        return None
    rev = parts[4]
    tld, _, rest = rev.partition(".")
    if tld not in tlds or not rest:
        return None
    try:
        return {"domain": ".".join(reversed(rev.split("."))), "tld": tld, "hc_pos": int(parts[0]),
                "hc_val": float(parts[1]), "pr_pos": int(parts[2]), "pr_val": float(parts[3]),
                "n_hosts": int(parts[5])}
    except ValueError:
        return None


def latest_release() -> str:
    request = urllib.request.Request(GRAPHINFO_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)[0]["id"]


class DownloadError(Exception):
    """A download problem that retrying cannot fix (missing file, file replaced mid-download)."""


class ResumableDownload(io.RawIOBase):
    """Read-only stream of a large HTTP file that survives dropped connections and Common Crawl's
    "SlowDown" throttling: on an error it waits (growing pause) and continues with a Range request
    from the byte where it stopped. Gives up after `max_retries` failures in a row."""

    def __init__(self, url: str, max_retries: int = 12, opener: Any = None, sleep: Any = time.sleep):
        self.url, self.max_retries = url, max_retries
        self.opener, self.sleep = opener or urllib.request.urlopen, sleep
        self.position, self.length, self.response, self.failures = 0, None, None, 0
        self.etag: str | None = None

    def readable(self) -> bool:
        return True

    def _connect(self) -> None:
        headers = {"User-Agent": USER_AGENT}
        if self.position:
            headers["Range"] = f"bytes={self.position}-"
            if self.etag:
                headers["If-Range"] = self.etag      # the server sends the whole file (200) if it changed
        try:
            response = self.opener(urllib.request.Request(self.url, headers=headers), timeout=120)
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500 and error.code not in (408, 429):
                raise DownloadError(f"HTTP {error.code} for {self.url}") from error     # retrying will not help
            raise
        if "xml" in (response.headers.get("Content-Type") or "").lower():   # S3 error body (e.g. SlowDown)
            response.close()
            raise OSError("error document instead of data")
        status = getattr(response, "status", 200)
        if self.position and status != 206:
            response.close()
            raise DownloadError(f"cannot resume (HTTP {status}): file changed on the server or no Range support")
        if self.etag is None:
            self.etag = response.headers.get("ETag")
        if self.length is None:
            length = response.headers.get("Content-Length")
            self.length = int(length) if length and length.isdigit() else None
        self.response = response

    def readinto(self, buffer: Any) -> int:
        while True:
            try:
                if self.response is None:
                    self._connect()
                n = self.response.readinto(buffer)
                if n == 0 and self.length is not None and self.position < self.length:
                    raise OSError(f"connection closed at byte {self.position} of {self.length}")
                self.position += n
                self.failures = 0
                return n
            except (OSError, urllib.error.URLError) as error:      # HTTPError (503 SlowDown) is a URLError
                if self.response is not None:
                    self.response.close()
                    self.response = None
                self.failures += 1
                if self.failures > self.max_retries:
                    raise OSError(f"download failed after {self.max_retries} retries: {error}") from error
                self.sleep(min(300, 5 * 2 ** (self.failures - 1)))

    def close(self) -> None:
        if self.response is not None:
            self.response.close()
            self.response = None
        super().close()


def open_gzip_lines(url: str) -> Iterator[bytes]:
    """Lines of a remote .gz file, streamed with resume on errors."""
    raw = ResumableDownload(url)
    with io.BufferedReader(raw, buffer_size=1 << 20) as buffered, gzip.GzipFile(fileobj=buffered) as unzipped:
        yield from unzipped


def _stream_lines(release: str) -> Iterator[str]:
    for line in open_gzip_lines(RANKS_URL.format(release=release)):
        yield line.decode("utf-8", errors="replace")


def import_ranks(lines: Iterable[str], release: str, path: str | None = None,
                 progress_every: int = 5_000_000) -> dict[str, Any]:
    """Load .nl/.be records from ranks-file lines into a new table and swap it in."""
    init(path)
    started = time.monotonic()
    counts = {tld: 0 for tld in TLDS}
    tie = {tld: (None, 0) for tld in TLDS}         # (last hc value, local rank of its first domain)
    batch: list[tuple[Any, ...]] = []
    read = 0
    with connect(path) as conn:
        conn.execute("DROP TABLE IF EXISTS authority_new")
        conn.execute(SCHEMA.split(";")[0].replace("authority (", "authority_new (", 1))
        _set_meta(conn, import_running=True, import_release=release, import_started_at=_now(), import_lines=0)
        conn.commit()
        header_checked = False
        for line in lines:
            read += 1
            if not header_checked:
                header_checked = True
                if line.startswith("#") and line.strip() != HEADER:
                    raise ValueError(f"Unexpected ranks file header: {line.strip()[:120]}")
            # cheap pre-filter: the reversed domain column starts with "nl." or "be."
            if "\tnl." not in line and "\tbe." not in line:
                if read % progress_every == 0:
                    _set_meta(conn, import_lines=read)
                    conn.commit()
                continue
            record = parse_line(line)
            if record is None:
                continue
            tld = record["tld"]
            counts[tld] += 1
            last_value, first_rank = tie[tld]
            if record["hc_val"] != last_value:          # file is sorted by harmonic centrality, high to low
                tie[tld] = (record["hc_val"], counts[tld])
            batch.append((record["domain"], tld, tie[tld][1], record["hc_pos"], record["hc_val"],
                          record["pr_pos"], record["pr_val"], record["n_hosts"]))
            if len(batch) >= BATCH:
                conn.executemany("INSERT OR IGNORE INTO authority_new (domain, tld, local_rank, hc_pos, hc_val,"
                                 " pr_pos, pr_val, n_hosts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch)
                batch.clear()
                _set_meta(conn, import_lines=read)
                conn.commit()
        if batch:
            conn.executemany("INSERT OR IGNORE INTO authority_new (domain, tld, local_rank, hc_pos, hc_val,"
                             " pr_pos, pr_val, n_hosts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch)
        if not header_checked or not any(counts.values()):
            raise ValueError("No .nl/.be domains found: download incomplete or format changed")
        linked = score_table(conn, "authority_new")
        conn.execute("DROP TABLE IF EXISTS authority")
        conn.execute("ALTER TABLE authority_new RENAME TO authority")
        conn.execute("CREATE INDEX IF NOT EXISTS authority_rank ON authority(tld, local_rank)")
        summary = {"release": release, "imported_at": _now(), "lines_read": read, "domains": counts,
                   "linked": linked, "seconds": round(time.monotonic() - started)}
        _set_meta(conn, import_running=False, last_import=summary, import_lines=read)
    return summary


class ImportBusy(RuntimeError):
    """Another Common Crawl import (authority or backlinks) is still running."""


def claim_import(job: str, path: str | None = None, now: datetime | None = None) -> None:
    """One Common Crawl import at a time (they share authority.db and the network). A lock older than
    LOCK_HOURS is ignored, so a crash or container restart never blocks imports for good."""
    init(path)
    now = now or datetime.now(timezone.utc)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")                      # check and set atomically
        lock = meta(conn).get("import_lock") or {}
        try:
            started = datetime.fromisoformat(lock["started"])
        except (KeyError, TypeError, ValueError):
            started = None
        if started and now - started < timedelta(hours=LOCK_HOURS):
            conn.rollback()
            raise ImportBusy(f"{lock.get('job')} import running since {lock['started']}; try again later")
        _set_meta(conn, import_lock={"job": job, "started": now.replace(microsecond=0).isoformat()})


def release_import(path: str | None = None) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM meta WHERE key = 'import_lock'")


def log_score(rank: int, linked: int) -> int:
    """100 for rank 1, 0 for the last of `linked` domains, logarithmic in between."""
    if linked <= 1:
        return 100
    return max(0, min(100, round(100 * (1 - math.log(rank) / math.log(linked)))))


def score_table(conn: sqlite3.Connection, table: str = "authority") -> dict[str, int]:
    """(Re)compute `score` for every row of `table`; returns the number of linked domains per TLD.
    Rows are ranked per TLD by harmonic centrality (local_rank, ties share the rank of their first
    domain), so linked domains (hc_val > 0) occupy ranks 1..linked."""
    assert table in ("authority", "authority_new")
    linked = {tld: n for tld, n in conn.execute(
        f"SELECT tld, COUNT(*) FROM {table} WHERE hc_val > 0 GROUP BY tld")}
    conn.execute(f"UPDATE {table} SET score = 0 WHERE hc_val <= 0")
    conn.execute("DROP TABLE IF EXISTS temp.rank_score")
    conn.execute("CREATE TEMP TABLE rank_score (tld TEXT, local_rank INTEGER, score INTEGER,"
                 " PRIMARY KEY (tld, local_rank))")
    for tld, total in linked.items():
        ranks = [r for (r,) in conn.execute(
            f"SELECT DISTINCT local_rank FROM {table} WHERE tld = ? AND hc_val > 0", (tld,))]
        conn.executemany("INSERT INTO rank_score VALUES (?, ?, ?)",
                         ((tld, r, log_score(r, total)) for r in ranks))
    conn.execute(f"UPDATE {table} SET score = (SELECT rs.score FROM rank_score rs WHERE rs.tld = {table}.tld"
                 f" AND rs.local_rank = {table}.local_rank) WHERE hc_val > 0")
    conn.execute("DROP TABLE temp.rank_score")
    return linked


def rescore(path: str | None = None) -> dict[str, int]:
    """Apply the current scoring to the imported data without downloading again."""
    claim_import("rescore", path)
    try:
        with connect(path) as conn:
            linked = score_table(conn, "authority")
            info = meta(conn).get("last_import") or {}
            info["linked"] = linked
            _set_meta(conn, last_import=info)
        return linked
    finally:
        release_import(path)


def run_import(release: str | None = None, path: str | None = None) -> dict[str, Any]:
    claim_import("authority", path)
    try:
        release = release or latest_release()
        return import_ranks(_stream_lines(release), release, path)
    except BaseException as error:
        with connect(path) as conn:
            _set_meta(conn, import_running=False, import_error=f"{type(error).__name__}: {error}"[:500])
        raise
    finally:
        release_import(path)


def lookup(domains: Iterable[str], path: str | None = None) -> dict[str, dict[str, Any]]:
    """Authority per domain ({} when not imported yet or unknown). 'www.' is ignored."""
    wanted = {d.lower().removeprefix("www.") for d in domains if d}
    if not wanted or not os.path.exists(path or DB_PATH):
        return {}
    try:
        with connect(path) as conn:
            marks = ",".join("?" * len(wanted))
            rows = conn.execute(f"SELECT domain, score, local_rank, tld, hc_val FROM authority WHERE domain IN ({marks})",
                                sorted(wanted)).fetchall()
            info = meta(conn).get("last_import") or {}
            totals = info.get("linked") or info.get("domains", {})
    except sqlite3.OperationalError:
        return {}
    # rank "X van Y" only for linked domains: Y counts linked domains, unlinked ones have no place
    return {row["domain"]: {"score": row["score"],
                            "rank": row["local_rank"] if row["hc_val"] > 0 else None,
                            "of": totals.get(row["tld"]) if row["hc_val"] > 0 else None}
            for row in rows}


def known(domains: Iterable[str], path: str | None = None) -> set[str]:
    """The domains that are in the Common Crawl graph (any rank, linked or not). Their links are already in
    Common Crawl's referring-domain counts, so the own link index (store.links) does not store them twice.
    Empty when nothing is imported yet."""
    wanted = sorted({d.lower().removeprefix("www.") for d in domains if d})
    if not wanted or not os.path.exists(path or DB_PATH):
        return set()
    found: set[str] = set()
    try:
        with connect(path) as conn:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start:start + 500]
                marks = ",".join("?" * len(chunk))
                found.update(row[0] for row in conn.execute(f"SELECT domain FROM authority WHERE domain IN ({marks})", chunk))
    except sqlite3.OperationalError:
        return set()
    return found


def top_domains(limit: int, exclude: set[str], path: str | None = None) -> list[dict[str, Any]]:
    """Strongest .nl/.be domains not yet in `exclude`, both TLDs interleaved by global position."""
    from backend.benchmark.worker import is_infrastructure   # lazy: keeps this module light for tests
    out: list[dict[str, Any]] = []
    with connect(path) as conn:
        for row in conn.execute("SELECT domain FROM authority ORDER BY hc_pos"):
            domain = row["domain"]
            if domain in exclude or is_infrastructure(domain):
                continue
            out.append({"domain": domain, "priority": max(1, 40 - len(out) // 1000)})
            if len(out) >= limit:
                break
    return out


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "import":
        print(json.dumps(run_import(argv[2] if len(argv) > 2 else None), indent=2))
    elif command == "status":
        init()
        with connect() as conn:
            info = meta(conn)
            info["rows"] = conn.execute("SELECT COUNT(*) FROM authority").fetchone()[0]
        print(json.dumps(info, indent=2))
    elif command == "rescore":
        print(json.dumps({"linked": rescore()}))
    elif command == "seed":
        from backend.benchmark import store
        limit = int(argv[2]) if len(argv) > 2 else 10_000
        store.init()
        with store.connect() as conn:
            known = {row[0] for row in conn.execute("SELECT root_domain FROM domains")}
            items = top_domains(limit, known)
            added = store.add_domains(conn, items, "commoncrawl")
        print(json.dumps({"offered": len(items), "added": added}))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
