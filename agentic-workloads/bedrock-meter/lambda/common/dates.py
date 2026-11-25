from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_QUOTA_TIME_ZONE = 'UTC'
DEFAULT_QUOTA_POLICY_VERSION = 1


def validate_time_zone(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError('quotaTimeZone must be a non-empty IANA time zone')
    normalized = name.strip()
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f'unknown IANA time zone: {normalized}') from exc
    return normalized


def quota_policy(config: dict) -> tuple[str, int]:
    time_zone = validate_time_zone(config.get('quotaTimeZone', DEFAULT_QUOTA_TIME_ZONE))
    raw_version = config.get('quotaTimeZoneVersion', DEFAULT_QUOTA_POLICY_VERSION)
    if isinstance(raw_version, bool):
        raise ValueError('quotaTimeZoneVersion must be a positive integer')
    version = int(raw_version)
    if version < 1:
        raise ValueError('quotaTimeZoneVersion must be a positive integer')
    return time_zone, version


def quota_today(time_zone: str, now: datetime | None = None) -> date:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('quota clock requires a timezone-aware datetime')
    return instant.astimezone(ZoneInfo(validate_time_zone(time_zone))).date()


def day_sk_prefix(policy_version: int) -> str:
    if policy_version < 1:
        raise ValueError('quota policy version must be positive')
    return 'DAY#' if policy_version == 1 else f'DAY#V{policy_version}#'


def day_sk(day: date | str, policy_version: int) -> str:
    value = day.isoformat() if isinstance(day, date) else day
    date.fromisoformat(value)
    return f'{day_sk_prefix(policy_version)}{value}'


def active_day_from_sk(sk: str, policy_version: int) -> str | None:
    prefix = day_sk_prefix(policy_version)
    if not isinstance(sk, str) or not sk.startswith(prefix):
        return None
    value = sk[len(prefix):]
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def iso_week_bounds(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def iso_week_key(monday: date) -> str:
    """ISO 8601 week key derived from .isocalendar() to handle year boundary correctly.

    e.g. 2027-01-01 (Friday) → '2026-W53', not '2027-W53'.
    """
    iso = monday.isocalendar()
    return f'{iso.year}-W{iso.week:02d}'


def month_bounds(today: date) -> tuple[date, date]:
    first = today.replace(day=1)
    if first.month == 12:
        last = first.replace(year=first.year + 1, month=1) - timedelta(days=1)
    else:
        last = first.replace(month=first.month + 1) - timedelta(days=1)
    return first, last


def month_key(first_day: date) -> str:
    return f'{first_day.year}-{first_day.month:02d}'


def athena_partition_predicate(start: date, end: date) -> str:
    months: set[tuple[str, str]] = set()
    cur = start.replace(day=1)
    while cur <= end:
        months.add((f'{cur.year:04d}', f'{cur.month:02d}'))
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    parts = ' OR '.join(f"(year='{y}' AND month='{m}')" for y, m in sorted(months))
    return f'({parts})'
