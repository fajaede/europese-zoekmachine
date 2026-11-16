"""Tests for fajaede.eu invoices (numbering, Peppol references, PDF, emails)."""

# pylint: disable=protected-access,missing-function-docstring,missing-class-docstring

import asyncio
import os
import tempfile
import unittest
import zlib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from api import developer_api, invoices
from api.routes import builder


def _pdf_text(pdf: bytes) -> str:
    """Rough text extraction from the (deflate-compressed) PDF content streams."""
    text = pdf.decode("latin-1")
    out = []
    for chunk in text.split("stream")[1:]:
        raw = chunk.split("endstream")[0].strip("\r\n").encode("latin-1")
        try:
            out.append(zlib.decompress(raw).decode("latin-1"))
        except zlib.error:
            out.append(raw.decode("latin-1"))
    return "\n".join(out)


class InvoiceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db = str(Path(self.tmp.name) / "data.db")
        self.patches = [patch.object(builder, "DB_PATH", db), patch.object(developer_api, "DB_PATH", db)]
        for p in self.patches:
            p.start()
        builder.init_builder_storage()
        developer_api.init_db()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _issue(self, mollie_id, mode="live", vat="", reverse=False, amount="5.00"):
        return invoices.issue_invoice(
            mollie_id=mollie_id, order_id="o-" + mollie_id, product="builder", description="fajaede Builder: 10 credits",
            amount=amount, mode=mode, customer_email="klant@example.com", customer_name="Klant",
            customer_address="Straat 1, Gent" if vat else "", customer_vat=vat, reverse_charge=reverse,
        )

    def test_gapless_series_and_one_invoice_per_payment(self):
        year = datetime.now(invoices.LOCAL_TZ).year
        first = self._issue("tr_1")
        again = self._issue("tr_1")  # Mollie repeats the webhook
        second = self._issue("tr_2")
        self.assertEqual(first["invoice_number"], f"EU-{year}-0001")
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(second["invoice_number"], f"EU-{year}-0002")

    def test_test_and_peppol_never_use_eu_numbers(self):
        year = datetime.now(invoices.LOCAL_TZ).year
        self.assertEqual(self._issue("tr_t", mode="test")["invoice_number"], "TEST-0001")
        peppol = self._issue("tr_be", vat="BE0123456789", amount="9.00")
        self.assertEqual(peppol["invoice_number"], "PEPPOL-0001")
        self.assertFalse(invoices.is_real_invoice(peppol))
        self.assertEqual(self._issue("tr_x")["invoice_number"], f"EU-{year}-0001")  # still the first EU number

    def test_amounts_vat_and_reverse_charge(self):
        b2c = self._issue("tr_b2c")
        self.assertEqual((b2c["price_excl"], b2c["vat_amount"], b2c["amount"]), ("4.13", "0.87", "5.00"))
        nl = self._issue("tr_nl", vat="NL123456789B01", reverse=True, amount="7.44")
        self.assertEqual((nl["price_excl"], nl["vat_amount"], nl["amount"], nl["country_code"]), ("7.44", "0.00", "7.44", "NL"))

    def test_pdf_contains_mandatory_details(self):
        nl = self._issue("tr_nl", vat="NL123456789B01", reverse=True, amount="7.44")
        pdf = invoices.render_pdf(nl)
        self.assertTrue(pdf.startswith(b"%PDF"))
        text = _pdf_text(pdf)
        for needle in ("FACTUUR", nl["invoice_number"], "BE1032407424", "Lodewijk de Koninckstraat 31",
                       "NL123456789B01", "art. 196", "EUR 7,44"):
            self.assertIn(needle, text, needle)

    def test_builder_confirmation_has_pdf_invoice(self):
        with patch.object(builder, "send_email") as mail:
            asyncio.run(builder._send_purchase_confirmation("klant@example.com", "tr_build", "order-b", "live"))
        subject, recipient, body, attachments = mail.call_args.args
        self.assertEqual(recipient, "klant@example.com")
        self.assertIn("factuur EU-", subject)
        self.assertIn("Factuurnummer: EU-", body)
        self.assertTrue(attachments[0][0].startswith("Factuur_EU-"))

    def _developer_order(self, vat, reverse):
        conn = developer_api.get_db()
        conn.execute("INSERT INTO api_keys (email, api_key) VALUES ('dev@example.com', 'sk_eu_x')")
        conn.commit()
        conn.close()
        return {"days": 30, "amount": "9.00" if not reverse else "7.44", "mode": "live", "invoice_name": "Firma",
                "invoice_address": "Straat 1", "invoice_vat": vat, "reverse_charge": int(reverse)}

    def test_belgian_business_gets_no_pdf_but_owner_gets_peppol_mail(self):
        order = self._developer_order("BE0123456789", False)
        with patch.object(developer_api, "send_email") as mail, patch.dict(os.environ, {"INVOICE_NOTIFY_EMAIL": "owner@example.com"}):
            asyncio.run(developer_api._send_developer_confirmation("o1", "tr_be", "dev@example.com", developer_api.PLANS["pro"], order, 2_000_000_000))
        customer, owner = (c.args for c in mail.call_args_list)
        self.assertIn("via Peppol", customer[2])
        self.assertTrue(len(customer) < 4 or not customer[3])  # no attachment
        self.assertEqual(owner[1], "owner@example.com")
        self.assertTrue(owner[0].startswith("Factuur maken via Peppol"))

    def test_foreign_business_gets_pdf_and_no_owner_mail(self):
        order = self._developer_order("NL123456789B01", True)
        with patch.object(developer_api, "send_email") as mail:
            asyncio.run(developer_api._send_developer_confirmation("o2", "tr_nl", "dev@example.com", developer_api.PLANS["pro"], order, 2_000_000_000))
        self.assertEqual(mail.call_count, 1)
        self.assertTrue(mail.call_args.args[3][0][0].startswith("Factuur_EU-"))


if __name__ == "__main__":
    unittest.main()
