"""Unit tests for ``handlers/dashboard.py``.

The dashboard handler issues four sequential SELECTs against the same cursor
(submissions counts, contact_outreach by kind, follow_ups pending, targets,
recent submissions). Tests sequence ``fetchone`` / ``fetchall`` returns via
``side_effect`` to mirror that order.

Slice 11 made the handler week-scoped: ``today`` and ``week_start`` are
computed in Python (UTC), not from a MySQL helper SELECT. The fetchone
side_effect now has 2 entries (submissions counts, follow_ups pending);
the dates row that used to be the 3rd entry is gone.
"""
from __future__ import annotations

import datetime
import json


def _current_utc_monday() -> datetime.date:
    today = datetime.datetime.now(datetime.UTC).date()
    return today - datetime.timedelta(days=today.weekday())


def test_dashboard_today_shape(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import dashboard

    patched_conn("handlers.dashboard")
    mocker.patch("handlers.dashboard.get_user_id", return_value=42)

    # _query_counts issues these reads, in this order:
    #   fetchone: submissions counts → follow_ups pending
    #   fetchall: outreach-by-kind  → targets → recent submissions
    mock_cursor.fetchone.side_effect = [
        {"today_count": 2, "week_count": 7},
        {"pending": 3},
    ]
    mock_cursor.fetchall.side_effect = [
        [
            {"kind": "personal",  "direction": "outbound",
             "today_count": 1, "week_count": 4},
            {"kind": "recruiter", "direction": "outbound",
             "today_count": 0, "week_count": 2},
            {"kind": "recruiter", "direction": "inbound",
             "today_count": 1, "week_count": 3},
        ],
        [
            {"short_name": "submissions",       "cadence": "daily",  "goal": 5},
            {"short_name": "submissions",       "cadence": "weekly", "goal": 25},
            {"short_name": "personal_outreach", "cadence": "weekly", "goal": 10},
        ],
        [
            {
                "id": 99, "role_title": "SRE",
                "submitted_on": "2026-04-28",
                "company_name": "Acme",
                "status": "applied",
                "updated_at": "2026-04-28 10:00:00",
            },
        ],
    ]

    resp = dashboard.handler(auth_event("GET /dashboard/today"), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    today_utc = datetime.datetime.now(datetime.UTC).date()
    assert body["today"] == today_utc.isoformat()
    assert body["week_start"] == _current_utc_monday().isoformat()
    assert body["is_current_week"] is True
    m = body["metrics"]
    assert m["submissions"]["today"] == 2
    assert m["submissions"]["week"] == 7
    assert m["submissions"]["daily"] == 5
    assert m["submissions"]["weekly"] == 25
    assert m["personal_outreach"]["today"] == 1
    assert m["personal_outreach"]["week"] == 4
    assert m["personal_outreach"]["weekly"] == 10
    assert m["personal_outreach"]["inbound_today"] == 0
    assert m["personal_outreach"]["inbound_week"] == 0
    assert m["recruiter_outreach"]["today"] == 0
    assert m["recruiter_outreach"]["week"] == 2
    # The new inbound counters come through on a separate row.
    assert m["recruiter_outreach"]["inbound_today"] == 1
    assert m["recruiter_outreach"]["inbound_week"] == 3
    assert m["follow_ups"]["pending"] == 3
    # slice-03 addition: recent_submissions list.
    assert len(body["recent_submissions"]) == 1
    assert body["recent_submissions"][0]["id"] == 99
    assert body["recent_submissions"][0]["company_name"] == "Acme"


def test_dashboard_empty_user(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    """A brand-new user with zero submissions / outreach / targets."""
    from handlers import dashboard

    patched_conn("handlers.dashboard")
    mocker.patch("handlers.dashboard.get_user_id", return_value=42)

    mock_cursor.fetchone.side_effect = [
        {"today_count": 0, "week_count": 0},
        {"pending": 0},
    ]
    mock_cursor.fetchall.side_effect = [[], [], []]

    resp = dashboard.handler(auth_event("GET /dashboard/today"), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["is_current_week"] is True
    m = body["metrics"]
    assert m["submissions"]   == {"today": 0, "week": 0, "daily": None, "weekly": None}
    assert m["personal_outreach"]  == {
        "today": 0, "week": 0, "daily": None, "weekly": None,
        "inbound_today": 0, "inbound_week": 0,
    }
    assert m["recruiter_outreach"] == {
        "today": 0, "week": 0, "daily": None, "weekly": None,
        "inbound_today": 0, "inbound_week": 0,
    }
    assert m["follow_ups"] == {"pending": 0, "daily": None, "weekly": None}
    assert body["recent_submissions"] == []


def test_outreach_query_groups_both_directions(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """The outreach query reports both directions so the dashboard can
    show outbound (goal-tracked) and inbound (informational) counts side
    by side. Goal mechanics still only consider outbound — that's enforced
    at the metric-population step, not in SQL."""
    from handlers import dashboard

    patched_conn("handlers.dashboard")
    mocker.patch("handlers.dashboard.get_user_id", return_value=42)

    mock_cursor.fetchone.side_effect = [
        {"today_count": 0, "week_count": 0},
        {"pending": 0},
    ]
    mock_cursor.fetchall.side_effect = [[], [], []]

    resp = dashboard.handler(auth_event("GET /dashboard/today"), lambda_ctx)

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    outreach_sql = next(s for s in sql_calls if "FROM contact_outreach" in s)
    # No direction filter — both 'outbound' and 'inbound' rows come back.
    assert "od.short_name = 'outbound'" not in outreach_sql
    # Project the direction column so the handler can branch per row.
    assert "od.short_name AS direction" in outreach_sql
    # GROUP BY must include direction, otherwise the SUMs would collapse
    # both directions into a single row per kind.
    assert "GROUP BY ck.short_name, od.short_name" in outreach_sql


def test_dashboard_with_past_week_start(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """When ?week_start resolves to a past week, today/inbound_today
    counters force-zero and is_current_week is False. The week counters
    reflect the requested BETWEEN range."""
    from handlers import dashboard

    patched_conn("handlers.dashboard")
    mocker.patch("handlers.dashboard.get_user_id", return_value=42)

    # The SQL still returns today_count (it doesn't know whether the
    # caller is on the current week); the handler zeros it in Python on
    # past/future weeks.
    mock_cursor.fetchone.side_effect = [
        {"today_count": 5, "week_count": 3},
        {"pending": 2},
    ]
    mock_cursor.fetchall.side_effect = [
        [
            {"kind": "personal",  "direction": "outbound",
             "today_count": 7, "week_count": 4},
            {"kind": "recruiter", "direction": "inbound",
             "today_count": 9, "week_count": 1},
        ],
        [],
        [],
    ]

    resp = dashboard.handler(
        auth_event("GET /dashboard/today", qs={"week_start": "2026-04-13"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["week_start"] == "2026-04-13"
    assert body["is_current_week"] is False
    m = body["metrics"]
    # Today counters force-zeroed because we are not on the current week.
    assert m["submissions"]["today"] == 0
    assert m["personal_outreach"]["today"] == 0
    assert m["recruiter_outreach"]["inbound_today"] == 0
    # Week counters reflect the requested range, not today's week.
    assert m["submissions"]["week"] == 3
    assert m["personal_outreach"]["week"] == 4
    assert m["recruiter_outreach"]["inbound_week"] == 1

    # The submissions SQL got the BETWEEN params with the requested range.
    week_start = datetime.date(2026, 4, 13)
    week_end = week_start + datetime.timedelta(days=6)
    submissions_call = next(
        c for c in mock_cursor.execute.call_args_list
        if "FROM submissions" in c.args[0]
        and "today_count" in c.args[0]
        and "BETWEEN" in c.args[0]
    )
    # Params order: (today, week_start, week_end, user_id)
    assert submissions_call.args[1][1] == week_start
    assert submissions_call.args[1][2] == week_end


def test_dashboard_bad_week_start_returns_400(
    mocker, patched_conn, auth_event, lambda_ctx,
):
    """Malformed ?week_start values 400 before the DB is touched."""
    from handlers import dashboard

    # patched_conn still set up, but no fetchone/fetchall expected — the
    # parse fails before get_connection() is called.
    patched_conn("handlers.dashboard")

    resp = dashboard.handler(
        auth_event("GET /dashboard/today", qs={"week_start": "not-a-date"}),
        lambda_ctx,
    )
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert "week_start" in body["error"]


def test_dashboard_default_week_is_current(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """No ?week_start → resolves to the current UTC Monday, is_current_week=True."""
    from handlers import dashboard

    patched_conn("handlers.dashboard")
    mocker.patch("handlers.dashboard.get_user_id", return_value=42)
    mock_cursor.fetchone.side_effect = [
        {"today_count": 0, "week_count": 0},
        {"pending": 0},
    ]
    mock_cursor.fetchall.side_effect = [[], [], []]

    resp = dashboard.handler(auth_event("GET /dashboard/today"), lambda_ctx)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    today_utc = datetime.datetime.now(datetime.UTC).date()
    expected_monday = today_utc - datetime.timedelta(days=today_utc.weekday())
    assert body["today"] == today_utc.isoformat()
    assert body["week_start"] == expected_monday.isoformat()
    assert body["is_current_week"] is True