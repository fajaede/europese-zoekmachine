"""Tests for domain authority from the Common Crawl ranks file."""

import os
import tempfile
import unittest

from backend.benchmark import authority

HEADER = authority.HEADER + "\n"


def line(pos, value, rev, pr_pos=None, hosts=1):
    return f"{pos}\t{value}\t{pr_pos or pos}\t0.001\t{rev}\t{hosts}\n"


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "authority.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_line_keeps_only_nl_and_be(self):
        record = authority.parse_line(line(7, 2.5e7, "nl.nu", hosts=12))
        self.assertEqual(record["domain"], "nu.nl")
        self.assertEqual((record["tld"], record["hc_pos"], record["n_hosts"]), ("nl", 7, 12))
        self.assertEqual(authority.parse_line(line(8, 1.0, "be.vrt.www"))["domain"], "www.vrt.be")
        self.assertIsNone(authority.parse_line(line(1, 3e7, "com.google")))
        self.assertIsNone(authority.parse_line(line(1, 3e7, "nl")))            # TLD alone
        self.assertIsNone(authority.parse_line(HEADER))
        self.assertIsNone(authority.parse_line("garbage"))

    def test_import_scores_per_country_with_ties(self):
        lines = [HEADER, line(1, 9.0, "com.google"), line(2, 8.0, "nl.groot"), line(3, 7.0, "be.sterk"),
                 line(4, 6.0, "nl.middel"), line(5, 5.0, "nl.klein-a"), line(6, 5.0, "nl.klein-b"),
                 line(7, 4.0, "be.zwak")]
        summary = authority.import_ranks(lines, "test-release", self.path)
        self.assertEqual(summary["domains"], {"nl": 4, "be": 2})
        found = authority.lookup(["groot.nl", "www.middel.nl", "klein-a.nl", "klein-b.nl", "sterk.be", "zwak.be",
                                  "onbekend.nl"], self.path)
        self.assertEqual(found["groot.nl"]["score"], 75)       # 3 of 4 .nl domains below it
        self.assertEqual(found["middel.nl"]["score"], 50)
        self.assertEqual(found["klein-a.nl"]["score"], 0)      # tie: nobody strictly below
        self.assertEqual(found["klein-a.nl"]["rank"], found["klein-b.nl"]["rank"])
        self.assertEqual((found["sterk.be"]["score"], found["zwak.be"]["score"]), (50, 0))
        self.assertEqual(found["groot.nl"]["of"], 4)
        self.assertNotIn("onbekend.nl", found)

    def test_reimport_replaces_and_records_meta(self):
        authority.import_ranks([HEADER, line(1, 2.0, "nl.oud")], "r1", self.path)
        authority.import_ranks([HEADER, line(1, 2.0, "nl.nieuw")], "r2", self.path)
        self.assertEqual(set(authority.lookup(["oud.nl", "nieuw.nl"], self.path)), {"nieuw.nl"})
        with authority.connect(self.path) as conn:
            info = authority.meta(conn)
        self.assertEqual(info["last_import"]["release"], "r2")
        self.assertFalse(info["import_running"])

    def test_bad_header_or_empty_download_fails_without_swapping(self):
        authority.import_ranks([HEADER, line(1, 2.0, "nl.blijft")], "r1", self.path)
        with self.assertRaises(ValueError):
            authority.import_ranks(["#other\tformat\n", line(1, 2.0, "nl.x")], "r2", self.path)
        with self.assertRaises(ValueError):
            authority.import_ranks([HEADER, line(1, 2.0, "com.x")], "r3", self.path)
        self.assertIn("blijft.nl", authority.lookup(["blijft.nl"], self.path))

    def test_top_domains_skips_known_and_lookup_without_db(self):
        authority.import_ranks([HEADER, line(1, 9.0, "nl.a"), line(2, 8.0, "be.b"), line(3, 7.0, "nl.c")],
                               "r", self.path)
        top = authority.top_domains(2, {"a.nl"}, self.path)
        self.assertEqual([t["domain"] for t in top], ["b.be", "c.nl"])
        self.assertEqual(authority.lookup(["a.nl"], os.path.join(self.tmp.name, "missing.db")), {})

    def test_competitors_response_gets_authority(self):
        from unittest import mock
        from backend.benchmark.routes import add_authority
        authority.import_ranks([HEADER, line(1, 9.0, "nl.leider"), line(2, 8.0, "nl.jij"), line(3, 7.0, "nl.ander")],
                               "r", self.path)
        result = {"domain": "jij.nl", "ahead": [{"domain": "leider.nl"}], "leader": None,
                  "behind": {"domain": "onbekend.nl"}}
        with mock.patch.object(authority, "DB_PATH", self.path):
            add_authority(result)
        self.assertEqual(result["your_authority"], 33)
        self.assertEqual(result["ahead"][0]["authority"], 67)
        self.assertIsNone(result["behind"]["authority"])

    def test_only_one_import_at_a_time_and_stale_lock_expires(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
        authority.claim_import("backlinks", self.path, now=now)
        with self.assertRaises(authority.ImportBusy):
            authority.claim_import("authority", self.path, now=now + timedelta(hours=1))
        authority.claim_import("authority", self.path, now=now + timedelta(hours=authority.LOCK_HOURS + 1))  # stale
        authority.release_import(self.path)
        authority.claim_import("authority", self.path, now=now)                                            # free again
        authority.release_import(self.path)

    def test_run_import_does_not_clear_a_running_import(self):
        from unittest import mock
        authority.claim_import("backlinks", self.path)
        with self.assertRaises(authority.ImportBusy):
            authority.run_import("r", self.path)
        with authority.connect(self.path) as conn:
            self.assertEqual(authority.meta(conn)["import_lock"]["job"], "backlinks")   # lock kept
        authority.release_import(self.path)
        with mock.patch.object(authority, "_stream_lines", return_value=iter([HEADER, line(1, 2.0, "nl.x")])):
            authority.run_import("r", self.path)
        with authority.connect(self.path) as conn:
            self.assertNotIn("import_lock", authority.meta(conn))                       # released after success


class FakeResponse:
    def __init__(self, data, status=200, content_type="application/octet-stream", fail_after=None, length=None):
        self.data, self.status, self.fail_after = data, status, fail_after
        self.headers = {"Content-Type": content_type, "Content-Length": str(length if length is not None else len(data))}
        self.pos = 0

    def readinto(self, buffer):
        if self.fail_after is not None and self.pos >= self.fail_after:
            raise ConnectionResetError("dropped")
        end = len(self.data) if self.fail_after is None else min(len(self.data), self.fail_after)
        chunk = self.data[self.pos:min(end, self.pos + len(buffer))]
        buffer[:len(chunk)] = chunk
        self.pos += len(chunk)
        return len(chunk)

    def close(self):
        pass


class ResumableDownloadTests(unittest.TestCase):
    def test_resumes_after_drop_and_slowdown_with_range(self):
        import gzip as gz
        import io
        import urllib.error
        payload = gz.compress(("".join(f"{i}\tnl.site{i}\n" for i in range(5000))).encode())
        calls = []

        def opener(request, timeout):
            calls.append(request.get_header("Range"))
            if len(calls) == 1:
                return FakeResponse(payload, fail_after=1000)             # drops after 1000 bytes
            if len(calls) == 2:
                raise urllib.error.HTTPError(request.full_url, 503, "SlowDown", {}, None)
            if len(calls) == 3:
                return FakeResponse(b"<?xml SlowDown", content_type="application/xml")
            start = int(request.get_header("Range").split("=")[1].rstrip("-"))
            return FakeResponse(payload[start:], status=206, length=len(payload) - start)

        pauses = []
        raw = authority.ResumableDownload("https://example.test/x.gz", opener=opener, sleep=pauses.append)
        with io.BufferedReader(raw, buffer_size=256) as buffered, gz.GzipFile(fileobj=buffered) as unzipped:
            lines = unzipped.read().decode().splitlines()
        self.assertEqual(len(lines), 5000)
        self.assertEqual(lines[-1], "4999\tnl.site4999")
        self.assertEqual(calls[0], None)
        self.assertTrue(all(c == "Bytes=1000-" or c == "bytes=1000-" for c in calls[1:]))
        self.assertEqual(pauses, [5, 10, 20])

    def test_missing_file_or_changed_file_fails_fast(self):
        import urllib.error
        def missing(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        with self.assertRaises(authority.DownloadError):
            authority.ResumableDownload("https://example.test/x.gz", opener=missing, sleep=lambda s: None).read(10)

        calls = []
        def changed(request, timeout):
            calls.append((request.get_header("Range"), request.get_header("If-range")))
            if len(calls) == 1:
                response = FakeResponse(b"x" * 100, fail_after=10)
                response.headers["ETag"] = '"v1"'
                return response
            return FakeResponse(b"y" * 100, status=200)     # If-Range did not match: whole new file
        raw = authority.ResumableDownload("https://example.test/x.gz", opener=changed, sleep=lambda s: None)
        self.assertEqual(raw.read(10), b"x" * 10)
        with self.assertRaises(authority.DownloadError):
            raw.read(10)
        self.assertEqual(calls[1], ("bytes=10-", '"v1"'))

    def test_gives_up_after_max_retries(self):
        def opener(request, timeout):
            raise ConnectionRefusedError("down")
        raw = authority.ResumableDownload("https://example.test/x.gz", max_retries=2, opener=opener, sleep=lambda s: None)
        with self.assertRaises(OSError):
            raw.read(10)


if __name__ == "__main__":
    unittest.main()
