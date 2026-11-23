"""Does Common Crawl know this domain? A Bloom filter of every domain in the Common Crawl web graph
(all extensions, ~100 million), so the own link index can accept links from .com/.eu/.de/... sites
without ever counting a link twice: Common Crawl's referring-domain count already includes every
site it knows, whatever the extension.

  - Built from the graph's vertices file (the same file backlinks.py reads), streamed, never stored.
  - About 1% false positives: then a genuinely new site is treated as known and its link is skipped.
    That is the safe side (never count twice). There are no false negatives.
  - Stored next to authority.db (~180 MB) and read through mmap, so it costs no process memory.

  docker exec -d fajaede-backend python -m backend.benchmark.ccdomains build      # current release, ~15 min
  docker exec fajaede-backend python -m backend.benchmark.ccdomains status
  docker exec fajaede-backend python -m backend.benchmark.ccdomains check voorbeeld.com
"""

from __future__ import annotations

import hashlib
import json
import math
import mmap
import os
import sys
import time
from typing import Any, Iterable

from backend.benchmark import authority

PATH = os.getenv("CC_DOMAINS_PATH", os.path.join(os.path.dirname(authority.DB_PATH), "cc-domains.bloom"))
CAPACITY = 150_000_000      # domains; the graph has ~100 million, room to grow
ERROR_RATE = 0.01
MAGIC = b"FJDBLOOM1\n"


def _sizes(capacity: int, error_rate: float) -> tuple[int, int]:
    bits = math.ceil(-capacity * math.log(error_rate) / (math.log(2) ** 2))
    return bits, max(1, round(bits / capacity * math.log(2)))


def _positions(domain: str, bits: int, hashes: int) -> list[int]:
    digest = hashlib.blake2b(domain.encode("utf-8"), digest_size=16).digest()
    a, b = int.from_bytes(digest[:8], "little"), int.from_bytes(digest[8:], "little") | 1
    return [(a + i * b) % bits for i in range(hashes)]


def candidates(domain: str) -> list[str]:
    """The forms under which the graph may list a site: the domain itself and its last two and three labels
    ('www.shop.example.co.uk' -> 'shop.example.co.uk', 'co.uk', 'example.co.uk'). Checking all of them
    errs on 'known', never on 'unknown'."""
    labels = [part for part in domain.lower().strip().rstrip(".").removeprefix("www.").split(".") if part]
    forms = {".".join(labels)}
    for n in (2, 3):
        if len(labels) >= n:
            forms.add(".".join(labels[-n:]))
    return sorted(forms)


class Builder:
    def __init__(self, capacity: int = CAPACITY, error_rate: float = ERROR_RATE):
        self.bits, self.hashes = _sizes(capacity, error_rate)
        self.array = bytearray((self.bits + 7) // 8)
        self.count = 0

    def add(self, domain: str) -> None:
        for pos in _positions(domain, self.bits, self.hashes):
            self.array[pos >> 3] |= 1 << (pos & 7)
        self.count += 1

    def add_reversed(self, reversed_domain: bytes) -> None:
        """A graph vertex is written reversed ('com.example' for example.com)."""
        self.add(".".join(reversed(reversed_domain.decode("utf-8", "replace").split("."))))

    def save(self, path: str, release: str) -> None:
        header = json.dumps({"bits": self.bits, "hashes": self.hashes, "count": self.count, "release": release,
                             "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}).encode()
        tmp = path + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(MAGIC + len(header).to_bytes(4, "little") + header)
            handle.write(self.array)
        os.replace(tmp, path)          # readers never see a half-written file
        _cache.clear()


_cache: dict[str, Any] = {}


def _open(path: str | None = None) -> dict[str, Any] | None:
    path = path or PATH
    try:
        stat = os.stat(path)
    except OSError:
        return None
    cached = _cache.get(path)
    if cached and cached["mtime"] == stat.st_mtime:
        return cached
    with open(path, "rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            return None
        size = int.from_bytes(handle.read(4), "little")
        header = json.loads(handle.read(size))
        mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
    entry = {"mtime": stat.st_mtime, "mm": mapped, "offset": len(MAGIC) + 4 + size, **header}
    _cache[path] = entry
    return entry


def available(path: str | None = None) -> bool:
    return _open(path) is not None


def known(domain: str, path: str | None = None) -> bool | None:
    """True: Common Crawl (probably) knows the site. False: certainly not. None: no filter built yet."""
    bloom = _open(path)
    if bloom is None:
        return None
    mm, offset, bits, hashes = bloom["mm"], bloom["offset"], bloom["bits"], bloom["hashes"]
    for form in candidates(domain):
        if all(mm[offset + (pos >> 3)] & (1 << (pos & 7)) for pos in _positions(form, bits, hashes)):
            return True
    return False


def counted_by_common_crawl(domains: Iterable[str], path: str | None = None) -> set[str]:
    """The domains whose links Common Crawl already counts: .nl/.be from the authority ranks, every other
    extension from the Bloom filter. Without a filter, every non-.nl/.be domain counts as known (safe side:
    nothing is ever counted twice)."""
    wanted = {d.lower().removeprefix("www.") for d in domains if d}
    local = {d for d in wanted if d.rsplit(".", 1)[-1] in authority.TLDS}
    found = authority.known(local) if local else set()
    for domain in wanted - local:
        if known(domain, path) is not False:
            found.add(domain)
    return found


def status(path: str | None = None) -> dict[str, Any]:
    bloom = _open(path)
    if bloom is None:
        return {"built": False, "path": path or PATH}
    return {"built": True, "path": path or PATH, **{k: bloom[k] for k in ("release", "count", "bits", "hashes", "built_at")}}


def build_from_vertices(lines: Iterable[bytes], release: str, path: str | None = None,
                        capacity: int = CAPACITY) -> dict[str, Any]:
    builder = Builder(capacity)
    for line in lines:
        rev = line.partition(b"\t")[2].partition(b"\t")[0]
        if rev:
            builder.add_reversed(rev)
    if not builder.count:
        raise ValueError("No domains in the vertices file")
    builder.save(path or PATH, release)
    return status(path)


def run_build(release: str | None = None, path: str | None = None) -> dict[str, Any]:
    from backend.benchmark import backlinks   # lazy: keeps this module light for lookups
    if release is None:
        with authority.connect() as conn:
            release = (authority.meta(conn).get("last_import") or {}).get("release")
    if not release:
        raise ValueError("Run the authority import first: python -m backend.benchmark.authority import")
    return build_from_vertices(backlinks._stream(release, "vertices"), release, path)


def _main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "build":
        print(json.dumps(run_build(argv[2] if len(argv) > 2 else None), indent=2))
    elif command == "status":
        print(json.dumps(status(), indent=2))
    elif command == "check" and len(argv) > 2:
        print(json.dumps({domain: known(domain) for domain in argv[2:]}, indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
