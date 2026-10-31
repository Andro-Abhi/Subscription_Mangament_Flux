import smtplib
from email.message import EmailMessage

from ..config import settings


def send_email(to: str, subject: str, body: str) -> None:
    """Sends via SMTP if configured; otherwise prints (handy for hackathon demos)."""
    if not settings.SMTP_HOST:
        print(f"\n--- EMAIL to {to} ---\nSubject: {subject}\n{body}\n--------------------\n")
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.SMTP_USER or "noreply@brightloop.test", to, subject
    msg.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as s:
        s.starttls()
        if settings.SMTP_USER:
            s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        s.send_message(msg)
