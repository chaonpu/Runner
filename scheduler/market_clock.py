"""Deterministic, READ-ONLY market-clock date candidates for coordinator audit.

The candidate is deliberately NOT a verified trading session: weekdays and
nominal closing clocks cannot account for exchange holidays, early closes,
halts or data publication. Never use alone to authorize production dispatch,
ledger writes, retries or a VERIFIED state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time, timezone, date
from zoneinfo import ZoneInfo

MARKETS = {
    "stock_a": ("Asia/Shanghai", time(15, 0)),
    "stock_us": ("America/New_York", time(16, 0)),
}


def resolve_date(task: str, entered: str = "", *, now: datetime | None = None) -> dict:
    if task not in MARKETS:
        raise ValueError("unsupported_market")
    if not isinstance(entered, str):
        raise ValueError("invalid_business_date")
    tz_name, nominal_close = MARKETS[task]
    # Use the GitHub runner's UTC clock; NEVER inherit its local timezone.
    instant = datetime.now(timezone.utc) if now is None else now
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise ValueError("trusted_aware_clock_required")
    local = instant.astimezone(ZoneInfo(tz_name))
    if entered:
        try:
            if date.fromisoformat(entered).isoformat() != entered:
                raise ValueError()
        except ValueError as exc:
            raise ValueError("invalid_business_date") from exc
        chosen, mode = entered, "manual_override"
    else:
        day = local.date()
        # During a nominal session, use the PREVIOUS completed weekday.
        # Closed weekdays may still be holidays; this is just an audit key.
        if local.weekday() < 5 and local.time() < nominal_close:
            day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        chosen, mode = day.isoformat(), "auto_market_clock_candidate"
    return {
        "businessDate": chosen,
        "dateMode": mode,
        "marketTimezone": tz_name,
        "marketLocalClock": local.isoformat(timespec="seconds"),
        "exchangeSessionVerified": False,
        "dispatchDateAuthorized": False,
    }
