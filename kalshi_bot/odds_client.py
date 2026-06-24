"""
The Odds API client for fetching cross-book prices.
Computes vig-free (sharp) consensus probability from Pinnacle, LowVig, BetOnline.
"""

from dataclasses import dataclass
from typing import Optional

import requests

from config import OddsConfig


@dataclass
class BookOdds:
    book: str
    home_price: int      # American odds
    away_price: int
    home_prob: float     # raw implied prob (includes vig)
    away_prob: float


@dataclass
class ConsensusLine:
    sport_key: str
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    home_true_prob: float   # vig-removed sharp consensus
    away_true_prob: float
    books_used: list[str]
    raw_book_odds: list[BookOdds]


class OddsAPIError(Exception):
    pass


class OddsClient:
    def __init__(self, config: OddsConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers["x-api-key"] = config.api_key

    def _get(self, endpoint: str, params: dict = None) -> any:
        url = self.config.base_url + endpoint
        resp = self.session.get(url, params=params or {})
        if not resp.ok:
            raise OddsAPIError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def get_sports(self) -> list[dict]:
        return self._get("/sports")

    def get_active_sports(self) -> list[str]:
        sports = self.get_sports()
        return [s["key"] for s in sports if not s.get("has_outrights", False)]

    def get_odds(self, sport_key: str) -> list[dict]:
        """Fetch h2h moneyline odds for a sport from sharp books."""
        return self._get(
            f"/sports/{sport_key}/odds",
            params={
                "regions": self.config.regions,
                "markets": self.config.markets,
                "bookmakers": ",".join(self.config.sharp_books.keys()),
                "oddsFormat": "american",
            },
        )

    @staticmethod
    def american_to_prob(american: int) -> float:
        """Convert American odds to raw implied probability."""
        if american > 0:
            return 100 / (american + 100)
        return abs(american) / (abs(american) + 100)

    @staticmethod
    def remove_vig(prob_a: float, prob_b: float) -> tuple[float, float]:
        """Shin/normalization vig removal — renormalize to sum to 1.0."""
        total = prob_a + prob_b
        return prob_a / total, prob_b / total

    def build_consensus(self, event: dict) -> Optional[ConsensusLine]:
        """
        Compute weighted vig-free probability from sharp books.
        Weights: Pinnacle 0.55, LowVig 0.30, BetOnline 0.15.
        Falls back to equal weights if a book is missing.
        """
        weights = self.config.sharp_books
        home_team = event["home_team"]
        away_team = event["away_team"]

        weighted_home = 0.0
        weighted_away = 0.0
        total_weight = 0.0
        books_used: list[str] = []
        raw: list[BookOdds] = []

        for bm in event.get("bookmakers", []):
            book_key = bm["key"]
            if book_key not in weights:
                continue
            for market in bm.get("markets", []):
                if market["key"] != "h2h":
                    continue
                outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                home_price = outcomes.get(home_team)
                away_price = outcomes.get(away_team)
                if home_price is None or away_price is None:
                    continue

                raw_home = self.american_to_prob(home_price)
                raw_away = self.american_to_prob(away_price)
                vig_free_home, vig_free_away = self.remove_vig(raw_home, raw_away)

                w = weights[book_key]
                weighted_home += vig_free_home * w
                weighted_away += vig_free_away * w
                total_weight += w
                books_used.append(book_key)
                raw.append(BookOdds(book_key, home_price, away_price, raw_home, raw_away))
                break  # one h2h market per book

        if total_weight == 0:
            return None

        # Renormalize in case not all books had data
        home_true = weighted_home / total_weight
        away_true = weighted_away / total_weight
        norm = home_true + away_true
        home_true /= norm
        away_true /= norm

        return ConsensusLine(
            sport_key=event["sport_key"],
            event_id=event["id"],
            home_team=home_team,
            away_team=away_team,
            commence_time=event["commence_time"],
            home_true_prob=round(home_true, 4),
            away_true_prob=round(away_true, 4),
            books_used=books_used,
            raw_book_odds=raw,
        )

    def get_all_consensus_lines(self, sport_keys: list[str]) -> list[ConsensusLine]:
        lines: list[ConsensusLine] = []
        for sport_key in sport_keys:
            try:
                events = self.get_odds(sport_key)
            except OddsAPIError:
                continue
            for event in events:
                line = self.build_consensus(event)
                if line:
                    lines.append(line)
        return lines
