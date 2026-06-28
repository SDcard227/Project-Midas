"""
MIDAS — billing: Stripe subscriptions.

Graceful when unconfigured: with no STRIPE_SECRET_KEY the plans are display-only,
so the app runs fine without Stripe (same pattern as the AI key). When the key +
price IDs are set, Checkout + the webhook go live and flip a user's tier.

We sell SOFTWARE (a flat subscription), never a cut of trades or profits — that
keeps Midas out of broker-dealer / investment-adviser registration.

Env it reads:
  STRIPE_SECRET_KEY        sk_test_... / sk_live_...
  STRIPE_PUBLISHABLE_KEY   pk_... (for the client, optional)
  STRIPE_WEBHOOK_SECRET    whsec_... (verifies webhook authenticity)
  STRIPE_PRICE_PRO         price_... (the $29/mo recurring price)
  STRIPE_PRICE_PREMIUM     price_... (the $79/mo recurring price)
"""
import os
import json


def _prices():
    return {
        "pro":     os.getenv("STRIPE_PRICE_PRO", ""),
        "premium": os.getenv("STRIPE_PRICE_PREMIUM", ""),
    }


def is_configured():
    return bool(os.getenv("STRIPE_SECRET_KEY"))


def status():
    p = _prices()
    return {
        "configured": is_configured(),
        "prices": {k: bool(v) for k, v in p.items()},
        "publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
    }


def _client():
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    return stripe


def create_checkout(user, tier, base_url):
    """Create a Stripe Checkout subscription session. Returns {'url':..} or {'error':..}."""
    tier = (tier or "").lower()
    if not is_configured():
        return {"error": "Billing is not configured yet."}
    price = _prices().get(tier)
    if not price:
        return {"error": f"No Stripe price set for the {tier} plan."}
    base = base_url.rstrip("/")
    try:
        stripe = _client()
        sess = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price, "quantity": 1}],
            client_reference_id=str(user["id"]),
            customer_email=user.get("email"),
            success_url=base + "/account.html?upgraded=1",
            cancel_url=base + "/account.html?canceled=1",
            metadata={"tier": tier, "uid": str(user["id"])},
        )
        return {"url": sess.url}
    except Exception as e:
        return {"error": str(e)}


def _tier_for_price(price_id):
    for t, p in _prices().items():
        if p and p == price_id:
            return t
    return None


def handle_webhook(payload, sig_header):
    """Verify + interpret a Stripe webhook. Returns an action dict the server
    applies: set_tier (by uid), set_tier_customer, downgrade, ignore, or error."""
    if not is_configured():
        return {"action": "ignore", "error": "not configured"}
    stripe = _client()
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    try:
        if secret:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
        else:
            event = json.loads(payload)
    except Exception as e:
        return {"action": "error", "error": str(e)}

    et  = event.get("type")
    obj = (event.get("data") or {}).get("object", {})

    if et == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        return {"action": "set_tier",
                "uid": obj.get("client_reference_id") or meta.get("uid"),
                "tier": meta.get("tier", "pro"),
                "customer": obj.get("customer")}

    if et == "customer.subscription.deleted":
        return {"action": "downgrade", "customer": obj.get("customer")}

    if et == "customer.subscription.updated":
        st = obj.get("status")
        if st in ("canceled", "unpaid", "past_due", "incomplete_expired"):
            return {"action": "downgrade", "customer": obj.get("customer")}
        items = (obj.get("items") or {}).get("data", [])
        price_id = items[0].get("price", {}).get("id") if items else None
        tier = _tier_for_price(price_id) if price_id else None
        if tier:
            return {"action": "set_tier_customer",
                    "customer": obj.get("customer"), "tier": tier}

    return {"action": "ignore", "type": et}
