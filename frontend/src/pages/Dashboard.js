import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Row, Col, Card, Spinner, Alert, ProgressBar } from 'react-bootstrap';
import { useApi } from '../api/client';
const TODAY_TILES = [
    { key: 'submissions', label: 'Submissions' },
    { key: 'personal_outreach', label: 'Personal Outreach' },
    { key: 'recruiter_outreach', label: 'Recruiter Outreach' },
];
function ProgressTile({ label, count, goal, }) {
    const target = goal ?? 0;
    const pct = target > 0 ? Math.min(100, (count / target) * 100) : 0;
    return (_jsx(Card, { className: "h-100", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: label }), _jsxs("div", { className: "fs-3 fw-semibold", children: [count, _jsxs("span", { className: "text-muted fs-5", children: [" of ", goal ?? '—'] })] }), _jsx(ProgressBar, { now: pct, className: "mt-2", style: { height: 6 } })] }) }));
}
export default function Dashboard() {
    const apiFetch = useApi();
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    useEffect(() => {
        apiFetch('/dashboard/today')
            .then((r) => {
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            return r.json();
        })
            .then(setData)
            .catch((e) => setError(String(e)));
    }, [apiFetch]);
    if (error)
        return _jsx(Alert, { variant: "danger", children: error });
    if (!data)
        return _jsx(Spinner, { animation: "border", size: "sm" });
    const m = data.metrics;
    return (_jsxs(_Fragment, { children: [_jsx("h3", { className: "mb-4", children: "Dashboard" }), _jsxs("h5", { className: "text-muted", children: ["Today (", data.today, ")"] }), _jsx(Row, { className: "g-3 mb-4", children: TODAY_TILES.map(({ key, label }) => (_jsx(Col, { md: 4, children: _jsx(ProgressTile, { label: label, count: m[key].today ?? 0, goal: m[key].daily }) }, `today-${key}`))) }), _jsxs("h5", { className: "text-muted", children: ["This week (since ", data.week_start, ")"] }), _jsx(Row, { className: "g-3 mb-4", children: TODAY_TILES.map(({ key, label }) => (_jsx(Col, { md: 4, children: _jsx(ProgressTile, { label: label, count: m[key].week ?? 0, goal: m[key].weekly }) }, `week-${key}`))) }), _jsxs(Row, { className: "g-3", children: [_jsx(Col, { md: 6, children: _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Title, { children: "Recent activity" }), _jsx(Card.Text, { className: "text-muted mb-0", children: "No activity yet \u2014 submissions and responses will appear here." })] }) }) }), _jsx(Col, { md: 6, children: _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Title, { children: "Pending follow-ups" }), _jsx("div", { className: "fs-3 fw-semibold", children: m.follow_ups.pending ?? 0 }), _jsx(Card.Text, { className: "text-muted mb-0", children: "Submissions awaiting a follow-up." })] }) }) })] })] }));
}
