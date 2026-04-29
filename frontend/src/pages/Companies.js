import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Table, Spinner, Alert, Card, Badge } from 'react-bootstrap';
import { Link } from 'react-router-dom';
import { useApi } from '../api/client';
export default function Companies() {
    const apiFetch = useApi();
    const [rows, setRows] = useState(null);
    const [error, setError] = useState(null);
    useEffect(() => {
        apiFetch('/companies')
            .then((r) => {
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            return r.json();
        })
            .then(setRows)
            .catch((e) => setError(String(e)));
    }, [apiFetch]);
    return (_jsxs(_Fragment, { children: [_jsx("h3", { className: "mb-4", children: "Companies" }), error && _jsx(Alert, { variant: "danger", children: error }), !rows ? (_jsx(Spinner, { animation: "border", size: "sm" })) : rows.length === 0 ? (_jsx(Card, { children: _jsx(Card.Body, { className: "text-muted", children: "No companies yet. Companies are created automatically when you log a submission." }) })) : (_jsxs(Table, { hover: true, responsive: true, className: "align-middle", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Company" }), _jsx("th", { children: "Submissions" }), _jsx("th", { children: "Notes" })] }) }), _jsx("tbody", { children: rows.map((c) => (_jsxs("tr", { children: [_jsx("td", { children: _jsx(Link, { to: `/companies/${c.id}`, children: c.name }) }), _jsx("td", { children: _jsx(Badge, { bg: "secondary", children: c.submission_count }) }), _jsx("td", { className: "text-truncate", style: { maxWidth: 480 }, children: c.notes ?? '' })] }, c.id))) })] }))] }));
}
