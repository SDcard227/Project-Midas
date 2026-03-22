import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

log = logging.getLogger("Mitas.Notifier")


def send_email(subject: str, body: str, to_email: str, from_email: str, password: str):
    """Send a plain-text email via Gmail SMTP."""
    try:
        msg = MIMEMultipart()
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_email, password)
            server.sendmail(from_email, to_email, msg.as_string())

        log.info(f"Notification sent to {to_email}: {subject}")
    except Exception as e:
        log.error(f"Failed to send notification email: {e}")


def notify_floor_hit(
    to_email: str,
    from_email: str,
    password: str,
    fund_value: float,
    floor_amount: float,
    positions_closed: list,
    total_recovered: float,
    starting_balance: float,
):
    """
    Alert the user that the floor was hit, Mitas has pulled everything out,
    and explain how to re-enter manually.
    """
    if not all([to_email, from_email, password]):
        log.warning("Floor hit but no notification credentials set — skipping email.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    loss = starting_balance - fund_value
    loss_pct = (loss / starting_balance) * 100

    positions_str = (
        "\n".join(f"  - {t}" for t in positions_closed)
        if positions_closed
        else "  (none)"
    )

    body = f"""
Mitas Floor Protection Alert
{timestamp}

Your fund has hit its floor and Mitas has automatically pulled out of all positions.

  Fund value at trigger : ${fund_value:.2f}
  Floor amount          : ${floor_amount:.2f}
  Starting balance      : ${starting_balance:.2f}
  Loss from peak        : -${loss:.2f} ({loss_pct:.1f}%)
  Capital recovered     : ${total_recovered:.2f}

Positions closed:
{positions_str}

---------------------------------------
Your money is now sitting in cash in your Alpaca account. Mitas has stopped trading.

TO RE-ENTER (at your own risk):
  1. Log into Alpaca and confirm your cash balance.
  2. Open main.py and update STARTING_BALANCE to your current balance.
  3. Set FLOOR_AMOUNT to whatever minimum you want to protect.
  4. Run: py main.py

Mitas will not restart on its own. This is intentional.
Re-entry is your choice.

---------------------------------------
This is an automated alert from Project Mitas.
""".strip()

    send_email(
        subject=f"[Mitas] Floor Hit — Trading Paused (Fund: ${fund_value:.2f})",
        body=body,
        to_email=to_email,
        from_email=from_email,
        password=password,
    )
