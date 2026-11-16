"""Invoices for fajaede.eu (Builder credits and Developer API passes).

- One gapless series for real sales: EU-2026-0001, EU-2026-0002, ... (Belgian VAT law).
- Mollie test-mode payments get their own TEST-0001 series and never use an EU number.
- Belgian business customers receive their invoice via Peppol from the accounting software
  (Octopus): they get a PEPPOL-0001 reference here instead of an invoice, so the EU series
  never has a number without an invoice.
- Exactly one invoice per Mollie payment (UNIQUE mollie_id), also when Mollie repeats the webhook.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fpdf import FPDF

from api import developer_api

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("Europe/Brussels")
except Exception:  # pylint: disable=broad-exception-caught
    LOCAL_TZ = timezone.utc

SELLER = {
    "name": "fajaede",
    "street": "Lodewijk de Koninckstraat 31",
    "city": "2600 Berchem (Antwerpen)",
    "country": "België",
    "vat": "BE1032407424",
    "email": "info@fajaede.nl",
    "website": "www.fajaede.eu",
}
VAT_RATE = Decimal("0.21")
TEST_YEAR = 0
PEPPOL_YEAR = 1


def _db() -> sqlite3.Connection:
    conn = developer_api.get_db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS eu_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mollie_id TEXT NOT NULL UNIQUE,
            order_id TEXT NOT NULL,
            product TEXT NOT NULL,
            description TEXT NOT NULL,
            customer_type TEXT NOT NULL,
            customer_name TEXT,
            customer_email TEXT,
            customer_address TEXT,
            customer_vat TEXT,
            country_code TEXT,
            price_excl TEXT NOT NULL,
            vat_rate TEXT NOT NULL,
            vat_amount TEXT NOT NULL,
            amount TEXT NOT NULL,
            reverse_charge INTEGER NOT NULL DEFAULT 0,
            mode TEXT NOT NULL,
            invoice_year INTEGER NOT NULL,
            invoice_seq INTEGER NOT NULL,
            invoice_number TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE (invoice_year, invoice_seq)
        )"""
    )
    return conn


def is_peppol_customer(vat_number: str | None) -> bool:
    """Belgian businesses must receive their invoice via Peppol."""
    return bool(vat_number) and str(vat_number).upper().startswith("BE")


def split_amount(amount: str, reverse_charge: bool) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(price excl. VAT, VAT rate, VAT amount, total) for an amount paid by the customer."""
    total = Decimal(amount)
    if reverse_charge:
        return total, Decimal("0"), Decimal("0.00"), total
    excl = (total / (Decimal(1) + VAT_RATE)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return excl, VAT_RATE, total - excl, total


def issue_invoice(
    *,
    mollie_id: str,
    order_id: str,
    product: str,
    description: str,
    amount: str,
    mode: str,
    customer_email: str,
    customer_name: str = "",
    customer_address: str = "",
    customer_vat: str = "",
    reverse_charge: bool = False,
) -> dict:
    """Create (once) the invoice or Peppol reference for a paid Mollie payment and return it."""
    business = bool(customer_vat)
    peppol = is_peppol_customer(customer_vat)
    is_test = mode != "live"
    excl, rate, vat, total = split_amount(amount, reverse_charge)
    prefix = (customer_vat or "")[:2].upper()
    country = ("GR" if prefix == "EL" else prefix) if business else ""

    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")  # serialises numbering: no duplicates, no gaps
        existing = conn.execute("SELECT * FROM eu_invoices WHERE mollie_id = ?", (mollie_id,)).fetchone()
        if existing:
            conn.rollback()
            return dict(existing)
        year = TEST_YEAR if is_test else (PEPPOL_YEAR if peppol else datetime.now(LOCAL_TZ).year)
        seq = conn.execute(
            "SELECT COALESCE(MAX(invoice_seq), 0) + 1 FROM eu_invoices WHERE invoice_year = ?", (year,)
        ).fetchone()[0]
        number = (
            f"TEST-{seq:04d}" if is_test else (f"PEPPOL-{seq:04d}" if peppol else f"EU-{year}-{seq:04d}")
        )
        conn.execute(
            """INSERT INTO eu_invoices (mollie_id, order_id, product, description, customer_type, customer_name,
                customer_email, customer_address, customer_vat, country_code, price_excl, vat_rate, vat_amount,
                amount, reverse_charge, mode, invoice_year, invoice_seq, invoice_number, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mollie_id, order_id, product, description, "onderneming" if business else "particulier",
                customer_name, customer_email, customer_address, customer_vat, country,
                f"{excl:.2f}", f"{rate:.2f}", f"{vat:.2f}", f"{total:.2f}", int(reverse_charge), mode,
                year, seq, number, int(time.time()),
            ),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM eu_invoices WHERE mollie_id = ?", (mollie_id,)).fetchone())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_real_invoice(invoice: dict) -> bool:
    """False for Peppol references: those get their invoice from the accounting software."""
    return not str(invoice.get("invoice_number", "")).startswith("PEPPOL-")


