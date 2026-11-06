"""The database tables, written as Flask-SQLAlchemy models (one class per table).

Each class is one table, and each db.Column is one column in that table.

The names inside the database follow our ERD, for example the column "portfolioID".
In Python we use snake_case names instead, for example portfolio.portfolio_id.
That is what the first argument of db.Column does:

    portfolio_id = db.Column("portfolioID", db.Integer, primary_key=True)
                   Python name ^            ^ name in the database

db.create_all() (in __init__.py) creates any table that doesn't exist yet.

Money columns use Numeric(10, 2) and share columns Numeric(18, 6). Python reads them as
Decimal, which is exact. (A float would turn $0.10 + $0.20 into 0.30000000000000004.)
All times are stored in UTC.
"""
from datetime import timedelta
from functools import cached_property

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.mysql import DATETIME

db = SQLAlchemy()

# Column types used in several tables.
MONEY = db.Numeric(10, 2)
SHARES = db.Numeric(18, 6)
TIMESTAMP = DATETIME(fsp=3)  # a date and time, to the millisecond

# Lets MySQL fill in the current time when a row is created.
NOW = db.text("CURRENT_TIMESTAMP(3)")


def mask(number):
    """Hide all but the last 4 digits of an account number: '1234567890' -> '•••• 7890'."""
    if not number or len(number) <= 4:
        return "••••"
    last_four = number[-4:]
    return "•••• " + last_four


class User(UserMixin, db.Model):
    """Everyone who can log in: customers and administrators.

    UserMixin (from Flask-Login) adds what Flask-Login needs, such as is_authenticated.
    """
    __tablename__ = "User"

    user_id = db.Column("userID", db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    date_of_birth = db.Column(db.Date, nullable=False)
    ssn = db.Column(db.String(255), nullable=False)  # stored encrypted, never shown
    email = db.Column(db.String(320), nullable=False, unique=True)  # stored in lower case
    password_hash = db.Column(db.String(255), nullable=False)  # the bcrypt hash, never the password
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)  # False = can't log in
    signup = db.Column(TIMESTAMP, nullable=False, server_default=NOW)

    # The user's row in the Admin table. It is None for customers.
    admin = db.relationship("Admin", uselist=False)

    def get_id(self):
        """Flask-Login calls this to know which user to remember in the session."""
        return str(self.user_id)

    @property
    def is_admin(self):
        return self.admin is not None

    @property
    def role(self):
        if self.is_admin:
            return "Administrator"
        return "Customer"

    @property
    def access_level(self):
        if self.is_admin:
            return self.admin.access_level
        return None

    @property
    def department(self):
        if self.is_admin:
            return self.admin.department
        return None


class Admin(db.Model):
    """Extra details for users who are administrators."""
    __tablename__ = "Admin"

    admin_id = db.Column("adminID", db.Integer, primary_key=True)
    user_id = db.Column("userID", db.Integer, db.ForeignKey("User.userID"),
                        nullable=False, unique=True)
    access_level = db.Column(db.String(20), nullable=False)
    department = db.Column(db.String(100))


class BankAccount(db.Model):
    """A customer's bank account, used for deposits and withdrawals."""
    __tablename__ = "BankAccount"

    bank_account_id = db.Column("bankAccountID", db.Integer, primary_key=True)
    user_id = db.Column("userID", db.Integer, db.ForeignKey("User.userID"), nullable=False)
    bank_name = db.Column(db.String(100), nullable=False)
    nickname = db.Column(db.String(100), nullable=False)
    account_number = db.Column(db.String(255), nullable=False)  # stored encrypted
    routing_number = db.Column(db.CHAR(9), nullable=False)
    # Removing a bank account only sets this to False, so old transfers can still show it.
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(TIMESTAMP, nullable=False, server_default=NOW)


class Portfolio(db.Model):
    """A customer's brokerage account: some cash, plus the stocks in Inventory."""
    __tablename__ = "Portfolio"
    __table_args__ = (db.CheckConstraint("cash_balance >= 0"),)  # cash can never go negative

    portfolio_id = db.Column("portfolioID", db.Integer, primary_key=True)
    user_id = db.Column("userID", db.Integer, db.ForeignKey("User.userID"), nullable=False)
    cash_balance = db.Column(MONEY, nullable=False, default=0)
    account_name = db.Column(db.String(100), nullable=False)
    account_type = db.Column(db.String(20), nullable=False)
    account_number = db.Column(db.String(20), nullable=False, unique=True)
    created_at = db.Column(TIMESTAMP, nullable=False, server_default=NOW)

    owner = db.relationship("User")

    @property
    def masked_account_number(self):
        return mask(self.account_number)


