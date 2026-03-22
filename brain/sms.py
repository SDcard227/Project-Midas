import logging
import random
import time

log = logging.getLogger("Midas.SMS")


def send_sms(to: str, from_: str, body: str, account_sid: str, auth_token: str) -> bool:
    """
    Send an SMS via Twilio. Returns True on success, False on failure.
    """
    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        client.messages.create(to=to, from_=from_, body=body)
        log.info(f"SMS sent to {to}")
        return True
    except Exception as e:
        log.warning(f"SMS failed: {e}")
        return False


def notify_floor_hit_sms(
    to: str,
    from_: str,
    account_sid: str,
    auth_token: str,
    fund_value: float,
    floor_amount: float,
    positions_closed: list,
):
    """Send a floor-hit alert via SMS."""
    tickers = ", ".join(positions_closed) if positions_closed else "none"
    body = (
        f"MIDAS ALERT: Floor hit. Trading stopped.\n"
        f"Fund: ${fund_value:.2f} | Floor: ${floor_amount:.2f}\n"
        f"Positions closed: {tickers}\n"
        f"Log in to review and restart manually."
    )
    send_sms(to, from_, body, account_sid, auth_token)


def send_2fa_code(
    to: str,
    from_: str,
    account_sid: str,
    auth_token: str,
) -> str:
    """
    Generate a 6-digit 2FA code, send it via SMS, and return the code.
    """
    code = str(random.randint(100000, 999999))
    body = f"Midas 2FA code: {code}\nDo not share this code."
    success = send_sms(to, from_, body, account_sid, auth_token)
    if not success:
        raise RuntimeError("Failed to send 2FA code via SMS.")
    return code


def verify_2fa(
    to: str,
    from_: str,
    account_sid: str,
    auth_token: str,
    max_attempts: int = 3,
) -> bool:
    """
    Send a 2FA code to the user's phone and prompt them to enter it.
    Returns True if verified, False if all attempts fail.
    """
    print("\n  Sending 2FA code to your phone...")
    code = send_2fa_code(to, from_, account_sid, auth_token)
    print("  Code sent.\n")

    for attempt in range(1, max_attempts + 1):
        entered = input(f"  Enter 2FA code ({attempt}/{max_attempts}): ").strip()
        if entered == code:
            print("  Verified.\n")
            return True
        else:
            remaining = max_attempts - attempt
            if remaining > 0:
                print(f"  Incorrect. {remaining} attempt(s) remaining.")
            else:
                print("  Too many failed attempts.")

    return False
