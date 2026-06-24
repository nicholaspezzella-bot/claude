import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class KalshiConfig:
    api_key_id: str = field(default_factory=lambda: os.environ["KALSHI_API_KEY_ID"])
    private_key_path: str = field(default_factory=lambda: os.environ["KALSHI_PRIVATE_KEY_PATH"])
    env: str = field(default_factory=lambda: os.getenv("KALSHI_ENV", "production"))

    @property
    def base_url(self) -> str:
        if self.env == "demo":
            return "https://demo-api.kalshi.co/trade-api/v2"
        return "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class OddsConfig:
    api_key: str = field(default_factory=lambda: os.environ["ODDS_API_KEY"])
    base_url: str = "https://api.the-odds-api.com/v4"
    # Sharp books weighted by closing line accuracy
    sharp_books: dict = field(default_factory=lambda: {
        "pinnacle": 0.55,
        "lowvig": 0.30,
        "betonline": 0.15,
    })
    regions: str = "us"
    markets: str = "h2h"


@dataclass
class BankrollConfig:
    bankroll: float = field(default_factory=lambda: float(os.getenv("BANKROLL", "10000.0")))
    kelly_fraction: float = field(default_factory=lambda: float(os.getenv("KELLY_FRACTION", "0.25")))
    max_position_pct: float = field(default_factory=lambda: float(os.getenv("MAX_POSITION_PCT", "0.03")))
    min_edge_threshold: float = field(default_factory=lambda: float(os.getenv("MIN_EDGE_THRESHOLD", "0.05")))


@dataclass
class StrategyConfig:
    # Min/max true probability to consider betting
    min_prob: float = field(default_factory=lambda: float(os.getenv("MIN_PROB_THRESHOLD", "0.05")))
    max_prob: float = field(default_factory=lambda: float(os.getenv("MAX_PROB_THRESHOLD", "0.60")))
    loop_interval: int = field(default_factory=lambda: int(os.getenv("LOOP_INTERVAL_SECONDS", "60")))
    dry_run: bool = field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")
    # Sharp money threshold: fade public when public % exceeds this on a side
    public_bet_fade_threshold: float = 0.70
    # Minimum line movement (cents) to flag as sharp steam
    steam_move_threshold: int = 3
    # Sports to scan on Kalshi
    kalshi_sports_tags: list = field(default_factory=lambda: [
        "sports", "basketball", "football", "baseball", "hockey", "soccer", "tennis"
    ])


@dataclass
class Config:
    kalshi: KalshiConfig = field(default_factory=KalshiConfig)
    odds: OddsConfig = field(default_factory=OddsConfig)
    bankroll: BankrollConfig = field(default_factory=BankrollConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
