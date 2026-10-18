"""Value types passed between routes and services (design: Service interfaces).

Read models are shaped like the page tables they feed. Account numbers in them
are already masked, and no read model ever carries an SSN (D8).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class MarketStatus:
    is_open: bool
    closes_at: datetime | None  # market-local; None while closed or forced open
    next_open: datetime | None  # market-local; None while open or forced closed
    forced_open: bool = False   # an administrator chose Force open (D29)
    forced_closed: bool = False  # an administrator chose Force closed (D29)


@dataclass(frozen=True)
class UserRow:
    user_id: int
    name: str
    email: str
    date_of_birth: date
    is_active: bool
    signup: datetime
    is_admin: bool
    access_level: str | None = None
    department: str | None = None
    must_change_password: bool = False  # still on a temporary password (R18)
    session_version: int = 0            # part of the login ID; bumped to end other logins

    @property
    def role(self) -> str:
        return "Administrator" if self.is_admin else "Customer"


@dataclass(frozen=True)
class PortfolioRow:
    portfolio_id: int
    user_id: int
    account_name: str
    account_type: str
    account_number: str
    cash_balance: Decimal
    created_at: datetime

    @property
    def masked_account_number(self) -> str:
        from app.crypto import mask_last4

        return mask_last4(self.account_number)


@dataclass(frozen=True)
class BankAccountRow:
    bank_account_id: int
    user_id: int
    bank_name: str
    nickname: str
    masked_account_number: str  # never the full number (R10.5)
    routing_number: str
    created_at: datetime


@dataclass(frozen=True)
class TransferResult:
    transfer_id: int
    transfer_type: str          # "Deposit", "Withdrawal", or "Internal"
    amount: Decimal
    cash_balance: Decimal       # the portfolio's balance after the transfer (the From account for Internal)
    portfolio_name: str
    to_cash_balance: Decimal | None = None   # Internal only: the To account's balance after (R19.5)
    to_portfolio_name: str | None = None     # Internal only


@dataclass(frozen=True)
class StockRow:
    issuer_id: int
    symbol: str
    company_name: str
    exchange: str | None
    current_price: Decimal
    description: str | None
    shares_available: Decimal = Decimal("0")  # D16
    is_listed: bool = True                    # False once delisted (D20)

# --- Trading (design: Trading, Value types; R8, R9, A3, A4) ---------------------------------------

@dataclass(frozen=True)
class OrderRequest:
    portfolio_id: int
    symbol: str
    action: str                  # "Buy" or "Sell"
    unit: str                    # "Dollars" or "Shares" (A3)
    amount: Decimal              # dollars or shares, per unit
    order_type: str = "Market"   # A4
    timing: str = "Day Only"     # A4


@dataclass(frozen=True)
class Quote:
    symbol: str
    company_name: str
    price: Decimal
    previous_close: Decimal
    change: Decimal
    change_pct: Decimal
    market: MarketStatus
    shares_owned: Decimal | None  # set when a portfolio is given


@dataclass(frozen=True)
class OrderPreview:
    request: OrderRequest
    quote: Quote
    quantity: Decimal
    total: Decimal               # an estimate: the order executes at the price when confirmed (D11)


@dataclass(frozen=True)
class OrderResult:
    transaction_id: int
    symbol: str
    action: str
    quantity: Decimal
    price: Decimal
    total: Decimal
    cash_balance: Decimal        # the portfolio's cash after the trade


# --- Market data (design: Valuation and market data; R7, A8, A14, A19) ---------------------------

@dataclass(frozen=True)
class StockMover:
    """One row of a Stocks page panel or search result: price and change since the previous close."""
    issuer_id: int
    symbol: str
    company_name: str
    price: Decimal
    previous_close: Decimal
    change: Decimal
    change_pct: Decimal                 # rounded to 2 places, for display
    sparkline: tuple[Decimal, ...] = ()  # recent daily closes, oldest first
    trades_today: int = 0               # Trending only (A19)


@dataclass(frozen=True)
class StockDetail:
    """The stock detail view (R7.4): no Bid or Ask (D17)."""
    mover: StockMover
    exchange: str | None
    description: str | None
    open_price: Decimal | None          # today's open (A8); None before the first tick of the day
    history: tuple[tuple[date, Decimal], ...]  # (day, close), oldest first, for the price chart


# --- Valuation (design: Valuation and market data; R5, R6, D22, D23, D26) ----------------------------

@dataclass(frozen=True)
class PositionRow:
    """One equity row on Positions (R6.3). Money is rounded to the cent per position."""
    issuer_id: int
    symbol: str
    company_name: str
    is_listed: bool
    quantity: Decimal
    price: Decimal
    price_change: Decimal
    price_change_pct: Decimal     # also the position's Day Change % (D26)
    market_value: Decimal
    day_change: Decimal
    average_cost: Decimal
    cost_basis: Decimal
    gain_loss: Decimal
    reinvest: bool                # D22: saved, no other effect


@dataclass(frozen=True)
class ValueTotals:
    """Totals for a portfolio, a group of positions, or all portfolios (R5.1, R6.2, D26)."""
    cash: Decimal
    positions_value: Decimal
    total_value: Decimal
    day_change: Decimal
    day_change_pct: Decimal
    ytd_change: Decimal
    ytd_change_pct: Decimal
    cost_basis: Decimal
    gain_loss: Decimal
    gain_loss_pct: Decimal


@dataclass(frozen=True)
class PortfolioSummaryRow:
    portfolio: PortfolioRow
    totals: ValueTotals


@dataclass(frozen=True)
class AccountSummary:
    """The Summary page (R5): every portfolio, the Investments Total, and the value chart (D23)."""
    rows: tuple[PortfolioSummaryRow, ...]
    totals: ValueTotals
    chart: tuple[tuple[date, Decimal], ...]


@dataclass(frozen=True)
class PositionsView:
    """The Positions page for one portfolio (R6)."""
    portfolio: PortfolioRow
    positions: tuple[PositionRow, ...]
    equities: ValueTotals          # the Equities group's Total row
    totals: ValueTotals            # the Portfolio Value panel
    chart: tuple[tuple[date, Decimal], ...]


# --- History (design: History; R13, R19.4) ------------------------------------------------------------

@dataclass(frozen=True)
class HistoryRow:
    """One line of the History table (R13.2). Quantity, Price, and Symbol are blank (None) for transfers."""
    at: datetime                 # Arizona time
    type: str                    # Buy, Sell, Deposit, Withdrawal, Transfer out, Transfer in
    symbol: str | None
    description: str
    quantity: Decimal | None
    price: Decimal | None
    amount: Decimal              # the cash effect: + money in (sells, deposits, moves in), - money out
    status: str                  # Filled or Completed
    customer: str = ""           # the portfolio's owner, shown to administrators only (R13.6)
    account: str = ""            # "Growth •••• 6281", shown to administrators only


@dataclass(frozen=True)
class AdminStockRow:
    """One row of the Administration page's Stocks list (R14.7): listed and delisted stocks."""
    stock: StockRow
    change_pct: Decimal          # since the previous close (A8)

