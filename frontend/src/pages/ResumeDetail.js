import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { Card, Form, Button, Row, Col, Spinner, Alert, Badge, ListGroup, } from 'react-bootstrap';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useApi } from '../api/client';
import ResumeUploadModal from './ResumeUploadModal';
export default function ResumeDetail() {
    const apiFetch = useApi();
    const navigate = useNavigate();
    const { id } = useParams();
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [editTitle, setEditTitle] = useState('');
    const [editSummary, setEditSummary] = useState('');
    const [savingField, setSavingField] = useState(null);
    const [showUpload, setShowUpload] = useState(false);
    const load = useCallback(async () => {
        try {
            const r = await apiFetch(`/resumes/${id}`);
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            const d = await r.json();
            setData(d);
            setEditTitle(d.title ?? '');
            setEditSummary(d.summary ?? '');
            setError(null);
        }
        catch (e) {
            setError(String(e));
        }
    }, [apiFetch, id]);
    useEffect(() => {
        load();
    }, [load]);
    async function patchField(field, value) {
        setSavingField(field);
        try {
            const r = await apiFetch(`/resumes/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ [field]: value }),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            await load();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setSavingField(null);
        }
    }
    async function handleSaveText() {
        if (!data)
            return;
        const body = {};
        if (editTitle.trim() !== (data.title ?? ''))
            body.title = editTitle.trim();
        if ((editSummary || null) !== (data.summary ?? null)) {
            body.summary = editSummary || null;
        }
        if (Object.keys(body).length === 0)
            return;
        setSavingField('save');
        try {
            const r = await apiFetch(`/resumes/${id}`, {
                method: 'PUT',
                body: JSON.stringify(body),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            await load();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setSavingField(null);
        }
    }
    async function handleDelete() {
        if (!data)
            return;
        if (!window.confirm(`Delete resume "${data.title}"?\n\n` +
            `This is reversible — toggle "Show deleted" on the Resumes list to ` +
            `restore it, or to permanently remove it.`)) {
            return;
        }
        setSavingField('delete');
        try {
            const r = await apiFetch(`/resumes/${id}`, { method: 'DELETE' });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            navigate('/resumes');
        }
        catch (e) {
            setError(String(e));
            setSavingField(null);
        }
    }
    if (error && !data)
        return _jsx(Alert, { variant: "danger", children: error });
    if (!data)
        return _jsx(Spinner, { animation: "border", size: "sm" });
    const dirty = editTitle.trim() !== (data.title ?? '') ||
        (editSummary || null) !== (data.summary ?? null);
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "d-flex align-items-center mb-3", children: [_jsx(Link, { to: "/resumes", className: "me-3", children: "\u2190 Back" }), _jsx("h3", { className: "mb-0", children: data.title || _jsx("span", { className: "text-muted", children: "Untitled" }) }), data.is_master && (_jsx(Badge, { bg: "primary", className: "ms-3", children: "master" }))] }), error && _jsx(Alert, { variant: "danger", children: error }), _jsxs(Row, { className: "g-3 mb-3", children: [_jsx(Col, { md: 8, children: _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsxs(Form.Group, { className: "mb-3", children: [_jsx(Form.Label, { children: "Title" }), _jsx(Form.Control, { value: editTitle, onChange: (e) => setEditTitle(e.target.value) })] }), _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Summary" }), _jsx(Form.Control, { as: "textarea", rows: 8, value: editSummary, onChange: (e) => setEditSummary(e.target.value) }), _jsx(Form.Text, { className: "text-muted", children: "Voice/style exemplar for AI-tailored summaries (slice 06)." })] }), _jsx("div", { className: "mt-3", children: _jsx(Button, { onClick: handleSaveText, disabled: !dirty || savingField === 'save', children: savingField === 'save' ? 'Saving…' : 'Save changes' }) })] }) }) }), _jsxs(Col, { md: 4, children: [_jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Master" }), _jsx(Form.Check, { type: "switch", id: "resume-master-switch", label: data.is_master ? 'Master resume' : 'Set as master', checked: data.is_master, disabled: savingField === 'is_master', onChange: (e) => patchField('is_master', e.target.checked) }), _jsx(Form.Text, { className: "text-muted", children: "Only one resume per account is the master at a time." })] }) }), _jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "File" }), data.has_file ? (_jsx(_Fragment, { children: _jsxs("div", { className: "mb-2", children: [data.original_filename ?? 'attached', data.download_url && (_jsx("a", { href: data.download_url, target: "_blank", rel: "noreferrer", className: "ms-2", children: "Open" }))] }) })) : (_jsx("div", { className: "text-muted mb-2", children: "No file attached." })), _jsx(Button, { size: "sm", onClick: () => setShowUpload(true), children: data.has_file ? 'Replace PDF' : 'Upload PDF' }), _jsx(Form.Text, { className: "d-block mt-2 text-muted", children: "PDF only, max 5 MB." })] }) }), _jsx(Card, { children: _jsx(Card.Body, { children: _jsx(Button, { variant: "outline-danger", size: "sm", onClick: handleDelete, disabled: savingField === 'delete', children: savingField === 'delete' ? 'Deleting…' : 'Delete resume' }) }) })] })] }), data.has_file && data.download_url && (_jsx(Card, { className: "mb-3", children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Preview" }), _jsx("iframe", { src: data.download_url, title: "Resume PDF preview", style: { width: '100%', height: 600, border: '1px solid #dee2e6' } })] }) })), _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-2", children: "Submissions using this resume" }), data.submissions.length === 0 ? (_jsx("div", { className: "text-muted", children: "None yet." })) : (_jsx(ListGroup, { variant: "flush", children: data.submissions.map((s) => (_jsxs(ListGroup.Item, { children: [_jsx(Link, { to: `/submissions/${s.id}`, children: s.role_title || '(untitled role)' }), s.company_name && _jsxs("span", { className: "text-muted", children: [" \u2014 ", s.company_name] }), _jsx("span", { className: "text-muted ms-2", children: s.submitted_on ?? '' }), _jsx(Badge, { bg: "secondary", className: "ms-2", children: s.status })] }, s.id))) }))] }) }), id && (_jsx(ResumeUploadModal, { show: showUpload, onHide: () => setShowUpload(false), resumeId: id, replacing: !!data.has_file, onUploaded: load }))] }));
}
