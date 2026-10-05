from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage

_SPLIT = re.compile(r"[,;\n]+")


def email_recipients(cfg: dict | None = None) -> list[str]:
    """Addresses from delivery.email_to and the EMAIL_TO env var, de-duplicated."""
    raw: list[str] = []
    for entry in ((cfg or {}).get("delivery") or {}).get("email_to") or []:
        raw.append(str(entry))
    env_value = _env("EMAIL_TO")
    if env_value:
        raw.append(env_value)
    return _parse_addresses(raw)


def send_email(subject: str, html: str, plain: str, recipients: list[str]) -> None:
    user = _env("SMTP_USER")
    password = _env("SMTP_APP_PASSWORD")
    sender = _env("EMAIL_FROM") or user
    host = _env("SMTP_HOST") or "smtp.gmail.com"
    port = int(_env("SMTP_PORT") or "465")
    if not user or not password:
        raise RuntimeError("SMTP_USER and SMTP_APP_PASSWORD are required")
    if not recipients:
        raise RuntimeError("Add at least one address in EMAIL_TO or delivery.email_to")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(plain)
    message.add_alternative(html, subtype="html")

    if port == 465:
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        smtp = smtplib.SMTP(host, port, timeout=30)
    try:
        if port != 465:
            smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)
    finally:
        smtp.quit()


def _parse_addresses(values: list[str]) -> list[str]:
    found: set[str] = set()
    recipients: list[str] = []
    for value in values:
        for part in _SPLIT.split(value):
            address = part.strip()
            if not address:
                continue
            if "@" not in address or " " in address:
                raise RuntimeError(f"Not an email address: {address}")
            key = address.lower()
            if key in found:
                continue
            found.add(key)
            recipients.append(address)
    return recipients


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()
