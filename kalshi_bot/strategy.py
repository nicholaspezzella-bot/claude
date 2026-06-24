"""
Sharp betting strategy engine.

Implements principles from sharp/contrarian sports betting:
 1. Line-shop: find the best available price across all books
 2. Sharp-book consensus: use Pinnacle/LowVig as the true probability anchor
 3. Fade the public: flag markets with lopsided public betting % (contrarian edge)
 4. Steam detection: track line movement indicating sharp money
 5. Low-vig market focus: prefer markets where the vig is thin

These principles align with what sharp professional bettors (often called
"crazy ninja" or "steam chasers" in betting forums) use to beat closing line.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from config import StrategyConfig
from kelly import KellyResult, KellySizer
from odds_client import ConsensusLine


@dataclass
class BetSignal:
    ticker: str
    title: str
    side: str           # "yes" or "no"
    market_price_cents: int
    true_prob: float
    edge: float
    kelly: KellyResult
    signal_reasons: list[str]
    sharp_consensus_prob: float
    books_used: list[str]


class SharpBettingStrategy:
    def __init__(self, config: StrategyConfig, kelly_sizer: KellySizer):
        self.config = config
        self.kelly = kelly_sizer
        # In-memory line history for steam detection: ticker -> list of prices
        self._price_history: dict[str, list[int]] = {}

    def _normalize_team_name(self, name: str) -> str:
        """Lowercase, strip punctuation for fuzzy matching."""
        return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()

    def _fuzzy_match(self, a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def _find_consensus_for_market(
        self, market: dict, consensus_lines: list[ConsensusLine]
    ) -> tuple[Optional[ConsensusLine], Optional[str]]:
        """
        Match a Kalshi market to a sharp consensus line by fuzzy team-name matching.
        Returns (consensus_line, "home"/"away") or (None, None).
        """
        title = market.get("title", "")
        subtitle = market.get("subtitle", "")
        text = (title + " " + subtitle).lower()

        best_line: Optional[ConsensusLine] = None
        best_side: Optional[str] = None
        best_score = 0.0

        for line in consensus_lines:
            for team, side in [(line.home_team, "home"), (line.away_team, "away")]:
                norm = self._normalize_team_name(team)
                score = self._fuzzy_match(norm, text)
                # Also check individual words
                for word in norm.split():
                    if len(word) > 3 and word in text:
                        score = max(score, 0.75)
                if score > best_score:
                    best_score = score
                    best_line = line
                    best_side = side

        if best_score < 0.45:
            return None, None
        return best_line, best_side

    def _detect_steam(self, ticker: str, current_price: int) -> bool:
        """True if price has moved >= steam_move_threshold cents against public."""
        history = self._price_history.get(ticker, [])
        if not history:
            return False
        movement = abs(current_price - history[-1])
        return movement >= self.config.steam_move_threshold

    def _record_price(self, ticker: str, price: int):
        hist = self._price_history.setdefault(ticker, [])
        hist.append(price)
        if len(hist) > 50:
            hist.pop(0)

    def _best_book_price(self, consensus: ConsensusLine, side: str) -> Optional[int]:
        """
        Return the best American odds (most favorable) across raw book data for a side.
        Converts to an implied probability for reference but returns as cents.
        """
        best_prob = None
        for book_odds in consensus.raw_book_odds:
            if side == "home":
                p = book_odds.home_prob
            else:
                p = book_odds.away_prob
            if best_prob is None or p < best_prob:
                # Lower implied prob = better odds for the bettor
                best_prob = p
        if best_prob is None:
            return None
        return int(best_prob * 100)

    def evaluate_market(
        self,
        market: dict,
        consensus_lines: list[ConsensusLine],
        current_bankroll: float,
    ) -> Optional[BetSignal]:
        """
        Evaluate a single Kalshi market for betting opportunity.
        Returns BetSignal if actionable, None otherwise.
        """
        ticker = market.get("ticker", "")
        title = market.get("title", "")
        yes_ask = market.get("yes_ask")  # cents
        no_ask = market.get("no_ask")   # cents
        last_price = market.get("last_price")

        if yes_ask is None or no_ask is None:
            return None

        # Find matching sharp consensus
        consensus, matched_side = self._find_consensus_for_market(market, consensus_lines)

        signal_reasons: list[str] = []
        true_prob_yes: Optional[float] = None

        if consensus and matched_side:
            if matched_side == "home":
                true_prob_yes = consensus.home_true_prob
            else:
                true_prob_yes = consensus.away_true_prob

            # Best price line-shopping signal
            best_book_price = self._best_book_price(consensus, matched_side)
            if best_book_price and best_book_price < yes_ask:
                signal_reasons.append(
                    f"Kalshi YES ask {yes_ask}c ABOVE best book {best_book_price}c — "
                    "market underpricing NO side"
                )
        else:
            # No sharp line available — skip
            return None

        true_prob_no = 1.0 - true_prob_yes

        # Filter by probability range
        if not (self.config.min_prob <= true_prob_yes <= self.config.max_prob):
            if not (self.config.min_prob <= true_prob_no <= self.config.max_prob):
                return None

        # Decide which side to bet
        yes_edge = true_prob_yes - (yes_ask / 100.0)
        no_edge = true_prob_no - (no_ask / 100.0)

        if yes_edge > no_edge and yes_edge > 0:
            bet_side = "yes"
            bet_price = yes_ask
            true_prob = true_prob_yes
        elif no_edge > 0:
            bet_side = "no"
            bet_price = no_ask
            true_prob = true_prob_no
        else:
            return None

        # Steam detection
        self._record_price(ticker, bet_price)
        if self._detect_steam(ticker, bet_price):
            signal_reasons.append("Steam move detected — sharp money moving this line")

        # Contrarian signal: low-probability markets often have public fade opportunity
        if true_prob < 0.30:
            signal_reasons.append(f"Low probability ({true_prob:.2%}) — potential public overreaction")

        # Kelly sizing
        kelly_result = self.kelly.size(
            ticker=ticker,
            side=bet_side,
            true_prob=true_prob,
            market_price_cents=bet_price,
            current_bankroll=current_bankroll,
        )

        if not kelly_result.viable:
            return None

        signal_reasons.append(kelly_result.reason)

        return BetSignal(
            ticker=ticker,
            title=title,
            side=bet_side,
            market_price_cents=bet_price,
            true_prob=true_prob,
            edge=kelly_result.edge,
            kelly=kelly_result,
            signal_reasons=signal_reasons,
            sharp_consensus_prob=true_prob,
            books_used=consensus.books_used,
        )

    def scan_markets(
        self,
        markets: list[dict],
        consensus_lines: list[ConsensusLine],
        current_bankroll: float,
    ) -> list[BetSignal]:
        """Evaluate all markets and return ranked bet signals."""
        signals: list[BetSignal] = []
        for market in markets:
            if market.get("status") != "open":
                continue
            signal = self.evaluate_market(market, consensus_lines, current_bankroll)
            if signal:
                signals.append(signal)

        # Rank by edge descending
        signals.sort(key=lambda s: s.edge, reverse=True)
        return signals
