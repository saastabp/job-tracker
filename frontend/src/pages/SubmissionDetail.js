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
    const [resumes, setResumes] = useState([]);
    const [error, setError] = useState(null);
    const [showJd, setShowJd] = useState(false);
    const [savingField, setSavingField] = useState(null);
    const [editNotes, setEditNotes] = useState('');
    const [editTailoredTitle, setEditTailoredTitle] = useState('');
    const [editTailoredSummary, setEditTailoredSummary] = useState('');
    const [editSubmittedOn, setEditSubmittedOn] = useState('');
    const [editJdUrl, setEditJdUrl] = useState('');
    const [editJdText, setEditJdText] = useState('');
    const [editingRoleTitle, setEditingRoleTitle] = useState(false);
    const [roleTitleDraft, setRoleTitleDraft] = useState('');
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
            setEditSubmittedOn(d.submitted_on ?? '');
            setEditJdUrl(d.jd_url ?? '');
            setEditJdText(d.jd_text ?? '');
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
        apiFetch('/resumes')
            .then((r) => (r.ok ? r.json() : []))
            .then((rows) => setResumes(rows.map((r) => ({
            id: r.id,
            title: r.title,
            is_master: r.is_master,
        }))))
            .catch(() => setResumes([]));
    }, [apiFetch]);
    function startEditingRoleTitle() {
        setRoleTitleDraft(data?.role_title ?? '');
        setEditingRoleTitle(true);
    }
    async function commitRoleTitle() {
        if (!data)
            return;
        const next = roleTitleDraft.trim();
        const current = data.role_title ?? '';
        setEditingRoleTitle(false);
        if (next === current)
            return;
        await patchField('role_title', next || null, 'role_title');
    }
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
        if (editSubmittedOn !== (data.submitted_on ?? ''))
            body.submitted_on = editSubmittedOn || null;
        if (editJdUrl.trim() !== (data.jd_url ?? ''))
            body.jd_url = editJdUrl.trim() || null;
        if (editJdText !== (data.jd_text ?? ''))
            body.jd_text = editJdText;
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
            setEditSubmittedOn(d.submitted_on ?? '');
            setEditJdUrl(d.jd_url ?? '');
            setEditJdText(d.jd_text ?? '');
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
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "d-flex align-items-center mb-3", children: [_jsx(Link, { to: "/submissions", className: "me-3", children: "\u2190 Back" }), editingRoleTitle ? (_jsx(Form.Control, { autoFocus: true, size: "lg", value: roleTitleDraft, onChange: (e) => setRoleTitleDraft(e.target.value), onBlur: commitRoleTitle, onKeyDown: (e) => {
                            if (e.key === 'Enter') {
                                e.preventDefault();
                                commitRoleTitle();
                            }
                            else if (e.key === 'Escape') {
                                setEditingRoleTitle(false);
                            }
                        }, disabled: savingField === 'role_title', placeholder: "Role title", style: { maxWidth: 480 } })) : (_jsx("h3", { className: "mb-0", onClick: startEditingRoleTitle, title: "Click to edit", style: { cursor: 'pointer' }, children: data.role_title || (_jsx("span", { className: "text-muted", children: "Untitled role" })) })), _jsx("span", { className: "ms-3", children: _jsx(StatusBadge, { status: data.status }) })] }), error && _jsx(Alert, { variant: "danger", children: error }), _jsxs(Row, { className: "g-3 mb-3", children: [_jsx(Col, { md: 6, children: _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Company" }), data.company_id ? (_jsx(Link, { to: `/companies/${data.company_id}`, children: data.company_name })) : (_jsx("span", { className: "text-muted", children: "\u2014" })), _jsx("hr", {}), _jsxs(Form.Group, { className: "mb-3", children: [_jsx(Form.Label, { className: "text-muted small mb-1", children: "Submitted on" }), _jsx(Form.Control, { type: "date", value: editSubmittedOn, onChange: (e) => setEditSubmittedOn(e.target.value) })] }), _jsxs(Form.Group, { children: [_jsx(Form.Label, { className: "text-muted small mb-1", children: "Link to Job Description" }), _jsx(Form.Control, { type: "url", value: editJdUrl, onChange: (e) => setEditJdUrl(e.target.value), placeholder: "https://..." }), data.jd_url && (_jsx(Form.Text, { children: _jsx("a", { href: data.jd_url, target: "_blank", rel: "noreferrer", children: "Open current link" }) }))] })] }) }) }), _jsx(Col, { md: 6, children: _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Status" }), _jsx(Form.Select, { value: data.status, disabled: savingField === 'status', onChange: (e) => patchField('status', e.target.value, 'status'), children: STATUSES.map((s) => (_jsx("option", { value: s.short_name, children: s.label }, s.short_name))) }), _jsx("hr", {}), _jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Resume" }), _jsxs(Form.Select, { value: data.resume_id ?? '', disabled: savingField === 'resume_id', onChange: (e) => patchField('resume_id', e.target.value ? Number(e.target.value) : null, 'resume_id'), children: [_jsx("option", { value: "", children: "\u2014 none \u2014" }), resumes.map((r) => (_jsxs("option", { value: r.id, children: [r.title || `Resume #${r.id}`, r.is_master ? ' (master)' : ''] }, r.id)))] }), data.resume_id && (_jsx("div", { className: "mt-1 small", children: _jsxs(Link, { to: `/resumes/${data.resume_id}`, children: ["Open ", data.resume_title || `resume #${data.resume_id}`] }) })), _jsx("hr", {}), _jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Actions" }), _jsxs("div", { className: "d-flex gap-2 flex-wrap", children: [_jsxs(Button, { variant: "outline-primary", size: "sm", disabled: true, children: ["Tailor with AI ", _jsx(Badge, { bg: "light", text: "dark", children: "slice 06" })] }), _jsxs(Button, { variant: "outline-secondary", size: "sm", disabled: true, children: ["Trigger follow-up ", _jsx(Badge, { bg: "light", text: "dark", children: "slice 08" })] })] })] }) }) })] }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Notes" }), _jsx(Form.Control, { as: "textarea", rows: 3, value: editNotes, onChange: (e) => setEditNotes(e.target.value) })] }) }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsxs("div", { className: "d-flex justify-content-between align-items-center mb-2", children: [_jsx(Card.Subtitle, { className: "text-muted", children: "Job Description" }), data.jd_snapshot && (_jsx(Button, { variant: "link", size: "sm", onClick: () => setShowJd((v) => !v), children: showJd ? 'Collapse' : 'Expand' }))] }), _jsx(Collapse, { in: showJd || !data.jd_snapshot, children: _jsxs("div", { children: [_jsx(Form.Control, { as: "textarea", rows: data.jd_snapshot ? 16 : 6, value: editJdText, onChange: (e) => setEditJdText(e.target.value), placeholder: "Paste the job description body here. Archived to S3 on save.", style: { fontFamily: 'monospace' } }), _jsx(Form.Text, { className: "text-muted", children: "Used as input to AI tailoring later. Leaving this empty clears the saved snapshot." })] }) })] }) }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Tailored title / summary" }), _jsxs(Form.Group, { className: "mb-3", children: [_jsx(Form.Label, { children: "Tailored title" }), _jsx(Form.Control, { value: editTailoredTitle, onChange: (e) => setEditTailoredTitle(e.target.value) })] }), _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Tailored summary" }), _jsx(Form.Control, { as: "textarea", rows: 4, value: editTailoredSummary, onChange: (e) => setEditTailoredSummary(e.target.value) })] })] }) }), _jsx("div", { className: "mb-3", children: _jsx(Button, { onClick: handleSave, disabled: savingField === 'save' ||
                        (editNotes === (data.notes ?? '') &&
                            editTailoredTitle === (data.tailored_title ?? '') &&
                            editTailoredSummary === (data.tailored_summary ?? '') &&
                            editSubmittedOn === (data.submitted_on ?? '') &&
                            editJdUrl.trim() === (data.jd_url ?? '') &&
                            editJdText === (data.jd_text ?? '')), children: savingField === 'save' ? 'Saving…' : 'Save changes' }) }), data.follow_ups.length > 0 && (_jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Follow-ups" }), _jsx("ul", { className: "mb-0", children: data.follow_ups.map((f) => (_jsxs("li", { children: ["Due ", f.due_at ?? '—', f.actioned_at ? ` (actioned ${f.actioned_at})` : ' (pending)'] }, f.id))) })] }) })), data.responses.length > 0 && (_jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Responses" }), _jsx("ul", { className: "mb-0", children: data.responses.map((r) => (_jsxs("li", { children: [_jsx(Badge, { bg: "info", className: "me-2", children: r.classification }), r.subject ?? '(no subject)', " \u2014 ", r.received_at ?? ''] }, r.id))) })] }) }))] }));
}
