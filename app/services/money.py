"""Money is stored and sent over the API in ngwee (100 ngwee = 1 kwacha). Anything a person reads
(screens, tickets, SMS, receipts, exports) shows kwacha, so every conversion goes through here."""


def zmw(ngwee: int | float | None, currency: str = "ZMW") -> str:
    """5,000 kwacha is stored as 500000; shown as 'ZMW 5,000.00'."""
    return f"{currency} {(ngwee or 0) / 100:,.2f}"


def kwacha(ngwee: int | float | None) -> float:
    return round((ngwee or 0) / 100, 2)