class Issuer(db.Model):
    """A stock that can be traded."""
    __tablename__ = "Issuer"
    __table_args__ = (
        db.CheckConstraint("current_price >= 0.01"),
        db.CheckConstraint("shares_available >= 0"),
    )

    issuer_id = db.Column("issuerID", db.Integer, primary_key=True)
    company_name = db.Column(db.String(255), nullable=False)
    symbol = db.Column("ticker_symbol", db.String(10), nullable=False, unique=True)
    exchange = db.Column(db.String(10))
    current_price = db.Column(MONEY, nullable=False)
    shares_available = db.Column(SHARES, nullable=False)  # shares nobody owns yet
    description = db.Column(db.String(500))
    # A delisted stock can't be found or bought, but people who own it can still sell it.
    is_listed = db.Column(db.Boolean, nullable=False, default=True)

    # The pages show each stock's change since yesterday, so it is worked out here, once, instead
    # of in every page. (These import from common.py inside the function, because common.py
    # imports this file.)

    @cached_property  # works it out the first time it is used, then remembers it
    def previous_close(self):
        """The closing price of the last trading day before today.

        A brand-new stock has no earlier day, so we use its current price instead.
        """
        from app.common import today
        last_day = (
            StockPrice.query
            .filter(StockPrice.issuer_id == self.issuer_id)
            .filter(StockPrice.price_date < today())
            .order_by(StockPrice.price_date.desc())
            .first()
        )
        if last_day is None:
            return self.current_price
        return last_day.close_price

    @property
    def change(self):
        """How much the price has moved since yesterday's close, in dollars."""
        return self.current_price - self.previous_close

    @property
    def change_pct(self):
        """The same change, as a percentage of yesterday's close."""
        from app.common import percent
        return percent(self.change, self.previous_close)

    def recent_closes(self, days):
        """The closing prices of the last `days` days, oldest first (for the small charts)."""
        from app.common import today
        rows = (
            StockPrice.query
            .filter(StockPrice.issuer_id == self.issuer_id)
            .filter(StockPrice.price_date > today() - timedelta(days=days))
            .order_by(StockPrice.price_date)
            .all()
        )
        return [row.close_price for row in rows]


class StockPrice(db.Model):
    """One row per stock per trading day: that day's opening and closing price."""
    __tablename__ = "StockPrice"
    __table_args__ = (db.UniqueConstraint("issuerID", "price_date"),)  # one row per day

    price_id = db.Column("priceID", db.Integer, primary_key=True)
    issuer_id = db.Column("issuerID", db.Integer, db.ForeignKey("Issuer.issuerID"), nullable=False)
    price_date = db.Column(db.Date, nullable=False)  # the date in Arizona
    open_price = db.Column(MONEY, nullable=False)
    close_price = db.Column(MONEY, nullable=False)


class Inventory(db.Model):
    """How many shares of one stock a portfolio owns. Deleted when they are all sold."""
    __tablename__ = "Inventory"
    __table_args__ = (
        db.UniqueConstraint("portfolioID", "issuerID"),  # one row per stock per portfolio
        db.CheckConstraint("quantity > 0"),
    )

    inventory_id = db.Column("inventoryID", db.Integer, primary_key=True)
    portfolio_id = db.Column("portfolioID", db.Integer, db.ForeignKey("Portfolio.portfolioID"),
                             nullable=False)
    issuer_id = db.Column("issuerID", db.Integer, db.ForeignKey("Issuer.issuerID"), nullable=False)
    quantity = db.Column(SHARES, nullable=False)
    average_cost = db.Column(MONEY, nullable=False)  # the average price paid per share
    reinvest = db.Column(db.Boolean, nullable=False, default=False)  # the Reinvest? button

    issuer = db.relationship("Issuer")


