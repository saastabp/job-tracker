"""Unit tests for ``handlers/dashboard.py``.

The dashboard handler issues five sequential SELECTs against the same cursor
(submissions counts, contact_outreach by kind, follow_ups pending, dates,
recent submissions). Tests sequence ``fetchone`` / ``fetchall`` returns via
``side_effect`` to mirror that order.
"""
from __future__ import annotations

import json


def test_dashboard_today_shape(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import dashboard

    patched_conn("handlers.dashboard")
    mocker.patch("handlers.dashboard.get_user_id", return_value=42)

    # _query_counts issues these reads, in this order:
    #   fetchone: submissions counts → follow_ups pending → dates row
    #   fetchall: outreach-by-kind  → targets → recent submissions
    mock_cursor.fetchone.side_effect = [
        {"today_count": 2, "week_count": 7},
        {"pending": 3},
        {"today": "2026-04-28", "week_start": "2026-04-27"},
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
    assert body["today"] == "2026-04-28"
    assert body["week_start"] == "2026-04-27"
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
        {"today": "2026-04-28", "week_start": "2026-04-27"},
    ]
    mock_cursor.fetchall.side_effect = [[], [], []]

    resp = dashboard.handler(auth_event("GET /dashboard/today"), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
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
        {"today": "2026-04-29", "week_start": "2026-04-27"},
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