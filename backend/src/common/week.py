"""Date helpers for week-scoped endpoints.

The dashboard and outreach-history handlers both accept an optional
``?week_start=YYYY-MM-DD`` query parameter and need to default to the
current UTC Monday when it's absent. The parser is shared here so the
two handlers cannot drift on edge cases (e.g. empty-string parameter,
Monday rounding) — slice 11 explicitly called out this consistency
concern.

All week math is in UTC. A user-configurable timezone is deferred.
"""
from __future__ import annotations

import datetime


def today_utc() -> datetime.date:
    """Return the current calendar date in UTC.

    Returns
    -------
    datetime.date
        Today's date as observed at UTC.
    """
    return datetime.datetime.now(datetime.UTC).date()


def parse_week_start(qs: dict[str, str] | None) -> datetime.date:
    """Resolve a ``week_start`` query-string parameter to a date.

    Parameters
    ----------
    qs : dict[str, str] | None
        Query-string parameters from the API Gateway event, or ``None``
        when the request had no query string.

    Returns
    -------
    datetime.date
        The supplied ``week_start`` parsed from ``YYYY-MM-DD``, or the
        current week's Monday (UTC) when the parameter is absent or an
        empty string.

    Raises
    ------
    ValueError
        If ``week_start`` is present and non-empty but not parseable as
        ``YYYY-MM-DD``.

    Notes
    -----
    Non-Monday dates are accepted — the SPA's ``WeekNav`` rounds to Monday
    before navigating, but a hand-crafted URL with an arbitrary date must
    not 400. It just resolves to whatever 7-day range it implies.
    """
    raw = (qs or {}).get("week_start")
    if raw:
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError as e:
            raise ValueError(
                f"invalid week_start: {raw!r}; expected YYYY-MM-DD"
            ) from e
    today = today_utc()
    return today - datetime.timedelta(days=today.weekday())