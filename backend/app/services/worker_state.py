from datetime import timedelta


MAX_ATTEMPTS = 5


def retry_at(now, attempts: int):
    return now + timedelta(seconds=min(5 * (2 ** max(0, attempts - 1)), 300))


def _as_aware(value):
    if value.tzinfo is None:
        from datetime import UTC
        return value.replace(tzinfo=UTC)
    return value


def next_poll_at(created_at, now, status: str):
    """Use short polling initially, then reduce provider load for long tasks."""
    if status != "processing":
        return now
    elapsed = (_as_aware(now) - _as_aware(created_at)).total_seconds()
    delay = 5 if elapsed < 120 else 15 if elapsed < 600 else 30
    return now + timedelta(seconds=delay)
