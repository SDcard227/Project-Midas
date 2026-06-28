"""
signal_ledger.py — the memory + corroboration + learning layer.

news_intelligence.py judges ONE headline at a time. This module is the system
AROUND it: it remembers what Midas has heard, groups mentions into events, and
grows a confidence score as independent sources corroborate the same thing over
time. That is how a "whisper" (one faint mention) becomes a "signal" (many
independent sources saying it) — Midas catching ripples before they become waves.

It also learns: record_outcome() logs what the price actually did after an event,
and the ledger reweights sources and event-types by their realized hit rate, so
confidence reflects what has actually worked, not just raw volume.

The escalation ladder (a raw leak corroborates UPWARD into a hauler):
    leak      1 source             rawest micro-mention / possible leak
    whisper   2 sources            someone else independently noticed
    swell     3-4 sources          corroborating, rising — the edge zone
    hauler    5+ sources           confirmed, broad, big enough to act on
    stale     no new mentions      went quiet, confidence decays off

State persists to JSON (signal_ledger.json) to match the project's current
storage. This is the INTERIM home — the real one is Postgres, because growing,
time-decaying, multi-source confidence will outgrow a single file fast.

Honesty notes (do not oversell):
  - "Independent source" here = distinct source name. True syndication/echo
    detection (50 sites reprinting one AP wire are NOT 50 sources) is a deeper
    problem — see roadmap. Until then, raw source-count over-counts echoes.
  - Event clustering is coarse (ticker + event_type + day). Finer same-story
    clustering needs embeddings. Roadmap.
  - "Learning" needs many real outcomes to be statistically meaningful, and a
    source that worked last month may not next month. Start simple; trust it slowly.
"""
import os
import json
import math
import logging
from datetime import datetime, timezone

log = logging.getLogger("Midas.Ledger")

_STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "signal_ledger.json")

# Confidence saturates with corroboration: each independent, reliable source adds
# diminishing lift. k sets how fast. ~3-4 good sources should feel "forming".
_SATURATION_K = 0.55
# Confidence half-life in hours — a signal with no new mentions decays.
_DECAY_HALFLIFE_H = 12.0
# Beta-smoothing priors for learned reliability (start each source at ~0.5).
_PRIOR_HITS, _PRIOR_MISSES = 1.0, 1.0


def _now():
    return datetime.now(timezone.utc)


