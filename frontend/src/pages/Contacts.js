import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { Table, Form, Button, Row, Col, Spinner, Alert, Badge, Card, } from 'react-bootstrap';
import { Link, useNavigate } from 'react-router-dom';
import { useApi } from '../api/client';
import ContactForm, { KINDS } from './ContactForm';
const KIND_VARIANTS = {
    personal: 'primary',
    recruiter: 'info',
};
export function KindBadge({ kind }) {
    const label = KINDS.find((k) => k.short_name === kind)?.label ?? kind;
    return _jsx(Badge, { bg: KIND_VARIANTS[kind] ?? 'secondary', children: label });
}
export default function Contacts() {
    const apiFetch = useApi();
    const navigate = useNavigate();
    const [rows, setRows] = useState(null);
    const [error, setError] = useState(null);
    const [showForm, setShowForm] = useState(false);
    const [filterKind, setFilterKind] = useState('');
    const load = useCallback(async () => {
        const params = new URLSearchParams();
        if (filterKind)
            params.set('kind', filterKind);
        const qs = params.toString();
        const path = qs ? `/contacts?${qs}` : '/contacts';
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
    }, [apiFetch, filterKind]);
    useEffect(() => {
        load();
    }, [load]);
    function handleCreated(newId) {
        setShowForm(false);
        navigate(`/contacts/${newId}`);
    }
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "d-flex align-items-center justify-content-between mb-4", children: [_jsx("h3", { className: "mb-0", children: "Contacts" }), _jsx(Button, { onClick: () => setShowForm(true), children: "+ New contact" })] }), _jsx(Card, { className: "mb-3", children: _jsx(Card.Body, { children: _jsx(Row, { className: "g-2 align-items-end", children: _jsxs(Col, { md: 3, children: [_jsx(Form.Label, { className: "small text-muted", children: "Kind" }), _jsxs(Form.Select, { value: filterKind, onChange: (e) => setFilterKind(e.target.value), children: [_jsx("option", { value: "", children: "All" }), KINDS.map((k) => (_jsx("option", { value: k.short_name, children: k.label }, k.short_name)))] })] }) }) }) }), error && _jsx(Alert, { variant: "danger", children: error }), !rows ? (_jsx(Spinner, { animation: "border", size: "sm" })) : rows.length === 0 ? (_jsx(Card, { children: _jsxs(Card.Body, { className: "text-muted", children: ["No contacts yet. Click ", _jsx("strong", { children: "+ New contact" }), " to add one."] }) })) : (_jsxs(Table, { hover: true, responsive: true, className: "align-middle", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Kind" }), _jsx("th", { children: "Name" }), _jsx("th", { children: "Company" }), _jsx("th", { children: "Last outreach" }), _jsx("th", { children: "Total" })] }) }), _jsx("tbody", { children: rows.map((row) => (_jsxs("tr", { style: { cursor: 'pointer' }, onClick: () => navigate(`/contacts/${row.id}`), children: [_jsx("td", { children: _jsx(KindBadge, { kind: row.kind }) }), _jsx("td", { children: _jsx(Link, { to: `/contacts/${row.id}`, onClick: (e) => e.stopPropagation(), children: row.name }) }), _jsx("td", { children: row.company_id ? (_jsx(Link, { to: `/companies/${row.company_id}`, onClick: (e) => e.stopPropagation(), children: row.company_name })) : (_jsx("span", { className: "text-muted", children: "\u2014" })) }), _jsx("td", { children: row.last_outreach_at ? (row.last_outreach_at.slice(0, 10)) : (_jsx("span", { className: "text-muted", children: "\u2014" })) }), _jsx("td", { children: _jsx(Badge, { bg: "secondary", children: row.outreach_count }) })] }, row.id))) })] })), _jsx(ContactForm, { show: showForm, onHide: () => setShowForm(false), onCreated: handleCreated })] }));
}
