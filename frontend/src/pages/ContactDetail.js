import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { Card, Form, Button, Spinner, Alert, Table, Row, Col, } from 'react-bootstrap';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useApi } from '../api/client';
import { KindBadge } from './Contacts';
import { KINDS, METHODS, DIRECTIONS } from './ContactForm';
function methodLabel(m) {
    if (!m)
        return '';
    return METHODS.find((x) => x.short_name === m)?.label ?? m;
}
function directionLabel(d) {
    return DIRECTIONS.find((x) => x.short_name === d)?.label ?? d;
}
export default function ContactDetail() {
    const apiFetch = useApi();
    const navigate = useNavigate();
    const { id } = useParams();
    const [data, setData] = useState(null);
    const [companies, setCompanies] = useState([]);
    const [error, setError] = useState(null);
    const [editName, setEditName] = useState('');
    const [editKind, setEditKind] = useState('personal');
    const [editEmail, setEditEmail] = useState('');
    const [editPhone, setEditPhone] = useState('');
    const [editLinkedin, setEditLinkedin] = useState('');
    const [editMethod, setEditMethod] = useState('');
    const [editCompanyId, setEditCompanyId] = useState('');
    const [editNotes, setEditNotes] = useState('');
    const [saving, setSaving] = useState(false);
    const [outDir, setOutDir] = useState('outbound');
    const [outMethod, setOutMethod] = useState('');
    const [outAt, setOutAt] = useState('');
    const [outNotes, setOutNotes] = useState('');
    const [logging, setLogging] = useState(false);
    const load = useCallback(async () => {
        try {
            const r = await apiFetch(`/contacts/${id}`);
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            const d = await r.json();
            setData(d);
            setEditName(d.name);
            setEditKind(d.kind);
            setEditEmail(d.email ?? '');
            setEditPhone(d.phone ?? '');
            setEditLinkedin(d.linkedin_url ?? '');
            setEditMethod(d.primary_method ?? '');
            setEditCompanyId(d.company_id ? String(d.company_id) : '');
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
    useEffect(() => {
        apiFetch('/companies')
            .then((r) => (r.ok ? r.json() : []))
            .then((rows) => setCompanies(rows.map((c) => ({ id: c.id, name: c.name }))))
            .catch(() => setCompanies([]));
    }, [apiFetch]);
    async function handleSave() {
        setSaving(true);
        try {
            const r = await apiFetch(`/contacts/${id}`, {
                method: 'PUT',
                body: JSON.stringify({
                    name: editName,
                    kind: editKind,
                    email: editEmail || null,
                    phone: editPhone || null,
                    linkedin_url: editLinkedin || null,
                    primary_method: editMethod || null,
                    company_id: editCompanyId ? Number(editCompanyId) : null,
                    notes: editNotes || null,
                }),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            const d = await r.json();
            setData(d);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setSaving(false);
        }
    }
    async function handleDelete() {
        if (!data)
            return;
        if (!window.confirm(`Delete contact "${data.name}"?`))
            return;
        try {
            const r = await apiFetch(`/contacts/${id}`, { method: 'DELETE' });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            navigate('/contacts');
        }
        catch (e) {
            setError(String(e));
        }
    }
    async function handleLog(e) {
        e.preventDefault();
        setLogging(true);
        try {
            const r = await apiFetch(`/contacts/${id}/outreach`, {
                method: 'POST',
                body: JSON.stringify({
                    direction: outDir,
                    method: outMethod || undefined,
                    outreach_at: outAt || undefined,
                    notes: outNotes || undefined,
                }),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            const d = await r.json();
            setData(d);
            setOutMethod('');
            setOutAt('');
            setOutNotes('');
        }
        catch (err) {
            setError(String(err));
        }
        finally {
            setLogging(false);
        }
    }
    async function handleDeleteOutreach(eventId) {
        if (!window.confirm('Delete this outreach event?'))
            return;
        try {
            const r = await apiFetch(`/contacts/${id}/outreach/${eventId}`, {
                method: 'DELETE',
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            load();
        }
        catch (e) {
            setError(String(e));
        }
    }
    if (error && !data)
        return _jsx(Alert, { variant: "danger", children: error });
    if (!data)
        return _jsx(Spinner, { animation: "border", size: "sm" });
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "d-flex align-items-center mb-3", children: [_jsx(Link, { to: "/contacts", className: "me-3", children: "\u2190 Back" }), _jsx("h3", { className: "mb-0 me-2", children: data.name }), _jsx(KindBadge, { kind: data.kind })] }), error && _jsx(Alert, { variant: "danger", children: error }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsxs(Row, { className: "g-3", children: [_jsx(Col, { md: 8, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Name" }), _jsx(Form.Control, { value: editName, onChange: (e) => setEditName(e.target.value) })] }) }), _jsx(Col, { md: 4, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Kind" }), _jsx(Form.Select, { value: editKind, onChange: (e) => setEditKind(e.target.value), children: KINDS.map((k) => (_jsx("option", { value: k.short_name, children: k.label }, k.short_name))) })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Email" }), _jsx(Form.Control, { type: "email", value: editEmail, onChange: (e) => setEditEmail(e.target.value) })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Phone" }), _jsx(Form.Control, { type: "tel", value: editPhone, onChange: (e) => setEditPhone(e.target.value) })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "LinkedIn URL" }), _jsx(Form.Control, { type: "url", value: editLinkedin, onChange: (e) => setEditLinkedin(e.target.value) })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Preferred outreach method" }), _jsxs(Form.Select, { value: editMethod, onChange: (e) => setEditMethod(e.target.value), children: [_jsx("option", { value: "", children: "\u2014 none \u2014" }), METHODS.map((m) => (_jsx("option", { value: m.short_name, children: m.label }, m.short_name)))] })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Company" }), _jsxs(Form.Select, { value: editCompanyId, onChange: (e) => setEditCompanyId(e.target.value), children: [_jsx("option", { value: "", children: "\u2014 none \u2014" }), companies.map((c) => (_jsx("option", { value: c.id, children: c.name }, c.id)))] })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Notes" }), _jsx(Form.Control, { as: "textarea", rows: 3, value: editNotes, onChange: (e) => setEditNotes(e.target.value) })] }) })] }), _jsxs("div", { className: "mt-3 d-flex gap-2", children: [_jsx(Button, { onClick: handleSave, disabled: saving, children: saving ? 'Saving…' : 'Save' }), _jsx(Button, { variant: "outline-danger", onClick: handleDelete, children: "Delete contact" })] })] }) }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx("h5", { children: "Log outreach" }), _jsx(Form, { onSubmit: handleLog, children: _jsxs(Row, { className: "g-2 align-items-end", children: [_jsxs(Col, { md: 3, children: [_jsx(Form.Label, { className: "small text-muted", children: "Direction" }), _jsx(Form.Select, { value: outDir, onChange: (e) => setOutDir(e.target.value), children: DIRECTIONS.map((d) => (_jsx("option", { value: d.short_name, children: d.label }, d.short_name))) })] }), _jsxs(Col, { md: 3, children: [_jsx(Form.Label, { className: "small text-muted", children: "Method" }), _jsxs(Form.Select, { value: outMethod, onChange: (e) => setOutMethod(e.target.value), children: [_jsx("option", { value: "", children: "\u2014 pick \u2014" }), METHODS.map((m) => (_jsx("option", { value: m.short_name, children: m.label }, m.short_name)))] })] }), _jsxs(Col, { md: 3, children: [_jsx(Form.Label, { className: "small text-muted", children: "When" }), _jsx(Form.Control, { type: "datetime-local", value: outAt, onChange: (e) => setOutAt(e.target.value), placeholder: "now" })] }), _jsxs(Col, { md: 12, children: [_jsx(Form.Label, { className: "small text-muted", children: "Notes" }), _jsx(Form.Control, { as: "textarea", rows: 2, value: outNotes, onChange: (e) => setOutNotes(e.target.value) })] }), _jsx(Col, { md: 3, children: _jsx(Button, { type: "submit", disabled: logging, className: "w-100", children: logging ? 'Logging…' : 'Log outreach' }) })] }) })] }) }), _jsxs("h5", { className: "mt-4", children: ["Outreach history (", data.outreach_count, ")"] }), data.outreach.length === 0 ? (_jsx(Card, { children: _jsx(Card.Body, { className: "text-muted", children: "No outreach logged yet." }) })) : (_jsxs(Table, { hover: true, responsive: true, className: "align-middle", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "When" }), _jsx("th", { children: "Direction" }), _jsx("th", { children: "Method" }), _jsx("th", { children: "Notes" }), _jsx("th", {})] }) }), _jsx("tbody", { children: data.outreach.map((evt) => (_jsxs("tr", { children: [_jsx("td", { children: evt.outreach_at ?? '—' }), _jsx("td", { children: directionLabel(evt.direction) }), _jsx("td", { children: methodLabel(evt.method) }), _jsx("td", { className: "text-truncate", style: { maxWidth: 360 }, children: evt.notes ?? '' }), _jsx("td", { className: "text-end", children: _jsx(Button, { variant: "link", size: "sm", onClick: () => handleDeleteOutreach(evt.id), children: "Delete" }) })] }, evt.id))) })] }))] }));
}
