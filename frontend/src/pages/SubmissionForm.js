import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Modal, Form, Button, Alert, Row, Col } from 'react-bootstrap';
import { useApi } from '../api/client';
export const STATUSES = [
    { short_name: 'applied', label: 'Applied' },
    { short_name: 'responded', label: 'Responded' },
    { short_name: 'interviewing', label: 'Interviewing' },
    { short_name: 'offer', label: 'Offer' },
    { short_name: 'rejected', label: 'Rejected' },
    { short_name: 'ghosted', label: 'Ghosted' },
];
function todayIso() {
    return new Date().toISOString().slice(0, 10);
}
export default function SubmissionForm({ show, onHide, onCreated }) {
    const apiFetch = useApi();
    const [companies, setCompanies] = useState([]);
    const [companyName, setCompanyName] = useState('');
    const [roleTitle, setRoleTitle] = useState('');
    const [status, setStatus] = useState('applied');
    const [submittedOn, setSubmittedOn] = useState(todayIso());
    const [jdUrl, setJdUrl] = useState('');
    const [jdText, setJdText] = useState('');
    const [notes, setNotes] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    useEffect(() => {
        if (!show)
            return;
        apiFetch('/companies')
            .then((r) => (r.ok ? r.json() : []))
            .then((rows) => setCompanies(rows.map((c) => ({ id: c.id, name: c.name }))))
            .catch(() => setCompanies([]));
    }, [show, apiFetch]);
    function reset() {
        setCompanyName('');
        setRoleTitle('');
        setStatus('applied');
        setSubmittedOn(todayIso());
        setJdUrl('');
        setJdText('');
        setNotes('');
        setError(null);
    }
    async function handleSubmit(e) {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const r = await apiFetch('/submissions', {
                method: 'POST',
                body: JSON.stringify({
                    company_name: companyName.trim() || undefined,
                    role_title: roleTitle.trim() || undefined,
                    status,
                    submitted_on: submittedOn || undefined,
                    jd_url: jdUrl.trim() || undefined,
                    jd_text: jdText || undefined,
                    notes: notes || undefined,
                }),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            const created = await r.json();
            reset();
            onCreated(created.id);
        }
        catch (err) {
            setError(String(err));
        }
        finally {
            setSaving(false);
        }
    }
    return (_jsx(Modal, { show: show, onHide: onHide, size: "lg", children: _jsxs(Form, { onSubmit: handleSubmit, children: [_jsx(Modal.Header, { closeButton: true, children: _jsx(Modal.Title, { children: "New submission" }) }), _jsxs(Modal.Body, { children: [error && _jsx(Alert, { variant: "danger", children: error }), _jsxs(Row, { className: "g-3", children: [_jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Company" }), _jsx(Form.Control, { list: "companies-list", value: companyName, onChange: (e) => setCompanyName(e.target.value), placeholder: "Existing or new company" }), _jsx("datalist", { id: "companies-list", children: companies.map((c) => (_jsx("option", { value: c.name }, c.id))) })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Role title" }), _jsx(Form.Control, { value: roleTitle, onChange: (e) => setRoleTitle(e.target.value) })] }) }), _jsx(Col, { md: 4, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Status" }), _jsx(Form.Select, { value: status, onChange: (e) => setStatus(e.target.value), children: STATUSES.map((s) => (_jsx("option", { value: s.short_name, children: s.label }, s.short_name))) })] }) }), _jsx(Col, { md: 4, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Submitted on" }), _jsx(Form.Control, { type: "date", value: submittedOn, onChange: (e) => setSubmittedOn(e.target.value) })] }) }), _jsx(Col, { md: 4, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "JD URL" }), _jsx(Form.Control, { type: "url", value: jdUrl, onChange: (e) => setJdUrl(e.target.value), placeholder: "https://..." })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "JD text" }), _jsx(Form.Control, { as: "textarea", rows: 6, value: jdText, onChange: (e) => setJdText(e.target.value), placeholder: "Paste the job description body here" }), _jsx(Form.Text, { className: "text-muted", children: "Archived to S3 on save. Used for AI tailoring later." })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Notes" }), _jsx(Form.Control, { as: "textarea", rows: 3, value: notes, onChange: (e) => setNotes(e.target.value) })] }) })] })] }), _jsxs(Modal.Footer, { children: [_jsx(Button, { variant: "secondary", onClick: onHide, disabled: saving, children: "Cancel" }), _jsx(Button, { type: "submit", disabled: saving, children: saving ? 'Saving…' : 'Create submission' })] })] }) }));
}
