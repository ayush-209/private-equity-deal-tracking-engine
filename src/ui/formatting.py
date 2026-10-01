"""Indian financial formatting: ₹ crore with lakh-style grouping, ₹ lakh crore above 1,00,000 cr."""
from __future__ import annotations

import math


def _isnum(v) -> bool:
    try:
        return v is not None and not math.isnan(float(v)) and not math.isinf(float(v))
    except (TypeError, ValueError):
        return False


def indian_group(n: float, decimals: int = 0) -> str:
    neg = n < 0
    s = f"{abs(n):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + (f".{frac}" if frac else "")


def inr_cr(v, decimals: int = 0) -> str:
    if not _isnum(v):
        return "n.m."
    v = float(v)
    if abs(v) >= 100_000:
        return f"₹{v / 100_000:,.2f} lakh cr"
    return f"₹{indian_group(v, decimals)} cr"


def pct(v, decimals: int = 1, signed: bool = False) -> str:
    if not _isnum(v):
        return "n.m."
    return f"{float(v) * 100:{'+' if signed else ''}.{decimals}f}%"


def mult(v, decimals: int = 1) -> str:
    if not _isnum(v):
        return "n.m."
    return f"{float(v):.{decimals}f}x"


def days(v) -> str:
    return f"{float(v):.0f} days" if _isnum(v) else "n.m."


def pp(v) -> str:
    return f"{float(v) * 100:+.1f}pp" if _isnum(v) else "n.m."


def score(v, out_of: float | None = None) -> str:
    if not _isnum(v):
        return "n/a"
    return f"{float(v):.1f}/{out_of:.0f}" if out_of else f"{float(v):.0f}/100"
