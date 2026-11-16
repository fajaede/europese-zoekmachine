"""Tests for the weekly fajaede.eu accounting report."""

# pylint: disable=protected-access,missing-function-docstring,missing-class-docstring

import csv
import io
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from api import accounting_report, developer_api
from api.routes import builder

# A Thursday and the Monday before it, 12:00 Brussels time.
THURSDAY = int(datetime(2026, 11, 19, 12, 0, tzinfo=accounting_report.LOCAL_TZ).timestamp())
MONDAY = int(datetime(2026, 11, 16, 12, 0, tzinfo=accounting_report.LOCAL_TZ).timestamp())


class AccountingReportTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "data.db")
        self.patches = [
            patch.object(builder, "DB_PATH", db),
            patch.object(developer_api, "DB_PATH", db),
            patch.dict(os.environ, {"ACCOUNTANT_EMAIL": "boekhouder@example.com"}),
        ]
        for p in self.patches:
            p.start()
        builder.init_builder_storage()
        developer_api.init_db()
        conn = builder._connect()
        conn.execute("INSERT INTO builder_accounts (id, email, created_at) VALUES ('acc', 'klant@example.com', 0)")
        day = 86400
        conn.executemany(
            "INSERT INTO builder_payments (order_id, mollie_id, account_id, credits, amount, mode, status, granted_at, created_at) "
            "VALUES (?, ?, 'acc', 10, '5.00', ?, ?, ?, 0)",
            [
                ("b1", "tr_builder_live", "live", "paid", THURSDAY - 2 * day),
                ("b2", "tr_builder_test", "test", "paid", THURSDAY - 2 * day),   # test mode: excluded
                ("b3", "tr_builder_open", "live", "open", None),                 # unpaid: excluded
                ("b4", "tr_builder_old", "live", "paid", THURSDAY - 30 * day),   # before the period
            ],
        )
        conn.commit()
        conn.close()
        conn = developer_api.get_db()
        conn.execute("INSERT INTO api_keys (email, api_key) VALUES ('dev@example.com', 'sk_eu_x')")
        key_id = conn.execute("SELECT id FROM api_keys").fetchone()[0]
        conn.executemany(
            "INSERT INTO developer_payments (order_id, key_id, plan, days, amount, mode, mollie_id, status, created_at, "
            "granted_at, invoice_name, invoice_vat, invoice_address, reverse_charge) VALUES (?, ?, ?, ?, ?, 'live', ?, 'paid', 0, ?, ?, ?, ?, ?)",
            [
                ("d1", key_id, "premium", 90, "65.29", "tr_dev_nl", THURSDAY - day, "Firma =NL BV", "NL123456789B01", "Straat 1, Amsterdam", 1),
                ("d2", key_id, "pro", 30, "9.00", "tr_dev_b2c", THURSDAY - 3 * day, None, None, None, 0),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _rows(self, csv_bytes):
        text = csv_bytes.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text), delimiter=";"))

    def test_report_has_same_columns_and_only_live_paid_sales(self):
        rows = accounting_report.collect_rows(THURSDAY - 7 * 86400, THURSDAY)
        self.assertEqual([r[1] for r in rows], ["tr_dev_b2c", "tr_builder_live", "tr_dev_nl"])  # sorted by date
        parsed = self._rows(accounting_report.build_csv(rows))
        self.assertEqual(parsed[0], accounting_report.HEADER)
        self.assertEqual(len(parsed[0]), 18)

        builder_row = parsed[2]
        self.assertEqual(builder_row[2], "Builder: 10 credits")
        self.assertEqual(builder_row[7:11], ["4,13", "21%", "0,87", "5,00"])
        self.assertEqual(builder_row[6], "Onbekend")

        nl = parsed[3]
        self.assertEqual(nl[3:6], ["NL", "Netherlands", "onderneming"])
        self.assertEqual(nl[7:11], ["65,29", "0%", "0,00", "65,29"])
        self.assertEqual(nl[14], "Firma =NL BV")  # "=" not at the start: no prefix needed
        self.assertEqual(nl[16:18], ["NL123456789B01", "Ja"])

    def test_formula_injection_is_neutralised(self):
        self.assertEqual(accounting_report._cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")

    def test_weekly_send_once_on_thursday_and_not_on_monday(self):
        with patch.object(accounting_report, "send_email") as mail:
            self.assertEqual(accounting_report.maybe_send_weekly_report(MONDAY), "not thursday yet")
            result = accounting_report.maybe_send_weekly_report(THURSDAY)
            again = accounting_report.maybe_send_weekly_report(THURSDAY + 3600)
        self.assertEqual(result, "sent (3 sales)")
        self.assertEqual(again, "already sent")
        self.assertEqual(mail.call_count, 1)
        subject, recipient, _body = mail.call_args.args
        self.assertIn("Weekly Sales Report", subject)
        self.assertEqual(recipient, "boekhouder@example.com")
        filename, content, mime = mail.call_args.kwargs["attachments"][0]
        self.assertTrue(filename.endswith(".csv"))
        self.assertEqual(mime, "text/csv")
        self.assertEqual(len(self._rows(content)), 4)  # header + 3 sales

    def test_after_a_sunday_start_the_next_report_comes_on_thursday(self):
        sunday = THURSDAY - 4 * 86400  # 15-11-2026, like the first deploy
        with patch.object(accounting_report, "send_email"):
            self.assertTrue(accounting_report.maybe_send_weekly_report(sunday).startswith("sent"))
        with patch.object(accounting_report, "send_report", return_value=0) as send:
            self.assertEqual(accounting_report.maybe_send_weekly_report(MONDAY), "not thursday yet")
            self.assertEqual(accounting_report.maybe_send_weekly_report(THURSDAY - 13 * 3600), "not thursday yet")  # Wed 23:00
            self.assertEqual(accounting_report.maybe_send_weekly_report(THURSDAY), "sent (0 sales)")  # Thursday, not Saturday
            self.assertEqual(accounting_report.maybe_send_weekly_report(THURSDAY + 2 * 86400), "already sent")
        self.assertEqual(send.call_args.args[0], sunday)  # continues where the Sunday report ended

    def test_next_report_starts_where_the_previous_ended(self):
        with patch.object(accounting_report, "send_email"):
            accounting_report.maybe_send_weekly_report(THURSDAY)
        with patch.object(accounting_report, "send_report", return_value=0) as send:
            accounting_report.maybe_send_weekly_report(THURSDAY + 7 * 86400)
        since, until, _ = send.call_args.args
        self.assertEqual((since, until), (THURSDAY, THURSDAY + 7 * 86400))

    def test_failed_send_is_retried(self):
        with patch.object(accounting_report, "send_email", side_effect=OSError("smtp down")):
            self.assertEqual(accounting_report.maybe_send_weekly_report(THURSDAY), "failed")
        with patch.object(accounting_report, "send_email") as mail:
            self.assertEqual(accounting_report.maybe_send_weekly_report(THURSDAY + 1800), "sent (3 sales)")
        self.assertEqual(mail.call_count, 1)

    def test_multiple_recipients_and_invoice_zip(self):
        from api import invoices  # pylint: disable=import-outside-toplevel
        invoices.issue_invoice(mollie_id="tr_builder_live", order_id="b1", product="builder", description="Builder",
                               amount="5.00", mode="live", customer_email="klant@example.com")
        invoices.issue_invoice(mollie_id="tr_test_x", order_id="bx", product="builder", description="Builder",
                               amount="5.00", mode="test", customer_email="klant@example.com")
        with patch.dict(os.environ, {"ACCOUNTANT_EMAIL": "<info@vcaccountants.be,fajaede@live.nl>"}):
            self.assertEqual(accounting_report.accountant_recipients(), ["info@vcaccountants.be", "fajaede@live.nl"])
        with patch.dict(os.environ, {"ACCOUNTANT_EMAIL": "boekhouder@example.com, fajaede@live.nl; boekhouder@example.com"}):
            self.assertEqual(accounting_report.accountant_recipients(), ["boekhouder@example.com", "fajaede@live.nl"])
            with patch.object(accounting_report, "send_email") as mail:
                accounting_report.send_report(THURSDAY - 7 * 86400, THURSDAY + 86400 * 400, accounting_report.accountant_email())
        self.assertEqual([c.args[1] for c in mail.call_args_list], ["boekhouder@example.com", "fajaede@live.nl"])
        attachments = mail.call_args.kwargs["attachments"]
        self.assertEqual([a[2] for a in attachments], ["text/csv", "application/zip"])
        import zipfile as _zip  # pylint: disable=import-outside-toplevel
        names = _zip.ZipFile(io.BytesIO(attachments[1][1])).namelist()
        self.assertEqual(len(names), 1)  # only the live EU invoice, not the TEST one
        self.assertTrue(names[0].startswith("Factuur_EU-"))
        self.assertIn("zip-bestand", mail.call_args.args[2])

    def test_no_sales_sends_short_mail(self):
        with patch.object(accounting_report, "send_email") as mail:
            accounting_report.send_report(0, 1000, "boekhouder@example.com")
        self.assertIn("No sales", mail.call_args.args[0])
        self.assertNotIn("attachments", mail.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
