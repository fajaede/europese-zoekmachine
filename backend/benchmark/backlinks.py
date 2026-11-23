"""Referring domains ("backlinks") from the Common Crawl domain-level web graph (open data, free).

Two files of the same release as the authority ranks (authority.py), both streamed, never stored:
  vertices (~0.9 GB gzip): id, reversed domain ('nl.voorbeeld'), number of hosts; sorted by reversed
           domain, so all .be and all .nl domains form two contiguous id ranges
  edges    (~7 GB gzip, ~1.8 billion lines): from_id, to_id; one line per linking domain pair

Per .nl/.be domain we keep:
  referring        number of distinct domains (any extension) that link to it
  referring_local  of which .nl/.be domains
  top              up to TOP_K strongest linking .nl/.be domains (by authority score); names and
                   strength of other extensions are not kept (that would need the full graph in memory)

Memory stays small (a few arrays of ~2.5 million entries). Takes roughly half an hour on the server.
Results go to the `backlinks` table in authority.db and replace the previous import only when complete.

  docker exec -d fajaede-backend python -m backend.benchmark.backlinks import     # in the background
  docker exec fajaede-backend python -m backend.benchmark.backlinks status
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from array import array
from typing import Any, Iterable, Iterator

from backend.benchmark import authority

BASE_URL = "https://data.commoncrawl.org/projects/hyperlinkgraph/{release}/domain/{release}-domain-{part}.txt.gz"
TOP_K = 5
PROGRESS_EVERY = 50_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS backlinks (
    domain TEXT PRIMARY KEY,
    referring INTEGER NOT NULL,
    referring_local INTEGER NOT NULL,
    top TEXT NOT NULL                -- JSON [{"domain": ..., "authority": ...}], strongest first
);
"""


def _stream(release: str, part: str) -> Iterator[bytes]:
    return authority.open_gzip_lines(BASE_URL.format(release=release, part=part))


class Ranges:
    """The contiguous id ranges of .be and .nl domains, mapped onto one index 0..size-1."""

    def __init__(self, spans: list[tuple[int, int]]):
        self.spans = sorted(spans)                     # [(first_id, last_id), ...]
        self.offsets, total = [], 0
        for first, last in self.spans:
            self.offsets.append(total)
            total += last - first + 1
        self.size = total

    def index(self, node_id: int) -> int:
        for (first, last), offset in zip(self.spans, self.offsets):
            if first <= node_id <= last:
                return offset + node_id - first
        return -1


def read_vertices(lines: Iterable[bytes], conn: sqlite3.Connection,
                  tlds: tuple[str, ...] = authority.TLDS, batch_size: int = 50_000, bloom: Any = None) -> Ranges:
    """Find the id range per TLD and store (index, domain) in a temp table, in batches. The file is
    sorted by reversed domain, so the index is simply the order in which .nl/.be lines appear."""
    prefixes = tuple(f"{tld}.".encode() for tld in tlds)
    spans: dict[bytes, list[int]] = {}
    conn.execute("DROP TABLE IF EXISTS temp.vertex")
    conn.execute("CREATE TEMP TABLE vertex (idx INTEGER PRIMARY KEY, domain TEXT NOT NULL)")
    batch: list[tuple[int, str]] = []
    count = 0
    for line in lines:
        node, _, rest = line.partition(b"	")
        rev = rest.partition(b"	")[0]
        if bloom is not None and rev:
            bloom.add_reversed(rev)          # every domain, any extension (ccdomains)
        prefix = rev[:3]
        if prefix not in prefixes:
            continue
        node_id = int(node)
        span = spans.setdefault(prefix, [node_id, node_id - 1])
        if node_id != span[1] + 1:
            raise ValueError("vertices are not contiguous per extension: file format changed?")
        span[1] = node_id
        batch.append((count, ".".join(reversed(rev.decode().split(".")))))
        count += 1
        if len(batch) >= batch_size:
            conn.executemany("INSERT INTO vertex (idx, domain) VALUES (?, ?)", batch)
            batch.clear()
    if batch:
        conn.executemany("INSERT INTO vertex (idx, domain) VALUES (?, ?)", batch)
    ranges = Ranges([(a, b) for a, b in spans.values()])
    if ranges.size != count:
        raise ValueError("vertex ranges overlap: file format changed?")
    return ranges