class Transaction(db.Model):
    """A buy or sell order that was filled."""
    __tablename__ = "Transaction"
    __table_args__ = (
        db.CheckConstraint("type IN ('Buy', 'Sell')"),
        db.CheckConstraint("quantity > 0 AND total_amount > 0"),
    )

    transaction_id = db.Column("transactionID", db.Integer, primary_key=True)
    portfolio_id = db.Column("portfolioID", db.Integer, db.ForeignKey("Portfolio.portfolioID"),
                             nullable=False)
    issuer_id = db.Column("issuerID", db.Integer, db.ForeignKey("Issuer.issuerID"), nullable=False)
    type = db.Column(db.String(4), nullable=False)  # "Buy" or "Sell"
    quantity = db.Column(SHARES, nullable=False)
    price_per_share = db.Column(MONEY, nullable=False)
    total_amount = db.Column(MONEY, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Filled")
    order_type = db.Column(db.String(10), nullable=False, default="Market")
    timing = db.Column(db.String(10), nullable=False, default="Day Only")
    timestamp = db.Column(TIMESTAMP, nullable=False, server_default=NOW, index=True)

    issuer = db.relationship("Issuer")


class Transfer(db.Model):
    """Money moving in or out of a portfolio. There are three kinds:

    Deposit     bank account -> portfolio
    Withdrawal  portfolio -> bank account
    Internal    one portfolio -> another portfolio of the same customer
    """
    __tablename__ = "Transfer"
    __table_args__ = (
        db.CheckConstraint("amount > 0"),
        db.CheckConstraint("transfer_type IN ('Deposit', 'Withdrawal', 'Internal')"),
    )

    transfer_id = db.Column("transferID", db.Integer, primary_key=True)
    from_portfolio_id = db.Column("from_portfolioID", db.Integer,
                                  db.ForeignKey("Portfolio.portfolioID"))
    to_portfolio_id = db.Column("to_portfolioID", db.Integer,
                                db.ForeignKey("Portfolio.portfolioID"))
    bank_account_id = db.Column("bankAccountID", db.Integer,
                                db.ForeignKey("BankAccount.bankAccountID"))
    amount = db.Column(MONEY, nullable=False)
    transfer_date = db.Column(db.Date, nullable=False)
    transfer_type = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Completed")
    created_at = db.Column(TIMESTAMP, nullable=False, server_default=NOW)

    bank_account = db.relationship("BankAccount")
    # Two links to the same table, so we say which column each one uses.
    from_portfolio = db.relationship("Portfolio", foreign_keys=[from_portfolio_id])
    to_portfolio = db.relationship("Portfolio", foreign_keys=[to_portfolio_id])


class History(db.Model):
    """The list behind the History page: one row for each trade or transfer on a portfolio.

    A move between two portfolios gets two rows: "Transfer out" and "Transfer in".
    """
    __tablename__ = "History"
    __table_args__ = (db.Index("k_portfolio_time", "portfolioID", "timestamp"),)

    history_id = db.Column("historyID", db.Integer, primary_key=True)
    portfolio_id = db.Column("portfolioID", db.Integer, db.ForeignKey("Portfolio.portfolioID"),
                             nullable=False)
    transaction_id = db.Column("transactionID", db.Integer,
                               db.ForeignKey("Transaction.transactionID"))  # set for trades
    transfer_id = db.Column("transferID", db.Integer,
                            db.ForeignKey("Transfer.transferID"))  # set for transfers
    # Buy, Sell, Deposit, Withdrawal, Transfer out, or Transfer in
    action = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(TIMESTAMP, nullable=False, server_default=NOW)

    portfolio = db.relationship("Portfolio")
    trade = db.relationship("Transaction")
    transfer = db.relationship("Transfer")


class MarketSchedule(db.Model):
    """The market hours, set on the Administration page. The newest row is the one used."""
    __tablename__ = "MarketSchedule"
    __table_args__ = (db.CheckConstraint("close_time > open_time"),)

    schedule_id = db.Column("scheduleID", db.Integer, primary_key=True)
    open_time = db.Column(db.Time, nullable=False)  # Arizona time
    close_time = db.Column(db.Time, nullable=False)
    # Which days the market opens, stored as one number: Monday adds 1, Tuesday 2, Wednesday 4,
    # Thursday 8, Friday 16, Saturday 32, Sunday 64. So 31 means Monday to Friday.
    trading_days = db.Column(db.SmallInteger, nullable=False, default=31)
    # "Schedule" follows the hours above; "Open" or "Closed" forces the market open or closed.
    market_override = db.Column(db.String(10), nullable=False, default="Schedule")
    updated_by = db.Column(db.Integer, db.ForeignKey("User.userID"), nullable=False)
    updated_at = db.Column(TIMESTAMP, nullable=False, server_default=NOW)


class PortfolioValueDaily(db.Model):
    """What each portfolio was worth at the end of each trading day. Used for the charts."""
    __tablename__ = "PortfolioValueDaily"

    portfolio_id = db.Column("portfolioID", db.Integer, db.ForeignKey("Portfolio.portfolioID"),
                             primary_key=True)
    value_date = db.Column(db.Date, primary_key=True)
    cash = db.Column(MONEY, nullable=False)
    total_value = db.Column(db.Numeric(12, 2), nullable=False)  # cash + all the shares
