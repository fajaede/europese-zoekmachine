"""Domain authority from the Common Crawl web graph (open data, free).

Common Crawl publishes a domain-level link graph every few months, with a ranks file of all
~135 million domains sorted by harmonic centrality (how close a domain is to the rest of the web
via links). We stream that file (~2.4 GB gzip, never stored on disk), keep only .nl and .be
domains, and store per domain:

  authority = share of the same country's domains with a lower harmonic centrality (0-100),
              i.e. "beter dan X% van alle .nl-domeinen"; equal values share the same score.

Stored in a separate SQLite file (authority.db) so the import never locks the crawler's database;
a new import is written to a fresh table and swapped in at the end, so readers never see half data.

Command line (on the server):
  docker exec fajaede-backend python -m backend.benchmark.authority import          # latest release
  docker exec fajaede-backend python -m backend.benchmark.authority status
  docker exec fajaede-backend python -m backend.benchmark.authority seed 40000      # add top domains to the benchmark
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sqlite3
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

DB_PATH = os.getenv("AUTHORITY_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "authority.db"))
GRAPHINFO_URL = "https://index.commoncrawl.org/graphinfo.json"
RANKS_URL = ("https://data.commoncrawl.org/projects/hyperlinkgraph/{release}/domain/"
             "{release}-domain-ranks.txt.gz")
TLDS = ("nl", "be")
HEADER = "#harmonicc_pos\t#harmonicc_val\t#pr_pos\t#pr_val\t#host_rev\t#n_hosts"
USER_AGENT = "Mozilla/5.0 (compatible; FajaedeBot/1.0; +https://psa.fajaede.nl/bot)"
BATCH = 50_000

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


def _stream_lines(release: str) -> Iterator[str]:
    request = urllib.request.Request(RANKS_URL.format(release=release), headers={"User-Agent": USER_AGENT})
    response = urllib.request.urlopen(request, timeout=120)
    with response, gzip.GzipFile(fileobj=response) as unzipped:
        yield from io.TextIOWrapper(unzipped, encoding="utf-8", errors="replace")


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
        # score = share of the TLD's domains with a strictly lower centrality (a tie group of n domains
        # starting at local_rank r has total - (r - 1) - n domains below it)
        conn.execute("CREATE TEMP TABLE ties AS SELECT tld, local_rank, COUNT(*) AS n FROM authority_new"
                     " GROUP BY tld, local_rank")
        conn.execute("CREATE INDEX temp.ties_key ON ties(tld, local_rank)")
        for tld, total in counts.items():
            if total:
                conn.execute(
                    "UPDATE authority_new SET score = (SELECT CAST(ROUND(100.0 * (? - (t.local_rank - 1) - t.n) / ?)"
                    " AS INTEGER) FROM ties t WHERE t.tld = authority_new.tld AND t.local_rank = authority_new.local_rank)"
                    " WHERE tld = ?", (total, total, tld))
        conn.execute("DROP TABLE temp.ties")
        conn.execute("DROP TABLE IF EXISTS authority")
        conn.execute("ALTER TABLE authority_new RENAME TO authority")
        conn.execute("CREATE INDEX IF NOT EXISTS authority_rank ON authority(tld, local_rank)")
        summary = {"release": release, "imported_at": _now(), "lines_read": read, "domains": counts,
                   "seconds": round(time.monotonic() - started)}
        _set_meta(conn, import_running=False, last_import=summary, import_lines=read)
    return summary


def run_import(release: str | None = None, path: str | None = None) -> dict[str, Any]:
    release = release or latest_release()
    try:
        return import_ranks(_stream_lines(release), release, path)
    except BaseException as error:
        with connect(path) as conn:
            _set_meta(conn, import_running=False, import_error=f"{type(error).__name__}: {error}"[:500])
        raise


def lookup(domains: Iterable[str], path: str | None = None) -> dict[str, dict[str, Any]]:
    """Authority per domain ({} when not imported yet or unknown). 'www.' is ignored."""
    wanted = {d.lower().removeprefix("www.") for d in domains if d}
    if not wanted or not os.path.exists(path or DB_PATH):
        return {}
    try:
        with connect(path) as conn:
            marks = ",".join("?" * len(wanted))
            rows = conn.execute(f"SELECT domain, score, local_rank, tld FROM authority WHERE domain IN ({marks})",
                                sorted(wanted)).fetchall()
            totals = (meta(conn).get("last_import") or {}).get("domains", {})
    except sqlite3.OperationalError:
        return {}
    return {row["domain"]: {"score": row["score"], "rank": row["local_rank"], "of": totals.get(row["tld"])}
            for row in rows}


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
