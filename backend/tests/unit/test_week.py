"""Unit tests for ``common/week.py``."""
from __future__ import annotations

import datetime

import pytest

from common.week import parse_week_start, today_utc


def _expected_current_monday() -> datetime.date:
    today = today_utc()
    return today - datetime.timedelta(days=today.weekday())


def test_parse_week_start_defaults_to_current_monday_when_qs_is_none():
    assert parse_week_start(None) == _expected_current_monday()


def test_parse_week_start_defaults_when_qs_is_empty_dict():
    assert parse_week_start({}) == _expected_current_monday()


def test_parse_week_start_defaults_when_param_is_empty_string():
    """Empty string is treated as absent — the SPA omits the param when
    it has no value, but a hand-crafted ``?week_start=`` URL should still
    resolve cleanly to today's Monday rather than 400."""
    assert parse_week_start({"week_start": ""}) == _expected_current_monday()


def test_parse_week_start_accepts_valid_iso_date():
    assert parse_week_start({"week_start": "2026-04-13"}) == datetime.date(2026, 4, 13)


def test_parse_week_start_accepts_non_monday():
    """Hand-edited mid-week dates are not rejected — the SPA rounds, but
    the backend accepts any date and treats it as the start of a 7-day
    range."""
    assert parse_week_start({"week_start": "2026-04-15"}) == datetime.date(2026, 4, 15)


def test_parse_week_start_rejects_malformed_string():
    with pytest.raises(ValueError, match="week_start"):
        parse_week_start({"week_start": "not-a-date"})


def test_parse_week_start_rejects_wrong_format():
    """``date.fromisoformat`` is strict — ``MM/DD/YYYY`` is rejected."""
    with pytest.raises(ValueError, match="week_start"):
        parse_week_start({"week_start": "04/13/2026"})


def test_today_utc_returns_date_not_datetime():
    """``today_utc`` should return ``datetime.date``, not a ``datetime``
    subclass. Callers want a plain date for SQL binding and arithmetic."""
    result = today_utc()
    assert isinstance(result, datetime.date)
    assert not isinstance(result, datetime.datetime)