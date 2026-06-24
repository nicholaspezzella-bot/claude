"""
Kelly Criterion bet sizing with fractional Kelly and hard bankroll cap.

Full Kelly formula: f* = (p * b - q) / b
  where p = true win probability, q = 1 - p, b = net odds (payout per unit staked)

On Kalshi, contracts pay $1 if YES resolves true, $0 if false.
  - Buying YES at price c cents: stake = c/100, win = (100-c)/100, b = (100-c)/c
  - Buying NO at price c cents: stake = c/100, win = (100-c)/100, b = (100-c)/c
    but the true prob for NO winning = 1 - p_yes
"""

from dataclasses import dataclass

from config import BankrollConfig


@dataclass
class KellyResult:
    ticker: str
    side: str           # "yes" or "no"
    true_prob: float    # estimated true probability of this side winning
    market_price_cents: int
    implied_prob: float
    edge: float         # true_prob - implied_prob
    full_kelly_fraction: float
    fractional_kelly: float
    recommended_dollars: float
    recommended_contracts: int
    bankroll: float
    viable: bool        # False if edge <= 0 or below threshold
    reason: str


class KellySizer:
    def __init__(self, config: BankrollConfig):
        self.config = config

    def size(
        self,
        ticker: str,
        side: str,
        true_prob: float,
        market_price_cents: int,
        current_bankroll: float | None = None,
    ) -> KellyResult:
        bankroll = current_bankroll if current_bankroll is not None else self.config.bankroll
        implied_prob = market_price_cents / 100.0
        edge = true_prob - implied_prob

        if edge <= 0:
            return KellyResult(
                ticker=ticker, side=side, true_prob=true_prob,
                market_price_cents=market_price_cents, implied_prob=implied_prob,
                edge=edge, full_kelly_fraction=0.0, fractional_kelly=0.0,
                recommended_dollars=0.0, recommended_contracts=0,
                bankroll=bankroll, viable=False, reason="No positive edge",
            )

        if edge < self.config.min_edge_threshold:
            return KellyResult(
                ticker=ticker, side=side, true_prob=true_prob,
                market_price_cents=market_price_cents, implied_prob=implied_prob,
                edge=edge, full_kelly_fraction=0.0, fractional_kelly=0.0,
                recommended_dollars=0.0, recommended_contracts=0,
                bankroll=bankroll, viable=False,
                reason=f"Edge {edge:.3f} below threshold {self.config.min_edge_threshold}",
            )

        # b = net payout per dollar staked (odds format)
        b = (100 - market_price_cents) / market_price_cents
        q = 1 - true_prob
        full_kelly = (true_prob * b - q) / b

        # Apply fractional Kelly then hard cap
        frac_kelly_dollars = full_kelly * self.config.kelly_fraction * bankroll
        max_dollars = bankroll * self.config.max_position_pct
        recommended_dollars = min(frac_kelly_dollars, max_dollars)
        recommended_dollars = max(recommended_dollars, 0.0)

        # Each contract costs market_price_cents / 100 dollars
        cost_per_contract = market_price_cents / 100.0
        recommended_contracts = max(1, int(recommended_dollars / cost_per_contract))

        return KellyResult(
            ticker=ticker, side=side, true_prob=true_prob,
            market_price_cents=market_price_cents, implied_prob=implied_prob,
            edge=edge, full_kelly_fraction=round(full_kelly, 4),
            fractional_kelly=round(full_kelly * self.config.kelly_fraction, 4),
            recommended_dollars=round(recommended_dollars, 2),
            recommended_contracts=recommended_contracts,
            bankroll=bankroll, viable=True,
            reason=f"Edge {edge:.3f}, full Kelly {full_kelly:.3f}, frac {self.config.kelly_fraction}x",
        )