def _parse(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return _now()


class SignalLedger:
    def __init__(self, path: str = _STORE):
        self.path = path
        self.state = self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        data = {"events": {}, "source_rep": {}, "event_rep": {}, "crowd_rep": {}}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data.update(json.load(f))
            except Exception as e:
                log.warning(f"Ledger load failed, starting fresh: {e}")
        for k in ("events", "source_rep", "event_rep", "crowd_rep"):
            data.setdefault(k, {})   # migrate older saves
        return data

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            log.error(f"Ledger save failed: {e}")

    # ── reliability (learned weights) ────────────────────────────────────────
    def _reliability(self, table: str, key: str) -> float:
        rec = self.state.get(table, {}).get(key, {})
        hits = rec.get("hits", 0) + _PRIOR_HITS
        misses = rec.get("misses", 0) + _PRIOR_MISSES
        return hits / (hits + misses)   # 0..1, starts at 0.5

    def source_trust(self, source: str) -> float:
        """Blended 0..1 trust in a source, from two evidence streams:
          outcomes — how often this source was RIGHT (record_outcome)
          crowd    — how reliable PEOPLE rate it (record_rating)
        Outcomes lead, the crowd nudges. Both start neutral (0.5)."""
        outcome = self._reliability("source_rep", source)
        crowd = self._reliability("crowd_rep", source)
        return round(0.65 * outcome + 0.35 * crowd, 4)

    def record_rating(self, source: str, helpful: bool):
        """Crowd review: a user marks a source reliable (or not). This is the
        personalized, human reliability signal feeding back into confidence."""
        rec = self.state.setdefault("crowd_rep", {}).setdefault(
            source, {"hits": 0, "misses": 0})
        rec["hits" if helpful else "misses"] += 1
        self._save()

    # ── ingest ───────────────────────────────────────────────────────────────
    def ingest(self, ticker: str, verdict: dict, source: str, ts: str = None,
               title: str = None, link: str = None):
        """
        Fold one classified mention into the ledger.

        verdict — a dict from news_intelligence.classify_headlines (one item).
        source  — where it came from (e.g. "Reddit", "SEC EDGAR", "Bloomberg").
        ts      — ISO timestamp; defaults to now.
        title / link — the article itself, stored so the UI can show a clickable
                       source feed and users can check every source themselves.

        Only market-moving mentions are tracked — the rest is noise by design.
        Returns the updated event dict, or None if the mention was ignored.
        """
        if not verdict or not verdict.get("market_moving"):
            return None

        ts = ts or _now().isoformat()
        ticker = (ticker or (verdict.get("tickers") or ["?"])[0]).upper()
        event_type = verdict.get("event_type", "other")
        day = _parse(ts).strftime("%Y-%m-%d")
        eid = f"{ticker}:{event_type}:{day}"

        ev = self.state["events"].get(eid)
        if ev is None:
            ev = {
                "id": eid, "ticker": ticker, "event_type": event_type,
                "first_seen": ts, "last_seen": ts,
                "sources": [], "mentions": 0, "mentions_log": [],
                "bull": 0, "bear": 0, "breaking": 0,
                "outcome": None,
            }
            self.state["events"][eid] = ev

        ev["last_seen"] = ts
        ev["mentions"] += 1
        if source and source not in ev["sources"]:
            ev["sources"].append(source)       # distinct-source corroboration

        # Keep the article so the user-facing feed can link straight to it.
        ev.setdefault("mentions_log", []).append({
            "source": source, "title": title or "", "link": link or "",
            "sentiment": verdict.get("sentiment", "neutral"), "ts": ts,
        })
        ev["mentions_log"] = ev["mentions_log"][-40:]   # cap growth

        if verdict.get("sentiment") == "bullish":
            ev["bull"] += verdict.get("confidence", 0)
        elif verdict.get("sentiment") == "bearish":
            ev["bear"] += verdict.get("confidence", 0)
        if verdict.get("novelty") == "breaking":
            ev["breaking"] += 1

        self._save()
        return ev

    # ── confidence ───────────────────────────────────────────────────────────
    def confidence(self, ev: dict) -> float:
        """
        0-100 confidence for an event, from:
          corroboration  — distinct sources, each weighted by learned reliability
          event quality  — learned reliability of this event_type
          recency        — decays since last mention (half-life _DECAY_HALFLIFE_H)
        Saturating, so the first few independent sources matter most.
        """
        weighted = sum(self.source_trust(s) * 2.0 for s in ev["sources"])
        corroboration = 1.0 - math.exp(-_SATURATION_K * weighted)   # 0..1

        ev_rel = self._reliability("event_rep", ev["event_type"])   # 0..1, ~0.5 base
        quality = 0.5 + ev_rel                                       # 0.5..1.5 nudge

        hours = (_now() - _parse(ev["last_seen"])).total_seconds() / 3600.0
        recency = 0.5 ** (hours / _DECAY_HALFLIFE_H)                 # 1.0 -> 0 over time

        return round(min(100.0, corroboration * quality * recency * 100.0), 1)

    def stage(self, ev: dict) -> str:
        """The escalation ladder: leak -> whisper -> swell -> hauler (or stale)."""
        n = len(ev["sources"])
        hours = (_now() - _parse(ev["last_seen"])).total_seconds() / 3600.0
        if hours > _DECAY_HALFLIFE_H * 2:
            return "stale"
        if n >= 5:
            return "hauler"
        if n >= 3:
            return "swell"
        if n >= 2:
            return "whisper"
        return "leak"

    def direction(self, ev: dict) -> str:
        if ev["bull"] > ev["bear"]:
            return "bullish"
        if ev["bear"] > ev["bull"]:
            return "bearish"
        return "neutral"

    # ── queries ──────────────────────────────────────────────────────────────
    def whispers(self, min_conf: float = 15.0):
        """
        The edge zone: leaks, whispers and swells — RISING but not yet a hauler.
        Few sources, recent, already corroborating. This is where 'before the
        outlets' lives. Sorted by confidence.
        """
        out = []
        for ev in self.state["events"].values():
            stg = self.stage(ev)
            if stg in ("leak", "whisper", "swell"):
                c = self.confidence(ev)
                if c >= min_conf:
                    out.append({**ev, "confidence": c, "stage": stg,
                                "direction": self.direction(ev),
                                "source_count": len(ev["sources"])})
        return sorted(out, key=lambda e: e["confidence"], reverse=True)

    def haulers(self):
        """The top of the ladder: broadly corroborated, high-confidence signals
        that have hauled their way up from a leak. Sorted by confidence."""
        out = []
        for ev in self.state["events"].values():
            if self.stage(ev) == "hauler":
                out.append({**ev, "confidence": self.confidence(ev), "stage": "hauler",
                            "direction": self.direction(ev),
                            "source_count": len(ev["sources"])})
        return sorted(out, key=lambda e: e["confidence"], reverse=True)

    def top_signals(self, limit: int = 10):
        out = [{**ev, "confidence": self.confidence(ev), "stage": self.stage(ev),
                "direction": self.direction(ev), "source_count": len(ev["sources"])}
               for ev in self.state["events"].values()]
        return sorted(out, key=lambda e: e["confidence"], reverse=True)[:limit]

    def ticker_feed(self, ticker: str) -> dict:
        """Everything Midas has heard about one ticker, for the user-facing view:
        an overall confidence + direction, plus each event's clickable source feed.
        Powers 'search a ticker -> see confidence -> click -> check the sources'."""
        tk = (ticker or "").upper()
        events = [ev for ev in self.state["events"].values() if ev.get("ticker") == tk]
        if not events:
            return {"ticker": tk, "confidence": 0.0, "direction": "neutral",
                    "stage": "none", "events": []}

        enriched = []
        for ev in sorted(events, key=lambda e: self.confidence(e), reverse=True):
            enriched.append({
                "event_type": ev["event_type"],
                "stage": self.stage(ev),
                "confidence": self.confidence(ev),
                "direction": self.direction(ev),
                "source_count": len(ev["sources"]),
                "first_seen": ev["first_seen"],
                "last_seen": ev["last_seen"],
                # Newest article first, each with its clickable link + a live
                # source-trust score so users see how reliable each source is.
                "articles": [{**m, "source_trust": self.source_trust(m.get("source", ""))}
                             for m in reversed(ev.get("mentions_log", []))],
            })
        top = enriched[0]
        return {"ticker": tk, "confidence": top["confidence"],
                "direction": top["direction"], "stage": top["stage"],
                "events": enriched}

    # ── learning ─────────────────────────────────────────────────────────────
    def scoreable(self, min_age_hours: float = 24.0, limit: int = 30):
        """Past predictions old enough to judge and not yet scored.
        Returns [(event_id, ticker, first_seen)] — feed each a real move to learn."""
        from datetime import timedelta
        cutoff = _now() - timedelta(hours=min_age_hours)
        out = []
        for ev in self.state["events"].values():
            if ev.get("outcome") or self.direction(ev) == "neutral":
                continue
            try:
                if _parse(ev["first_seen"]) <= cutoff:
                    out.append((ev["id"], ev["ticker"], ev["first_seen"]))
            except Exception:
                continue
        return out[:limit]

    def record_outcome(self, event_id: str, move_pct: float, threshold: float = 1.0):
        """
        Teach the ledger. After an event, log the realized price move (%). If the
        move agreed with the event's direction and was big enough, every source
        and the event_type get a 'hit'; otherwise a 'miss'. Future confidence then
        leans on sources/types that have actually predicted moves.
        """
        ev = self.state["events"].get(event_id)
        if ev is None:
            log.warning(f"record_outcome: unknown event {event_id}")
            return

        direction = self.direction(ev)
        predicted_up = direction == "bullish"
        actual_up = move_pct > 0
        correct = (abs(move_pct) >= threshold) and (predicted_up == actual_up) \
            and direction != "neutral"
        field = "hits" if correct else "misses"

        for s in ev["sources"]:
            rec = self.state["source_rep"].setdefault(s, {"hits": 0, "misses": 0})
            rec[field] += 1
        rec = self.state["event_rep"].setdefault(ev["event_type"], {"hits": 0, "misses": 0})
        rec[field] += 1

        ev["outcome"] = {"move_pct": move_pct, "correct": correct}
        self._save()
        log.info(f"Learned from {event_id}: move {move_pct:+.2f}% -> "
                 f"{'HIT' if correct else 'miss'}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    led = SignalLedger(path=os.path.join(os.path.dirname(__file__), "_ledger_demo.json"))

    # A leak that climbs the ladder as independent sources corroborate it.
    v = {"sentiment": "bullish", "confidence": 80, "market_moving": True,
         "novelty": "breaking", "tickers": ["NVDA"], "event_type": "product",
         "rationale": "new chip leak"}
    for src in ["LeakForum", "NicheBlog", "Reddit", "RegionalPaper", "SEC EDGAR"]:
        ev = led.ingest("NVDA", v, source=src)
        print(f"after {src:13s} -> conf {led.confidence(ev):5.1f}  stage {led.stage(ev)}")

    print("\nEdge zone (leaks / whispers / swells):")
    for w in led.whispers():
        print(f"  {w['ticker']:5s} {w['direction']:8s} conf {w['confidence']:5.1f} "
              f"{w['source_count']} sources [{w['stage']}]")
    print("Haulers (confirmed):")
    for h in led.haulers():
        print(f"  {h['ticker']:5s} {h['direction']:8s} conf {h['confidence']:5.1f} "
              f"{h['source_count']} sources [{h['stage']}]")