def source_scores(conn: sqlite3.Connection, size: int) -> array:
    """Authority score per index (-1 = unknown), from the authority table."""
    scores = array("b", [-1]) * size
    for idx, score in conn.execute("SELECT v.idx, a.score FROM vertex v JOIN authority a ON a.domain = v.domain"):
        scores[idx] = score if score is not None else -1
    return scores


def count_edges(lines: Iterable[bytes], ranges: Ranges, scores: array,
                progress: Any = None) -> tuple[array, array, array]:
    """Referring-domain counts per target index, and the TOP_K strongest .nl/.be sources."""
    size = ranges.size
    referring = array("I", [0]) * size
    local = array("I", [0]) * size
    top = array("i", [-1]) * (size * TOP_K)
    (a1, b1), (a2, b2) = (ranges.spans + [(1, 0)])[:2]      # second span empty when only one TLD
    o1, o2 = ranges.offsets[0], (ranges.offsets + [0, 0])[1]
    read = 0
    for line in lines:
        read += 1
        if progress and read % PROGRESS_EVERY == 0:
            progress(read)
        tab = line.find(b"\t")
        target = int(line[tab + 1:])
        if a1 <= target <= b1:
            t = o1 + target - a1
        elif a2 <= target <= b2:
            t = o2 + target - a2
        else:
            continue
        referring[t] += 1
        source = int(line[:tab])
        if a1 <= source <= b1:
            s = o1 + source - a1
        elif a2 <= source <= b2:
            s = o2 + source - a2
        else:
            continue
        local[t] += 1
        score = scores[s]
        if score < 0:
            continue
        base = t * TOP_K
        weakest, weakest_score = -1, 101
        for k in range(base, base + TOP_K):
            current = top[k]
            if current < 0:
                weakest, weakest_score = k, -1
                break
            if scores[current] < weakest_score:
                weakest, weakest_score = k, scores[current]
        if score > weakest_score:
            top[weakest] = s
    if progress:
        progress(read)
    return referring, local, top


def save(conn: sqlite3.Connection, referring: array, local: array, top: array, scores: array) -> int:
    conn.execute("DROP TABLE IF EXISTS backlinks_new")
    conn.execute(SCHEMA.replace("IF NOT EXISTS backlinks (", "backlinks_new (", 1))
    # names only for the (few, often repeated) strongest sources, not for all 2.5 million domains
    needed = sorted({s for s in top if s >= 0})
    source_names: dict[int, str] = {}
    for start in range(0, len(needed), 900):
        chunk = needed[start:start + 900]
        source_names.update(conn.execute(
            f"SELECT idx, domain FROM vertex WHERE idx IN ({','.join('?' * len(chunk))})", chunk))
    rows = 0
    batch: list[tuple[str, int, int, str]] = []
    for t, domain in _iter_vertex(conn):
        count = referring[t]
        if not count:
            continue
        sources = sorted((s for s in top[t * TOP_K:(t + 1) * TOP_K] if s >= 0), key=lambda s: -scores[s])
        batch.append((domain, count, local[t],
                      json.dumps([{"domain": source_names[s], "authority": scores[s]} for s in sources])))
        if len(batch) >= 50_000:
            _insert(conn, batch)
            rows += len(batch)
            batch.clear()
    _insert(conn, batch)
    rows += len(batch)
    conn.execute("DROP TABLE IF EXISTS backlinks")
    conn.execute("ALTER TABLE backlinks_new RENAME TO backlinks")
    conn.execute("DROP TABLE IF EXISTS temp.vertex")
    return rows


def _iter_vertex(conn: sqlite3.Connection) -> Iterator[tuple[int, str]]:
    """(idx, domain) in order, read in pages so the write connection stays usable."""
    last = -1
    while True:
        page = conn.execute("SELECT idx, domain FROM vertex WHERE idx > ? ORDER BY idx LIMIT 50000", (last,)).fetchall()
        if not page:
            return
        yield from ((row[0], row[1]) for row in page)
        last = page[-1][0]


