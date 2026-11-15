"""Weekly accounting report for fajaede.eu (Builder credits + Developer API passes).

Same CSV columns as the weekly report of the FajaedeSEO WordPress plugin, so the
accountant receives every sales channel in one format. Every Thursday (or the first
check after that) a CSV with all paid live payments since the previous report is
emailed to ACCOUNTANT_EMAIL. Mollie test-mode payments are never included.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from api import developer_api
from api.email_utils import send_email
from api.routes import builder

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("Europe/Brussels")
except Exception:  # pylint: disable=broad-exception-caught  # no tz database in the image
    LOCAL_TZ = timezone.utc

HEADER = [
    "Factuurnummer", "Transactie ID", "Plan", "Land Code", "Land", "Klant Type",
    "EU-consument buiten BE (OSS-drempel)", "Bedrag Excl BTW", "BTW %", "BTW Bedrag", "Totaal",
    "Valuta", "Datum", "Soort", "Klant", "Adres klant", "Btw-nummer klant", "Btw verlegd",
]
VAT_RATE = Decimal("0.21")
WEEK = 7 * 86400
COUNTRY_NAMES = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CY": "Cyprus", "CZ": "Czech Republic",
    "DE": "Germany", "DK": "Denmark", "EE": "Estonia", "GR": "Greece", "ES": "Spain", "FI": "Finland",
    "FR": "France", "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "MT": "Malta", "NL": "Netherlands", "PL": "Poland",
    "PT": "Portugal", "RO": "Romania", "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia",
}


def _cell(value) -> str:
    """Text cell safe against spreadsheet formula injection."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}".replace(".", ",")


def _split_vat(amount: str, reverse_charge: bool) -> tuple[Decimal, Decimal, Decimal, str]:
    """(excl, vat, total, rate label) for an amount that already includes Belgian VAT."""
    total = Decimal(amount)
    if reverse_charge:
        return total, Decimal("0"), total, "0%"
    excl = (total / (Decimal(1) + VAT_RATE)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return excl, total - excl, total, "21%"


def _local(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), LOCAL_TZ).strftime("%d-%m-%Y %H:%M")


def _row(*, payment_id, plan, country_code, customer_type, amount, reverse, paid_at, customer,
         address="", vat_number="") -> list[str]:
    excl, vat, total, rate = _split_vat(amount, reverse)
    known_country = bool(country_code)
    return [
        "",  # fajaede.eu has no own invoice numbers: B2B invoices are made in the accounting software
        _cell(payment_id or ""),
        _cell(plan),
        country_code or "",
        COUNTRY_NAMES.get(country_code, country_code) if known_country else "Onbekend (Belgische btw)",
        customer_type,
        ("Ja" if country_code != "BE" else "Nee") if known_country and customer_type == "particulier"
        else ("Onbekend" if customer_type == "particulier" else "Nee"),
        _money(excl), rate, _money(vat), _money(total), "EUR", _local(paid_at), "Aankoop",
        _cell(customer), _cell(address), _cell(vat_number), "Ja" if reverse else "Nee",
    ]


def collect_rows(since: int, until: int) -> list[list[str]]:
    """All paid live payments of both products with granted_at in [since, until)."""
    rows: list[tuple[int, list[str]]] = []

    conn = builder._connect()  # pylint: disable=protected-access
    try:
        for p in conn.execute(
            """
            SELECT p.mollie_id, p.amount, p.credits, p.granted_at, a.email
            FROM builder_payments p LEFT JOIN builder_accounts a ON a.id = p.account_id
            WHERE p.status = 'paid' AND p.mode = 'live' AND p.granted_at >= ? AND p.granted_at < ?
            """,
            (since, until),
        ):
            rows.append((p["granted_at"], _row(
                payment_id=p["mollie_id"], plan=f"Builder: {p['credits']} credits", country_code="",
                customer_type="particulier", amount=p["amount"], reverse=False,
                paid_at=p["granted_at"], customer=p["email"] or "",
            )))
    except sqlite3.OperationalError:
        logger.info("No builder payments table yet")
    finally:
        conn.close()

    conn = developer_api.get_db()
    try:
        for p in conn.execute(
            """
            SELECT p.*, k.email FROM developer_payments p LEFT JOIN api_keys k ON k.id = p.key_id
            WHERE p.status = 'paid' AND p.mode = 'live' AND p.granted_at >= ? AND p.granted_at < ?
            """,
            (since, until),
        ):
            is_business = bool(p["invoice_vat"])
            prefix = (p["invoice_vat"] or "")[:2].upper()
            plan_name = developer_api.PLANS.get(p["plan"], {}).get("name", p["plan"])
            rows.append((p["granted_at"], _row(
                payment_id=p["mollie_id"], plan=f"Developer API: {plan_name}, {p['days']} dagen",
                country_code=("GR" if prefix == "EL" else prefix) if is_business else "",
                customer_type="onderneming" if is_business else "particulier",
                amount=p["amount"], reverse=bool(p["reverse_charge"]), paid_at=p["granted_at"],
                customer=p["invoice_name"] if is_business else (p["email"] or ""),
                address=p["invoice_address"] or "", vat_number=p["invoice_vat"] or "",
            )))
    except sqlite3.OperationalError:
        logger.info("No developer payments table yet")
    finally:
        conn.close()

    return [row for _, row in sorted(rows, key=lambda item: item[0])]


