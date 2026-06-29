"""
fact_checker.py — the peer-review layer.

Scraping "as much as possible" floods the system with rumors, hype, and outright
manipulation (pump-and-dump posts are DESIGNED to deceive). Corroboration alone
is not truth — 50 coordinated accounts can repeat one fake rumor and look like a
hauler. So before a signal is trusted, it goes through PEER REVIEW: a panel of
independent, skeptical reviewers whose job is to REFUTE it. A claim survives only
if the panel can't.

Three independent review lenses (each its own Claude call, so they can disagree):
  - grounding    — does this trace to a primary source (filing / official
                   release), or is it just chatter?
  - manipulation — does this look like coordinated hype / pump-and-dump?
  - staleness    — is this already public and priced in (no edge left)?

Aggregate verdict and a confidence multiplier the caller applies to the ledger:
  verified     — supported AND primary-source grounded            (x1.0)
  corroborated — supported but no primary source yet              (x0.7)
  unverified   — refuted / unsupported, treat as noise            (x0.4)
  suspect      — manipulation flagged, do NOT trade               (x0.2)

This runs only on the FEW signals that climb the ladder (swells / haulers), not
the whole firehose — so a stronger model is affordable here. The firehose filter
uses Haiku; peer review defaults to Opus, where judgment matters most. Override
with ANTHROPIC_REVIEW_MODEL.

Degrades gracefully: no SDK / no key -> status "unreviewed", multiplier 1.0, and
the caller just keeps corroboration-only confidence.
"""
import os
import json
import logging

log = logging.getLogger("Midas.FactCheck")

REVIEW_MODEL = os.getenv("ANTHROPIC_REVIEW_MODEL", "claude-haiku-4-5")   # cheap by default; set claude-opus-4-8 for premium fact-checks

try:
    import anthropic
    _SDK_OK = True
except ImportError:
    _SDK_OK = False


# Each lens is a distinct skeptical reviewer. They are told to default to doubt.
_LENSES = {
    "grounding": (
        "You verify SOURCING. Decide whether this claim traces to a primary "
        "source (an SEC filing, official company release, regulator, or named "
        "on-record report) versus anonymous chatter or speculation. Set "
        "primary_source=true ONLY if a primary source is clearly implied."
    ),
    "manipulation": (
        "You hunt MANIPULATION. Decide whether this looks like a pump-and-dump, "
        "coordinated hype, or astroturfing — e.g. hype with no substance, only "
        "anonymous social sources, urgency/FOMO language, or a thinly-traded "
        "ticker. Set manipulation_risk=true if it smells engineered."
    ),
    "staleness": (
        "You check NOVELTY. Decide whether this is genuinely new and tradable, "
        "or already widely public and priced in. Refute (verdict='refuted') if "
        "it is stale / old news with no remaining edge."
    ),
}

_SYSTEM = ("You are a skeptical financial fact-checker on a peer-review panel. "
           "Your default is doubt: refute the claim unless the evidence is solid. "
           "Judge only what you are given. {lens}")

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["verified", "refuted", "uncertain"]},
        "primary_source": {"type": "boolean"},
        "manipulation_risk": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "primary_source", "manipulation_risk", "reason"],
    "additionalProperties": False,
}


def _client():
    if not _SDK_OK:
        log.info("Fact-check disabled - `pip install anthropic` to enable.")
        return None
    if not os.getenv("ANTHROPIC_API_KEY"):
        log.info("Fact-check disabled - ANTHROPIC_API_KEY not set.")
        return None
    return anthropic.Anthropic()


def _review_one(client, lens_name: str, lens_prompt: str,
                claim: str, sources: list, model: str) -> dict:
    src = ", ".join(sources) if sources else "unknown"
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=600,
            system=_SYSTEM.format(lens=lens_prompt),
            messages=[{"role": "user", "content":
                       f"CLAIM: {claim}\nREPORTED BY: {src}\n\n"
                       "Review this claim through your lens and return your verdict."}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        v = json.loads(text)
        v["lens"] = lens_name
        return v
    except Exception as e:
        log.warning(f"Fact-check lens '{lens_name}' failed: {e}")
        return {"lens": lens_name, "verdict": "uncertain", "primary_source": False,
                "manipulation_risk": False, "reason": f"review error: {e}"}


def peer_review(claim: str, sources: list = None, model: str = None) -> dict:
    """
    Run the skeptic panel on one claim. Returns:
        {status, multiplier, primary_grounded, manipulation, reviews}

    status in {verified, corroborated, unverified, suspect, unreviewed}.
    multiplier is what the caller multiplies its corroboration confidence by.
    """
    client = _client()
    if client is None:
        return {"status": "unreviewed", "multiplier": 1.0,
                "primary_grounded": False, "manipulation": False, "reviews": []}

    model = model or REVIEW_MODEL
    reviews = [_review_one(client, name, prompt, claim, sources, model)
               for name, prompt in _LENSES.items()]

    refuted = sum(1 for r in reviews if r["verdict"] == "refuted")
    verified = sum(1 for r in reviews if r["verdict"] == "verified")
    manipulation = sum(1 for r in reviews if r["manipulation_risk"]) >= 2
    grounded = any(r["primary_source"] for r in reviews)

    if manipulation:
        status, mult = "suspect", 0.2
    elif refuted > verified:
        status, mult = "unverified", 0.4
    elif grounded:
        status, mult = "verified", 1.0
    else:
        status, mult = "corroborated", 0.7

    return {"status": status, "multiplier": mult,
            "primary_grounded": grounded, "manipulation": manipulation,
            "reviews": reviews}


def review_event(event: dict, model: str = None) -> dict:
    """Peer-review a ledger event (from signal_ledger). Builds a claim string
    from the event and reviews it against the sources that reported it."""
    claim = (f"{event.get('ticker', '?')} {event.get('direction', '')} "
             f"news ({event.get('event_type', 'event')}), reported by "
             f"{event.get('source_count', 0)} sources")
    return peer_review(claim, sources=event.get("sources"), model=model)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pprint import pprint
    # No key -> prints the graceful 'unreviewed' fallback. That's expected.
    pprint(peer_review("NVDA soaring on leaked next-gen chip benchmarks",
                       sources=["r/wallstreetbets", "AnonBlog"]))
