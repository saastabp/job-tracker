import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { Card, Form, Button, Row, Col, Spinner, Alert, Collapse, Badge, } from 'react-bootstrap';
import { Link, useParams } from 'react-router-dom';
import { useApi } from '../api/client';
import { STATUSES } from './SubmissionForm';
import { StatusBadge } from './Submissions';
export default function SubmissionDetail() {
    const apiFetch = useApi();
    const { id } = useParams();
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [showJd, setShowJd] = useState(false);
    const [savingField, setSavingField] = useState(null);
    const [editNotes, setEditNotes] = useState('');
    const [editTailoredTitle, setEditTailoredTitle] = useState('');
    const [editTailoredSummary, setEditTailoredSummary] = useState('');
    const load = useCallback(async () => {
        try {
            const r = await apiFetch(`/submissions/${id}`);
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            const d = await r.json();
            setData(d);
            setEditNotes(d.notes ?? '');
            setEditTailoredTitle(d.tailored_title ?? '');
            setEditTailoredSummary(d.tailored_summary ?? '');
            setError(null);
        }
        catch (e) {
            setError(String(e));
        }
    }, [apiFetch, id]);
    useEffect(() => {
        load();
    }, [load]);
    async function patchField(field, value, label) {
        setSavingField(label);
        try {
            const r = await apiFetch(`/submissions/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ [field]: value }),
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
            setSavingField(null);
        }
    }
    async function handleSave() {
        if (!data)
            return;
        const body = {};
        if (editNotes !== (data.notes ?? ''))
            body.notes = editNotes || null;
        if (editTailoredTitle !== (data.tailored_title ?? ''))
            body.tailored_title = editTailoredTitle || null;
        if (editTailoredSummary !== (data.tailored_summary ?? ''))
            body.tailored_summary = editTailoredSummary || null;
        if (Object.keys(body).length === 0)
            return;
        setSavingField('save');
        try {
            const r = await apiFetch(`/submissions/${id}`, {
                method: 'PUT',
                body: JSON.stringify(body),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            const d = await r.json();
            setData(d);
            setEditNotes(d.notes ?? '');
            setEditTailoredTitle(d.tailored_title ?? '');
            setEditTailoredSummary(d.tailored_summary ?? '');
            setError(null);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setSavingField(null);
        }
    }
    if (error && !data)
        return _jsx(Alert, { variant: "danger", children: error });
    if (!data)
        return _jsx(Spinner, { animation: "border", size: "sm" });
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "d-flex align-items-center mb-3", children: [_jsx(Link, { to: "/submissions", className: "me-3", children: "\u2190 Back" }), _jsx("h3", { className: "mb-0", children: data.role_title || _jsx("span", { className: "text-muted", children: "Untitled role" }) }), _jsx("span", { className: "ms-3", children: _jsx(StatusBadge, { status: data.status }) })] }), error && _jsx(Alert, { variant: "danger", children: error }), _jsxs(Row, { className: "g-3 mb-3", children: [_jsx(Col, { md: 6, children: _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Company" }), data.company_id ? (_jsx(Link, { to: `/companies/${data.company_id}`, children: data.company_name })) : (_jsx("span", { className: "text-muted", children: "\u2014" })), _jsx("hr", {}), _jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Submitted" }), _jsx("div", { children: data.submitted_on ?? '—' }), data.jd_url && (_jsxs(_Fragment, { children: [_jsx("hr", {}), _jsx(Card.Subtitle, { className: "text-muted mb-2", children: "JD URL" }), _jsx("a", { href: data.jd_url, target: "_blank", rel: "noreferrer", children: data.jd_url })] }))] }) }) }), _jsx(Col, { md: 6, children: _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Status" }), _jsx(Form.Select, { value: data.status, disabled: savingField === 'status', onChange: (e) => patchField('status', e.target.value, 'status'), children: STATUSES.map((s) => (_jsx("option", { value: s.short_name, children: s.label }, s.short_name))) }), _jsx("hr", {}), _jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Actions" }), _jsxs("div", { className: "d-flex gap-2 flex-wrap", children: [_jsxs(Button, { variant: "outline-primary", size: "sm", disabled: true, children: ["Tailor with AI ", _jsx(Badge, { bg: "light", text: "dark", children: "slice 06" })] }), _jsxs(Button, { variant: "outline-secondary", size: "sm", disabled: true, children: ["Trigger follow-up ", _jsx(Badge, { bg: "light", text: "dark", children: "slice 08" })] })] })] }) }) })] }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Notes" }), _jsx(Form.Control, { as: "textarea", rows: 3, value: editNotes, onChange: (e) => setEditNotes(e.target.value) })] }) }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsxs("div", { className: "d-flex justify-content-between align-items-center", children: [_jsx(Card.Subtitle, { className: "text-muted", children: "JD text" }), data.jd_snapshot && (_jsx(Button, { variant: "link", size: "sm", onClick: () => setShowJd((v) => !v), children: showJd ? 'Hide' : 'Show' }))] }), !data.jd_snapshot && (_jsx("div", { className: "text-muted mt-2", children: "No JD captured for this submission." })), _jsx(Collapse, { in: showJd, children: _jsx("pre", { className: "mt-3 mb-0 p-3 bg-light border rounded", style: { whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto' }, children: data.jd_text ?? '(unavailable)' }) })] }) }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Tailored title / summary" }), _jsxs(Form.Group, { className: "mb-3", children: [_jsx(Form.Label, { children: "Tailored title" }), _jsx(Form.Control, { value: editTailoredTitle, onChange: (e) => setEditTailoredTitle(e.target.value) })] }), _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Tailored summary" }), _jsx(Form.Control, { as: "textarea", rows: 4, value: editTailoredSummary, onChange: (e) => setEditTailoredSummary(e.target.value) })] })] }) }), _jsx("div", { className: "mb-3", children: _jsx(Button, { onClick: handleSave, disabled: savingField === 'save' ||
                        (editNotes === (data.notes ?? '') &&
                            editTailoredTitle === (data.tailored_title ?? '') &&
                            editTailoredSummary === (data.tailored_summary ?? '')), children: savingField === 'save' ? 'Saving…' : 'Save changes' }) }), data.follow_ups.length > 0 && (_jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Follow-ups" }), _jsx("ul", { className: "mb-0", children: data.follow_ups.map((f) => (_jsxs("li", { children: ["Due ", f.due_at ?? '—', f.actioned_at ? ` (actioned ${f.actioned_at})` : ' (pending)'] }, f.id))) })] }) })), data.responses.length > 0 && (_jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Responses" }), _jsx("ul", { className: "mb-0", children: data.responses.map((r) => (_jsxs("li", { children: [_jsx(Badge, { bg: "info", className: "me-2", children: r.classification }), r.subject ?? '(no subject)', " \u2014 ", r.received_at ?? ''] }, r.id))) })] }) }))] }));
}