def _insert(conn: sqlite3.Connection, batch: list[tuple[str, int, int, str]]) -> None:
    if batch:
        conn.executemany("INSERT INTO backlinks_new (domain, referring, referring_local, top) VALUES (?, ?, ?, ?)",
                         batch)


def import_backlinks(vertices: Iterable[bytes], edges: Iterable[bytes], release: str,
                     path: str | None = None, bloom: Any = None) -> dict[str, Any]:
    authority.init(path)
    started = time.monotonic()
    with authority.connect(path) as conn:
        imported = (authority.meta(conn).get("last_import") or {}).get("release")
        if imported != release:
            raise ValueError(f"Import authority for {release} first (have: {imported})")
        authority._set_meta(conn, backlinks_running=True, backlinks_release=release, backlinks_lines=0,
                            backlinks_started_at=authority._now())
        conn.commit()

        def progress(read: int) -> None:
            authority._set_meta(conn, backlinks_lines=read)
            conn.commit()

        ranges = read_vertices(vertices, conn, bloom=bloom)
        if not ranges.size:
            raise ValueError("No .nl/.be domains in the vertices file")
        scores = source_scores(conn, ranges.size)
        referring, local, top = count_edges(edges, ranges, scores, progress)
        if not any(referring):
            raise ValueError("No links found: download incomplete or format changed")
        rows = save(conn, referring, local, top, scores)
        summary = {"release": release, "imported_at": authority._now(), "domains_with_links": rows,
                   "seconds": round(time.monotonic() - started)}
        authority._set_meta(conn, backlinks_running=False, last_backlinks=summary)
    return summary


def run_import(release: str | None = None, path: str | None = None) -> dict[str, Any]:
    if release is None:
        with authority.connect(path) as conn:
            release = (authority.meta(conn).get("last_import") or {}).get("release")
    if not release:
        raise ValueError("Run the authority import first: python -m backend.benchmark.authority import")
    authority.claim_import("backlinks", path)
    try:
        from backend.benchmark import ccdomains
        bloom = ccdomains.Builder()
        summary = import_backlinks(_stream(release, "vertices"), _stream(release, "edges"), release, path, bloom)
        bloom.save(ccdomains.PATH, release)       # "does Common Crawl know this site?" for every extension
        return summary
    except BaseException as error:
        with authority.connect(path) as conn:
            authority._set_meta(conn, backlinks_running=False,
                                backlinks_error=f"{type(error).__name__}: {error}"[:500])
        raise
    finally:
        authority.release_import(path)


def lookup(domain: str, path: str | None = None) -> dict[str, Any] | None:
    """Referring domains for one domain; None when not imported or no links known."""
    wanted = domain.lower().removeprefix("www.")
    try:
        with authority.connect(path) as conn:
            row = conn.execute("SELECT referring, referring_local, top FROM backlinks WHERE domain = ?",
                               (wanted,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return {"referring_domains": row["referring"], "referring_local": row["referring_local"],
            "top": json.loads(row["top"])}


def counts(domains: Iterable[str], path: str | None = None) -> dict[str, int]:
    wanted = sorted({d.lower().removeprefix("www.") for d in domains if d})
    if not wanted:
        return {}
    try:
        with authority.connect(path) as conn:
            marks = ",".join("?" * len(wanted))
            return dict(conn.execute(f"SELECT domain, referring FROM backlinks WHERE domain IN ({marks})", wanted))
    except sqlite3.OperationalError:
        return {}


def _main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "import":
        print(json.dumps(run_import(argv[2] if len(argv) > 2 else None), indent=2))
    elif command == "status":
        authority.init()
        with authority.connect() as conn:
            info = {k: v for k, v in authority.meta(conn).items() if k.startswith(("backlinks", "last_backlinks"))}
            try:
                info["rows"] = conn.execute("SELECT COUNT(*) FROM backlinks").fetchone()[0]
            except sqlite3.OperationalError:
                info["rows"] = 0
        if info.get("backlinks_running") and info.get("backlinks_lines"):
            info["progress"] = f"{info['backlinks_lines'] / 1_817_202_146:.0%} of ~1.8 billion links"
        print(json.dumps(info, indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
