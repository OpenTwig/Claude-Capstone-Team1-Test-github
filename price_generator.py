"""The random price generator. Run it in its own terminal, next to the website:

    .venv/bin/python price_generator.py          (Windows: .venv\\Scripts\\python price_generator.py)

There is no real stock market feed, so this makes the prices up. Every 5 minutes:

  - If the market is open, every listed stock's price moves up or down by a small random amount.
  - If the market has closed for the day, it saves what every portfolio is worth (once a day),
    for the value charts on the Account and Positions pages.

Press Ctrl+C to stop it. Run only one at a time.
"""
import random
import time
import traceback
from decimal import Decimal

from app import create_app
from app.common import ARIZONA, ONE_CENT, now_utc, to_cents
from app.market import FORCE_OPEN, get_schedule, is_open, is_trading_day
from app.models import Inventory, Issuer, Portfolio, PortfolioValueDaily, StockPrice, db

SECONDS_BETWEEN_PRICE_MOVES = 5 * 60
BIGGEST_MOVE = 0.0025  # each move is at most 0.25% up or down (about 1.5% over a trading day)


def random_step(price):
    """The price after one move: a random change between -0.25% and +0.25%, rounded to the cent.

    Changing by a percentage (rather than a fixed amount) means a $5 stock and a $500 stock
    move by about the same share of their price. A price never goes below one cent.
    """
    change = random.uniform(-BIGGEST_MOVE, BIGGEST_MOVE)
    new_price = to_cents(price * Decimal(1 + change))
    return max(ONE_CENT, new_price)


def move_prices():
    """Move every listed stock's price once, and record today's opening and closing price."""
    today = now_utc().astimezone(ARIZONA).date()
    for stock in Issuer.query.filter_by(is_listed=True).all():
        old_price = stock.current_price
        stock.current_price = random_step(old_price)

        # One StockPrice row per stock per day. The day's first move creates it, opening at the
        # price before that move; every move after that updates the closing price.
        todays_row = StockPrice.query.filter_by(issuer_id=stock.issuer_id, price_date=today).first()
        if todays_row is None:
            todays_row = StockPrice(issuer_id=stock.issuer_id, price_date=today, open_price=old_price)
            db.session.add(todays_row)
        todays_row.close_price = stock.current_price
    db.session.commit()


def portfolio_value(portfolio):
    """Cash plus what all the portfolio's shares are worth at today's prices."""
    total = portfolio.cash_balance
    for holding in Inventory.query.filter_by(portfolio_id=portfolio.portfolio_id).all():
        total = total + to_cents(holding.quantity * holding.issuer.current_price)
    return total


def save_values_after_close():
    """Once each trading day, after the market closes, save what every portfolio is worth."""
    schedule = get_schedule()
    now = now_utc().astimezone(ARIZONA)
    if schedule["override"] == FORCE_OPEN:
        return  # prices are still moving
    if not is_trading_day(now.date(), schedule):
        return  # the market didn't open today
    if now.time() < schedule["close_time"]:
        return  # the market hasn't closed yet today
    if PortfolioValueDaily.query.filter_by(value_date=now.date()).first() is not None:
        return  # already saved today

    portfolios = Portfolio.query.all()
    for portfolio in portfolios:
        snapshot = PortfolioValueDaily(portfolio_id=portfolio.portfolio_id, value_date=now.date(),
                                       cash=portfolio.cash_balance,
                                       total_value=portfolio_value(portfolio))
        db.session.add(snapshot)
    db.session.commit()
    print(f"Saved the value of {len(portfolios)} portfolios for {now.date()}.")


def main():
    app = create_app()  # the same settings (.env) and database as the website
    print("Moving prices every 5 minutes while the market is open. Press Ctrl+C to stop.")
    while True:
        time.sleep(SECONDS_BETWEEN_PRICE_MOVES)
        with app.app_context():
            try:
                if is_open():
                    move_prices()
                else:
                    save_values_after_close()
            except Exception:
                # For example, the database restarted. Show what happened, and try again next time.
                # (Leaving the app_context throws away any half-finished changes.)
                traceback.print_exc()


# This runs only when the file is started directly (python price_generator.py), not when another
# file imports it, so the functions above can be used without starting the loop.
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:  # Ctrl+C
        print("Stopped.")