def invoice_by_mollie_id(mollie_id: str) -> dict | None:
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM eu_invoices WHERE mollie_id = ?", (mollie_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _latin1(text) -> str:
    """The built-in PDF fonts only cover Latin-1: replace anything else (accents stay intact)."""
    return str(text or "").replace("€", "EUR").replace("–", "-").replace("’", "'").encode(
        "latin-1", "replace"
    ).decode("latin-1")


def _euro(value) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    whole, cents = f"{amount:.2f}".split(".")
    return f"EUR {int(whole):,}".replace(",", ".") + f",{cents}"


def render_pdf(invoice: dict) -> bytes:
    """A4 invoice in Dutch with all mandatory Belgian invoice details."""
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(18, 18, 18)
    is_test = invoice.get("mode") != "live"
    date = datetime.fromtimestamp(int(invoice["created_at"]), LOCAL_TZ).strftime("%d-%m-%Y")

    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 10, _latin1("FACTUUR" + ("  (TEST - geen geldige factuur)" if is_test else "")), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 10)
    seller = [SELLER["name"], SELLER["street"], SELLER["city"], SELLER["country"],
              f"Ondernemings- en btw-nummer: {SELLER['vat']}", SELLER["email"], SELLER["website"]]
    details = [
        f"Factuurnummer: {invoice['invoice_number']}",
        f"Factuurdatum: {date}",
        f"Bestelnummer: {invoice['order_id']}",
        "Status: betaald",
    ]
    y = pdf.get_y()
    for line in seller:
        pdf.cell(95, 5, _latin1(line), new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(115, y)
    for line in details:
        pdf.set_x(115)
        pdf.cell(0, 5, _latin1(line), new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(max(pdf.get_y(), y + 7 * 5) + 6)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Klant", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for line in (invoice.get("customer_name"), invoice.get("customer_address"), invoice.get("customer_email"),
                 f"Btw-nummer: {invoice['customer_vat']}" if invoice.get("customer_vat") else ""):
        if line:
            pdf.cell(0, 5, _latin1(line), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(241, 245, 249)
    pdf.cell(110, 7, "Omschrijving", border=1, fill=True)
    pdf.cell(20, 7, "Aantal", border=1, fill=True, align="C")
    pdf.cell(44, 7, "Bedrag excl. btw", border=1, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(110, 7, _latin1(invoice["description"]), border=1)
    pdf.cell(20, 7, "1", border=1, align="C")
    pdf.cell(44, 7, _euro(invoice["price_excl"]), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    reverse = bool(invoice.get("reverse_charge"))
    rate_label = "Btw verlegd (0%)" if reverse else f"Btw {int(Decimal(invoice['vat_rate']) * 100)}%"
    for label, value, bold in (
        ("Subtotaal excl. btw", invoice["price_excl"], False),
        (rate_label, invoice["vat_amount"], False),
        ("Totaal te betalen" if reverse else "Totaal incl. btw", invoice["amount"], True),
    ):
        pdf.set_font("Helvetica", "B" if bold else "", 10)
        pdf.set_x(18 + 86)
        pdf.cell(44, 6, _latin1(label))
        pdf.cell(44, 6, _euro(value), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_font("Helvetica", "", 9)
    if reverse:
        pdf.multi_cell(0, 5, _latin1(
            "Btw verlegd naar de afnemer (art. 196 Richtlijn 2006/112/EG). De afnemer voldoet de btw."
        ), new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(0, 5, _latin1(
        f"Betaald via Mollie op {date} (betaling {invoice['mollie_id']}). Er is geen bedrag meer verschuldigd."
    ), new_x="LMARGIN", new_y="NEXT")
    if is_test:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(0, 5, "TESTBETALING (Mollie testmodus): er is geen geld ontvangen. Dit is geen factuur.",
                       new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def pdf_filename(invoice: dict) -> str:
    return f"Factuur_{invoice['invoice_number']}.pdf"