def build_csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")  # BOM so Excel opens it as UTF-8


def accountant_email() -> str:
    return (
        os.getenv("ACCOUNTANT_EMAIL", "").strip()
        or os.getenv("INVOICE_NOTIFY_EMAIL", "").strip()
        or os.getenv("SMTP_FROM", "").strip()
    )


def send_report(since: int, until: int, recipient: str) -> int:
    """Email the report for [since, until) to recipient; returns the number of sales."""
    rows = collect_rows(since, until)
    period = f"{_local(since)[:10]} tot {_local(until)[:10]}"
    if not rows:
        send_email(
            f"fajaede.eu - Weekly Sales Report (No sales this week) ({period})",
            recipient,
            f"Geen verkopen op fajaede.eu (Builder en Developer API) van {period}.\n",
        )
        return 0
    body = (
        f"In de bijlage de verkopen van fajaede.eu (Builder en Developer API) van {period}.\n\n"
        "Zelfde kolommen als het weekrapport van de FajaedeSEO-plugin.\n"
        "- Particulieren: geen factuur; de bedragen zijn inclusief 21% Belgische btw.\n"
        "  Het land van particulieren wordt niet gevraagd (onder de OSS-drempel geldt Belgische btw).\n"
        "- Bedrijven: factuur wordt apart opgemaakt; klantnaam, adres en btw-nummer staan in het rapport.\n"
        "- Testbetalingen (Mollie testmodus) zijn niet opgenomen.\n"
    )
    filename = f"fajaede-eu-verkopen-{datetime.fromtimestamp(until, LOCAL_TZ):%Y-%m-%d}.csv"
    send_email(
        f"fajaede.eu - Weekly Sales Report ({period})",
        recipient,
        body,
        attachments=[(filename, build_csv(rows), "text/csv")],
    )
    return len(rows)


def _runs_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS accounting_report_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start INTEGER NOT NULL,
            period_end INTEGER NOT NULL,
            status TEXT NOT NULL,
            sales INTEGER,
            created_at INTEGER NOT NULL
        )"""
    )


def _this_weeks_thursday(now: int) -> int | None:
    """Unix time of Thursday 00:00 (Brussels) of the current week, or None before Thursday."""
    local = datetime.fromtimestamp(now, LOCAL_TZ)
    if local.weekday() < 3:  # Monday-Wednesday
        return None
    thursday = (local - timedelta(days=local.weekday() - 3)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(thursday.timestamp())


def maybe_send_weekly_report(now: int | None = None) -> str:
    """Send the weekly report once per week, from Thursday on (Brussels time).

    Safe to call often and from several workers: a run is claimed in the database first.
    """
    now = int(now or time.time())
    recipient = accountant_email()
    if not recipient:
        return "no recipient"
    thursday = _this_weeks_thursday(now)
    if thursday is None:
        return "not thursday yet"

    conn = developer_api.get_db()
    try:
        _runs_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        last = conn.execute(
            "SELECT period_end, created_at FROM accounting_report_runs WHERE status IN ('sent', 'sending') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last and last["created_at"] >= thursday:  # this week's report is already out
            conn.rollback()
            return "already sent"
        since = last["period_end"] if last else now - WEEK  # no gaps between two reports
        run_id = conn.execute(
            "INSERT INTO accounting_report_runs (period_start, period_end, status, created_at) "
            "VALUES (?, ?, 'sending', ?)",
            (since, now, now),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()

    try:
        sales = send_report(since, now, recipient)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Weekly accounting report failed; will retry")
        conn = developer_api.get_db()
        try:
            conn.execute("DELETE FROM accounting_report_runs WHERE id = ?", (run_id,))
            conn.commit()
        finally:
            conn.close()
        return "failed"

    conn = developer_api.get_db()
    try:
        conn.execute("UPDATE accounting_report_runs SET status = 'sent', sales = ? WHERE id = ?", (sales, run_id))
        conn.commit()
    finally:
        conn.close()
    return f"sent ({sales} sales)"
