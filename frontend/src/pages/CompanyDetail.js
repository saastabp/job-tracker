import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { Card, Form, Button, Spinner, Alert, Table, Row, Col, } from 'react-bootstrap';
import { Link, useParams } from 'react-router-dom';
import { useApi } from '../api/client';
import { StatusBadge } from './Submissions';
export default function CompanyDetail() {
    const apiFetch = useApi();
    const { id } = useParams();
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [editName, setEditName] = useState('');
    const [editNotes, setEditNotes] = useState('');
    const [saving, setSaving] = useState(false);
    const load = useCallback(async () => {
        try {
            const r = await apiFetch(`/companies/${id}`);
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            const d = await r.json();
            setData(d);
            setEditName(d.name);
            setEditNotes(d.notes ?? '');
            setError(null);
        }
        catch (e) {
            setError(String(e));
        }
    }, [apiFetch, id]);
    useEffect(() => {
        load();
    }, [load]);
    async function handleSave() {
        setSaving(true);
        try {
            const r = await apiFetch(`/companies/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ name: editName, notes: editNotes || null }),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            const d = await r.json();
            setData(d);
            setEditName(d.name);
            setEditNotes(d.notes ?? '');
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setSaving(false);
        }
    }
    if (error && !data)
        return _jsx(Alert, { variant: "danger", children: error });
    if (!data)
        return _jsx(Spinner, { animation: "border", size: "sm" });
    const dirty = editName !== data.name || editNotes !== (data.notes ?? '');
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "d-flex align-items-center mb-3", children: [_jsx(Link, { to: "/companies", className: "me-3", children: "\u2190 Back" }), _jsx("h3", { className: "mb-0", children: data.name })] }), error && _jsx(Alert, { variant: "danger", children: error }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsxs(Row, { className: "g-3", children: [_jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Name" }), _jsx(Form.Control, { value: editName, onChange: (e) => setEditName(e.target.value) })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Notes" }), _jsx(Form.Control, { as: "textarea", rows: 3, value: editNotes, onChange: (e) => setEditNotes(e.target.value) })] }) })] }), _jsx("div", { className: "mt-3", children: _jsx(Button, { onClick: handleSave, disabled: !dirty || saving, children: saving ? 'Saving…' : 'Save' }) })] }) }), _jsxs("h5", { className: "mt-4", children: ["Submissions (", data.submission_count, ")"] }), data.submissions.length === 0 ? (_jsx(Card, { children: _jsx(Card.Body, { className: "text-muted", children: "No submissions yet." }) })) : (_jsxs(Table, { hover: true, responsive: true, className: "align-middle", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Date" }), _jsx("th", { children: "Role" }), _jsx("th", { children: "Status" }), _jsx("th", { children: "Notes" })] }) }), _jsx("tbody", { children: data.submissions.map((s) => (_jsxs("tr", { children: [_jsx("td", { children: s.submitted_on ?? '—' }), _jsx("td", { children: _jsx(Link, { to: `/submissions/${s.id}`, children: s.role_title ?? '(untitled)' }) }), _jsx("td", { children: _jsx(StatusBadge, { status: s.status }) }), _jsx("td", { className: "text-truncate", style: { maxWidth: 320 }, children: s.notes ?? '' })] }, s.id))) })] }))] }));
}
