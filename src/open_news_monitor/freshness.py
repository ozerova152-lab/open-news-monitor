"""Conservative publication dating for unattended delivery."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


def publication_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        try:
            stamp = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    # Date-only and timezone-free times cannot prove freshness.
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc)


def classify(value: str | None, now: datetime, baseline: datetime | None, max_age_hours: int) -> str:
    stamp = publication_time(value)
    if stamp is None or stamp > now:
        return "uncertain"
    if stamp < now - timedelta(hours=max_age_hours) or (baseline and stamp <= baseline):
        return "archived"
    return "new" if baseline else "baseline"
