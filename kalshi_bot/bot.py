"""
Main bot loop — scans Kalshi sports markets, finds edge vs. sharp consensus,
sizes with fractional Kelly, places orders.

Run with: python bot.py
Set DRY_RUN=false in .env to live trade.
"""

import time
import uuid
import logging
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from config import Config
from kalshi_client import KalshiClient, KalshiAuthError, KalshiAPIError
from kelly import KellySizer
from odds_client import OddsClient, OddsAPIError
from strategy import BetSignal, SharpBettingStrategy

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def print_signal_table(signals: list[BetSignal]):
    table = Table(title="Bet Signals", box=box.ROUNDED, show_lines=True)
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Side", style="bold")
    table.add_column("Price", justify="right")
    table.add_column("True Prob", justify="right")
    table.add_column("Edge", justify="right", style="green")
    table.add_column("Kelly $", justify="right", style="yellow")
    table.add_column("Contracts", justify="right")
    table.add_column("Books", style="dim")

    for s in signals:
        edge_color = "green" if s.edge > 0.08 else "yellow"
        table.add_row(
            s.ticker[:30],
            s.side.upper(),
            f"{s.market_price_cents}¢",
            f"{s.true_prob:.1%}",
            f"[{edge_color}]{s.edge:.3f}[/{edge_color}]",
            f"${s.kelly.recommended_dollars:.2f}",
            str(s.kelly.recommended_contracts),
            ", ".join(s.books_used),
        )
    console.print(table)


def place_bet(client: KalshiClient, signal: BetSignal, dry_run: bool) -> bool:
    """Place a limit order for the signal. Returns True on success."""
    # Place one cent better than current ask for resting maker fill
    limit_price = max(1, signal.market_price_cents - 1)

    if dry_run:
        log.info(
            "[DRY RUN] Would place %s %s on %s @ %dc, %d contracts ($%.2f)",
            signal.side.upper(), "BUY", signal.ticker,
            limit_price, signal.kelly.recommended_contracts,
            signal.kelly.recommended_dollars,
        )
        return True

    try:
        order = client.create_order(
            ticker=signal.ticker,
            side=signal.side,
            action="buy",
            order_type="limit",
            count=signal.kelly.recommended_contracts,
            yes_price=limit_price if signal.side == "yes" else None,
            no_price=limit_price if signal.side == "no" else None,
            client_order_id=str(uuid.uuid4())[:8],
        )
        log.info("Order placed: %s", order)
        return True
    except KalshiAPIError as e:
        log.error("Order failed for %s: %s", signal.ticker, e)
        return False


def run_once(
    kalshi: KalshiClient,
    odds: OddsClient,
    strategy: SharpBettingStrategy,
    config: Config,
):
    console.rule(f"[bold blue]Scan @ {datetime.now().strftime('%H:%M:%S')}")

    # 1. Get current bankroll
    try:
        balance = kalshi.get_balance()
        bankroll_cents = balance.get("available_balance", 0)
        current_bankroll = bankroll_cents / 100.0
        console.print(f"[dim]Bankroll: [bold]${current_bankroll:,.2f}[/bold][/dim]")
    except KalshiAuthError as e:
        console.print(f"[red]Auth error: {e}[/red]")
        return
    except KalshiAPIError as e:
        log.warning("Could not fetch balance: %s", e)
        current_bankroll = config.bankroll.bankroll

    # 2. Fetch Kalshi sports markets
    try:
        markets = kalshi.get_all_sports_markets(config.strategy.kalshi_sports_tags)
        console.print(f"[dim]Loaded {len(markets)} Kalshi sports markets[/dim]")
    except KalshiAPIError as e:
        log.error("Failed to fetch Kalshi markets: %s", e)
        return

    # 3. Fetch sharp consensus lines from The Odds API
    try:
        active_sports = odds.get_active_sports()
        consensus_lines = odds.get_all_consensus_lines(active_sports[:20])
        console.print(f"[dim]Sharp consensus lines: {len(consensus_lines)}[/dim]")
    except OddsAPIError as e:
        log.error("Failed to fetch odds: %s", e)
        return

    # 4. Evaluate markets and rank signals
    signals = strategy.scan_markets(markets, consensus_lines, current_bankroll)

    if not signals:
        console.print("[yellow]No actionable signals this scan.[/yellow]")
        return

    print_signal_table(signals)

    # 5. Place bets on top signals
    placed = 0
    for signal in signals:
        # Guard: never exceed 5 positions per loop
        if placed >= 5:
            break
        console.print(
            Panel(
                f"[bold]{signal.title}[/bold]\n"
                f"Side: [cyan]{signal.side.upper()}[/cyan]  "
                f"Price: {signal.market_price_cents}¢  "
                f"Edge: [green]{signal.edge:.3f}[/green]\n"
                f"Contracts: {signal.kelly.recommended_contracts}  "
                f"Dollars: ${signal.kelly.recommended_dollars:.2f}\n"
                f"Reasons: {' | '.join(signal.signal_reasons)}",
                title="Placing Bet",
            )
        )
        if place_bet(kalshi, signal, config.strategy.dry_run):
            placed += 1

    console.print(f"[green]{placed} bet(s) placed this scan.[/green]")


def main():
    config = Config()

    console.print(
        Panel(
            f"[bold]Kalshi Sharp Sports Bot[/bold]\n"
            f"Env: [cyan]{config.kalshi.env}[/cyan]  "
            f"Dry run: [yellow]{config.strategy.dry_run}[/yellow]\n"
            f"Kelly: [green]{config.bankroll.kelly_fraction}x[/green]  "
            f"Max position: {config.bankroll.max_position_pct:.0%}  "
            f"Min edge: {config.bankroll.min_edge_threshold}",
            title="Bot Starting",
        )
    )

    if not config.strategy.dry_run:
        console.print(
            "[bold red]LIVE TRADING MODE — real money at risk. "
            "Set DRY_RUN=true to paper trade.[/bold red]"
        )

    kalshi = KalshiClient(config.kalshi)
    odds = OddsClient(config.odds)
    kelly_sizer = KellySizer(config.bankroll)
    strategy = SharpBettingStrategy(config.strategy, kelly_sizer)

    interval = config.strategy.loop_interval
    console.print(f"[dim]Scanning every {interval}s. Ctrl+C to stop.[/dim]\n")

    while True:
        try:
            run_once(kalshi, odds, strategy, config)
        except KeyboardInterrupt:
            console.print("[bold]Stopping bot.[/bold]")
            break
        except Exception as e:
            log.exception("Unexpected error in run loop: %s", e)

        time.sleep(interval)


if __name__ == "__main__":
    main()
