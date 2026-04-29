import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { Table, Form, Button, Row, Col, Spinner, Alert, Badge, Card, } from 'react-bootstrap';
import { Link, useNavigate } from 'react-router-dom';
import { useApi } from '../api/client';
import SubmissionForm, { STATUSES } from './SubmissionForm';
const STATUS_VARIANTS = {
    applied: 'secondary',
    responded: 'info',
    interviewing: 'primary',
    offer: 'success',
    rejected: 'danger',
    ghosted: 'warning',
};
export function StatusBadge({ status }) {
    const label = STATUSES.find((s) => s.short_name === status)?.label ?? status;
    return (_jsx(Badge, { bg: STATUS_VARIANTS[status] ?? 'secondary', children: label }));
}
export default function Submissions() {
    const apiFetch = useApi();
    const navigate = useNavigate();
    const [rows, setRows] = useState(null);
    const [companies, setCompanies] = useState([]);
    const [error, setError] = useState(null);
    const [showForm, setShowForm] = useState(false);
    const [filterStatus, setFilterStatus] = useState('');
    const [filterCompany, setFilterCompany] = useState('');
    const [filterFrom, setFilterFrom] = useState('');
    const [filterTo, setFilterTo] = useState('');
    const load = useCallback(async () => {
        const params = new URLSearchParams();
        if (filterStatus)
            params.set('status', filterStatus);
        if (filterCompany)
            params.set('company_id', filterCompany);
        if (filterFrom)
            params.set('from', filterFrom);
        if (filterTo)
            params.set('to', filterTo);
        const qs = params.toString();
        const path = qs ? `/submissions?${qs}` : '/submissions';
        try {
            const r = await apiFetch(path);
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            setRows(await r.json());
            setError(null);
        }
        catch (e) {
            setError(String(e));
        }
    }, [apiFetch, filterStatus, filterCompany, filterFrom, filterTo]);
    useEffect(() => {
        load();
    }, [load]);
    useEffect(() => {
        apiFetch('/companies')
            .then((r) => (r.ok ? r.json() : []))
            .then((cs) => setCompanies(cs.map((c) => ({ id: c.id, name: c.name }))))
            .catch(() => setCompanies([]));
    }, [apiFetch]);
    function handleCreated(newId) {
        setShowForm(false);
        navigate(`/submissions/${newId}`);
    }
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "d-flex align-items-center justify-content-between mb-4", children: [_jsx("h3", { className: "mb-0", children: "Submissions" }), _jsx(Button, { onClick: () => setShowForm(true), children: "+ New submission" })] }), _jsx(Card, { className: "mb-3", children: _jsx(Card.Body, { children: _jsxs(Row, { className: "g-2 align-items-end", children: [_jsxs(Col, { md: 3, children: [_jsx(Form.Label, { className: "small text-muted", children: "Status" }), _jsxs(Form.Select, { value: filterStatus, onChange: (e) => setFilterStatus(e.target.value), children: [_jsx("option", { value: "", children: "All" }), STATUSES.map((s) => (_jsx("option", { value: s.short_name, children: s.label }, s.short_name)))] })] }), _jsxs(Col, { md: 3, children: [_jsx(Form.Label, { className: "small text-muted", children: "Company" }), _jsxs(Form.Select, { value: filterCompany, onChange: (e) => setFilterCompany(e.target.value), children: [_jsx("option", { value: "", children: "All" }), companies.map((c) => (_jsx("option", { value: c.id, children: c.name }, c.id)))] })] }), _jsxs(Col, { md: 3, children: [_jsx(Form.Label, { className: "small text-muted", children: "From" }), _jsx(Form.Control, { type: "date", value: filterFrom, onChange: (e) => setFilterFrom(e.target.value) })] }), _jsxs(Col, { md: 3, children: [_jsx(Form.Label, { className: "small text-muted", children: "To" }), _jsx(Form.Control, { type: "date", value: filterTo, onChange: (e) => setFilterTo(e.target.value) })] })] }) }) }), error && _jsx(Alert, { variant: "danger", children: error }), !rows ? (_jsx(Spinner, { animation: "border", size: "sm" })) : rows.length === 0 ? (_jsx(Card, { children: _jsxs(Card.Body, { className: "text-muted", children: ["No submissions yet. Click ", _jsx("strong", { children: "+ New submission" }), " to log one."] }) })) : (_jsxs(Table, { hover: true, responsive: true, className: "align-middle", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Date" }), _jsx("th", { children: "Company" }), _jsx("th", { children: "Role" }), _jsx("th", { children: "Status" }), _jsx("th", { children: "Notes" })] }) }), _jsx("tbody", { children: rows.map((row) => (_jsxs("tr", { style: { cursor: 'pointer' }, onClick: () => navigate(`/submissions/${row.id}`), children: [_jsx("td", { children: row.submitted_on ?? '—' }), _jsx("td", { children: row.company_id ? (_jsx(Link, { to: `/companies/${row.company_id}`, onClick: (e) => e.stopPropagation(), children: row.company_name })) : (_jsx("span", { className: "text-muted", children: "\u2014" })) }), _jsx("td", { children: row.role_title ?? _jsx("span", { className: "text-muted", children: "\u2014" }) }), _jsx("td", { children: _jsx(StatusBadge, { status: row.status }) }), _jsx("td", { className: "text-truncate", style: { maxWidth: 320 }, children: row.notes ?? '' })] }, row.id))) })] })), _jsx(SubmissionForm, { show: showForm, onHide: () => setShowForm(false), onCreated: handleCreated })] }));
}
