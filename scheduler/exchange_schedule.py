"""Pure, fail-closed published regular-equities calendar audit (2026 only).

Offline schedule transcribed from official SSE/SZSE and NYSE/Nasdaq notices,
with exact source links in config/exchange_schedule_2026.json. This is NOT a
live exchange status feed. Unexpected closures, delayed quotes, settlement
completeness, correct strategy business date and database effects are NOT
verified; never use it alone to dispatch or mark a ledger verified.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEDULE = Path(__file__).resolve().parents[1] / "config" / "exchange_schedule_2026.json"
EXPECTED = {
    "stock_a": ("Asia/Shanghai", {"SSE", "SZSE"}, 2),
    "stock_us": ("America/New_York", {"NYSE", "NASDAQ"}, 2),
}


class CalendarGuard(ValueError):
    pass


def _validated(data: dict) -> dict:
    if (not isinstance(data, dict) or data.get("schemaVersion") != 1
            or data.get("coverageYear") != 2026
            or not isinstance(data.get("markets"), dict)
            or set(data["markets"]) != set(EXPECTED)):
        raise CalendarGuard("published_schedule_contract_invalid")
    for market, (zone, venues, n_sources) in EXPECTED.items():
        value = data["markets"][market]
        if (not isinstance(value, dict) or value.get("timezone") != zone
                or not isinstance(value.get("exchanges"), list)
                or set(value["exchanges"]) != venues
                or not isinstance(value.get("sources"), list)
                or len(value["sources"]) != n_sources
                or not all(isinstance(x, str) and x.startswith("https://")
                           for x in value["sources"])
                or value.get("regularClose") not in ("15:00", "16:00")
                or not isinstance(value.get("closedWeekdays"), list)
                or not isinstance(value.get("earlyCloses"), dict)):
            raise CalendarGuard("published_schedule_market_invalid")
        seen = set()
        for name in value["closedWeekdays"]:
            day = _day(name)
            if day.year != 2026 or day.weekday() > 4 or name in seen:
                raise CalendarGuard("published_schedule_holiday_invalid")
            seen.add(name)
        for name, hour in value["earlyCloses"].items():
            day = _day(name)
            _hour(hour)
            if day.year != 2026 or day.weekday() > 4 or name in seen:
                raise CalendarGuard("published_schedule_early_close_invalid")
    return data


def _day(value: str) -> date:
    if not isinstance(value, str):
        raise CalendarGuard("invalid_schedule_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CalendarGuard("invalid_schedule_date") from exc
    if parsed.isoformat() != value:
        raise CalendarGuard("invalid_schedule_date")
    return parsed


def _hour(value: str) -> time:
    if not isinstance(value, str) or len(value) != 5:
        raise CalendarGuard("invalid_schedule_close")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise CalendarGuard("invalid_schedule_close") from exc
    if parsed.strftime("%H:%M") != value:
        raise CalendarGuard("invalid_schedule_close")
    return parsed


def load_schedule(path: Path = SCHEDULE) -> dict:
    try:
        return _validated(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise CalendarGuard("published_schedule_unavailable") from exc


def evaluate(task: str, entered: str = "", *, now: datetime | None = None,
             schedule: dict | None = None) -> dict:
    if task not in EXPECTED:
        raise CalendarGuard("unsupported_market")
    if not isinstance(entered, str):
        raise CalendarGuard("invalid_schedule_date")
    instant = datetime.now(timezone.utc) if now is None else now
    if not isinstance(instant, datetime) or instant.tzinfo is None:
        raise CalendarGuard("timezone_aware_clock_required")
    data = _validated(schedule) if schedule is not None else load_schedule()
    record = data["markets"][task]
    zone = ZoneInfo(record["timezone"])
    local = instant.astimezone(zone)
    # Fail closed outside the verified reference year. Do not quietly pick
    # the last day of 2026 for an audit started in 2027.
    if local.year != data["coverageYear"]:
        return {
            "businessDate": entered if entered else None,
            "calendarStatus": "outside_published_year",
            "publishedScheduleMatched": False,
            "scheduledSessionClosed": False,
            "calendarCoverageCurrent": False,
            "marketDataReady": False,
            "businessDateAuthorized": False,
            "dateMode": "manual_override" if entered else "auto_schedule_unavailable",
            "marketTimezone": record["timezone"],
            "marketLocalClock": local.isoformat(timespec="seconds"),
            "scheduledClose": None,
        }
    if entered:
        requested = _day(entered)
        if requested.year != data["coverageYear"]:
            return {
                "businessDate": entered, "calendarStatus": "outside_published_year",
                "publishedScheduleMatched": False, "scheduledSessionClosed": False,
                "calendarCoverageCurrent": False, "marketDataReady": False,
                "businessDateAuthorized": False, "dateMode": "manual_override",
                "marketTimezone": record["timezone"],
                "marketLocalClock": local.isoformat(timespec="seconds"),
                "scheduledClose": None,
            }
        selected = requested
        mode = "manual_override"
    else:
        selected = None
        mode = "auto_official_schedule_candidate"
        cursor = local.date()
        while cursor.year == data["coverageYear"]:
            name = cursor.isoformat()
            eligible = (cursor.weekday() < 5
                        and name not in record["closedWeekdays"])
            if eligible:
                close = _hour(record["earlyCloses"].get(
                    name, record["regularClose"]))
                if local >= datetime.combine(cursor, close, tzinfo=zone):
                    selected = cursor
                    break
            cursor -= timedelta(days=1)
        if selected is None:
            return {
                "businessDate": None, "calendarStatus": "no_complete_session_in_coverage",
                "publishedScheduleMatched": False, "scheduledSessionClosed": False,
                "calendarCoverageCurrent": True, "marketDataReady": False,
                "businessDateAuthorized": False, "dateMode": mode,
                "marketTimezone": record["timezone"],
                "marketLocalClock": local.isoformat(timespec="seconds"),
                "scheduledClose": None,
            }
    name = selected.isoformat()
    eligible = selected.weekday() < 5 and name not in record["closedWeekdays"]
    close = _hour(record["earlyCloses"].get(name, record["regularClose"]))
    moment = datetime.combine(selected, close, tzinfo=zone)
    complete = eligible and local >= moment
    return {
        "businessDate": name,
        "calendarStatus": (
            "published_session_past_scheduled_close" if complete
            else "published_session_not_yet_closed" if eligible
            else "exchange_scheduled_closed"
        ),
        "publishedScheduleMatched": eligible,
        "scheduledSessionClosed": complete,
        "calendarCoverageCurrent": True,
        "marketDataReady": False,  # Requires separate PRIVATE source readiness.
        "businessDateAuthorized": False,
        "dateMode": mode,
        "marketTimezone": record["timezone"],
        "marketLocalClock": local.isoformat(timespec="seconds"),
        "scheduledClose": moment.isoformat(timespec="seconds") if eligible else None,
    }
