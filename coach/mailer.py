"""Outgoing email (password reset). Works with any SMTP server, including AWS SES SMTP.

Without SMTP_HOST configured (local development), emails are written to the server log instead.
"""
import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage

from coach.config import SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_STARTTLS, SMTP_USER

log = logging.getLogger("mailer")


def _send(msg: EmailMessage) -> None:
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=20)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
            if SMTP_STARTTLS:
                server.starttls(context=ssl.create_default_context())
        with server:
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        log.info("Sent email to %s: %s", msg["To"], msg["Subject"])
    except Exception:
        log.exception("Could not send email to %s", msg["To"])


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    if not SMTP_HOST:
        log.warning("SMTP is not configured; email to %s not sent. Subject: %s\n%s", to, subject, text)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_FROM, to, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    threading.Thread(target=_send, args=(msg,), daemon=True).start()
