"""Shared SMTP helpers for transactional email."""

import os
import smtplib
import ssl
from email.message import EmailMessage


def send_email(
    subject: str,
    recipient: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """Send a plain-text email using the configured SMTP account.

    attachments: optional list of (filename, content, mime type such as "text/csv").
    """
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD") or os.getenv("SMTP_PASS", "")
    if not host or not sender or not username or not password:
        raise RuntimeError(
            "SMTP_HOST, SMTP_FROM, SMTP_USER and SMTP_PASSWORD are required."
        )

    port = int(os.getenv("SMTP_PORT", "587"))
    if not 1 <= port <= 65535:
        raise RuntimeError("SMTP_PORT is invalid.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    for filename, content, mime_type in attachments or []:
        maintype, _, subtype = mime_type.partition("/")
        message.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10, context=context) as server:
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(username, password)
            server.send_message(message)
