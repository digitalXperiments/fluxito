"""
Email service for transactional emails.

Supports multiple backends:
  - **SMTP** — standard email via any SMTP provider (SES, SendGrid, etc.)
  - **Console** — logs emails to stdout (development default)

All email sending is async and fire-and-forget to avoid blocking request
handlers. Failed sends are logged but never raise.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import app.app_state as app_state
from app.config import settings
from app.settings_service import get_runtime_setting

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


async def _email_config() -> dict:
    """Return DB-backed email settings with env/default fallback."""
    fallback = {
        "host": getattr(settings, "SMTP_HOST", ""),
        "port": int(getattr(settings, "SMTP_PORT", 587)),
        "username": getattr(settings, "SMTP_USERNAME", ""),
        "password": getattr(settings, "SMTP_PASSWORD", ""),
        "from_email": getattr(settings, "SMTP_FROM_EMAIL", "noreply@example.com"),
        "from_name": getattr(settings, "SMTP_FROM_NAME", ""),
    }
    session_factory = getattr(app_state, "db_session_factory", None)
    if session_factory is None:
        return fallback

    try:
        async with session_factory() as db:
            return {
                "host": await get_runtime_setting(db, "smtp_host"),
                "port": await get_runtime_setting(db, "smtp_port"),
                "username": await get_runtime_setting(db, "smtp_username"),
                "password": await get_runtime_setting(db, "smtp_password"),
                "from_email": await get_runtime_setting(db, "smtp_from_email"),
                "from_name": await get_runtime_setting(db, "smtp_from_name"),
            }
    except Exception:
        logger.exception("Failed to load DB email settings; using env/default fallback")
        return fallback


# ---------------------------------------------------------------------------
# Low-level send
# ---------------------------------------------------------------------------


async def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
):
    """
    Send an email. Uses SMTP if configured, otherwise logs to console.
    """
    from app.branding import brand as _brand
    cfg = await _email_config()
    from_email = cfg["from_email"] or "noreply@example.com"
    from_name = cfg["from_name"] or _brand()["name"]

    if not (cfg["host"] and from_email):
        logger.info(f"[EMAIL-DEV] To: {to_email} | Subject: {subject}\n{text_body or html_body[:500]}")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        host = cfg["host"]
        port = int(cfg["port"] or 587)
        user = cfg["username"] or ""
        password = cfg["password"] or ""

        with smtplib.SMTP(host, port) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(from_email, [to_email], msg.as_string())

        logger.info(f"Email sent to {to_email}: {subject}")
    except Exception:
        logger.exception(f"Failed to send email to {to_email}")


# ---------------------------------------------------------------------------
# Project invite email
# ---------------------------------------------------------------------------


async def send_project_invite_email(
    to_email: str,
    project_name: str,
    project_slug: str,
    inviter_email: str,
    role: str,
):
    """Send an invitation email for a project."""
    from app.branding import brand as _brand
    brand_name = _brand()["name"]

    base_url = getattr(settings, "APP_BASE_URL", "https://fluxito.ai")
    invite_url = f"{base_url}/project/{project_slug}"

    subject = f"You've been invited to {project_name} on {brand_name}"

    html_body = f"""
    <div style="font-family: Inter, -apple-system, system-ui, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 0;">
      <div style="text-align: center; margin-bottom: 32px;">
        <span style="font-family: 'JetBrains Mono', monospace; font-size: 20px; font-weight: 700; color: #1c1917;">
          [ <span style="color: #b47800;">{brand_name}</span> ]
        </span>
      </div>

      <div style="background: #fffefa; border: 1px solid #e4e2dc; border-radius: 12px; padding: 32px;">
        <h2 style="font-size: 20px; font-weight: 700; margin: 0 0 8px; color: #1c1917;">
          You're invited to {project_name}
        </h2>
        <p style="font-size: 15px; color: #57534e; margin: 0 0 24px; line-height: 1.6;">
          <strong>{inviter_email}</strong> has invited you to join the project
          <strong>{project_name}</strong> as a <strong>{role}</strong>.
        </p>

        <div style="text-align: center; margin: 24px 0;">
          <a href="{invite_url}"
             style="display: inline-block; background: #b47800; color: #fff; font-size: 15px;
                    font-weight: 600; padding: 12px 32px; border-radius: 8px;
                    text-decoration: none;">
            Open Project
          </a>
        </div>

        <p style="font-size: 13px; color: #a8a29e; margin: 24px 0 0; line-height: 1.5;">
          If you don't have a {brand_name} account, one will be created when you sign in
          with this email address ({to_email}).
        </p>
      </div>

      <p style="font-size: 12px; color: #a8a29e; text-align: center; margin-top: 24px;">
        {brand_name} — Marketing analytics for any AI
      </p>
    </div>
    """

    text_body = (
        f"{inviter_email} has invited you to join '{project_name}' on {brand_name} as a {role}.\n\n"
        f"Open the project: {invite_url}\n\n"
        f"If you don't have a {brand_name} account, one will be created when you sign in."
    )

    await send_email(to_email, subject, html_body, text_body)
