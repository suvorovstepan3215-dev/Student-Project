"""Optional CLI email notifications; never called from the public web UI/MCP."""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger("archiv_tracker.notifier")


def send_email(subject: str, body: str, email_cfg: dict) -> bool:
    try:
        env_name = email_cfg.get("password_env", "SMTP_PASSWORD")
        password = os.environ.get(env_name, "")
        # Backwards compatibility for private local configurations only.
        password = password or email_cfg.get("password", "")
        if not password or not all(
            email_cfg.get(key) for key in ("smtp_server", "username", "from_address", "to_address")
        ):
            raise ValueError("SMTP settings incomplete")
        message = EmailMessage()
        message["From"] = email_cfg["from_address"]
        message["To"] = email_cfg["to_address"]
        message["Subject"] = subject  # email library rejects CR/LF header injection.
        message.set_content(body)
        with smtplib.SMTP(
            email_cfg["smtp_server"], int(email_cfg.get("smtp_port", 587)), timeout=20
        ) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(email_cfg["username"], password)
            server.send_message(message)
        logger.info("Письмо отправлено.")
        return True
    except Exception as exc:
        # SMTP error bodies can echo credentials/recipient; don't log them.
        logger.warning("Письмо не отправлено (%s).", type(exc).__name__)
        return False
