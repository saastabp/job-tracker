import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Modal, Form, Button, Alert, Row, Col } from 'react-bootstrap';
import { useApi } from '../api/client';
export const KINDS = [
    { short_name: 'personal', label: 'Personal' },
    { short_name: 'recruiter', label: 'Recruiter' },
];
export const METHODS = [
    { short_name: 'email', label: 'Email' },
    { short_name: 'linkedin', label: 'LinkedIn' },
    { short_name: 'phone', label: 'Phone' },
    { short_name: 'in_person', label: 'In person' },
    { short_name: 'other', label: 'Other' },
];
export const DIRECTIONS = [
    { short_name: 'outbound', label: 'Outbound (I reached out)' },
    { short_name: 'inbound', label: 'Inbound (they reached out)' },
];
export default function ContactForm({ show, onHide, onCreated, defaultKind = 'personal', }) {
    const apiFetch = useApi();
    const [companies, setCompanies] = useState([]);
    const [name, setName] = useState('');
    const [kind, setKind] = useState(defaultKind);
    const [email, setEmail] = useState('');
    const [phone, setPhone] = useState('');
    const [linkedinUrl, setLinkedinUrl] = useState('');
    const [primaryMethod, setPrimaryMethod] = useState('');
    const [companyId, setCompanyId] = useState('');
    const [notes, setNotes] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    useEffect(() => {
        if (!show)
            return;
        setKind(defaultKind);
        apiFetch('/companies')
            .then((r) => (r.ok ? r.json() : []))
            .then((rows) => setCompanies(rows.map((c) => ({ id: c.id, name: c.name }))))
            .catch(() => setCompanies([]));
    }, [show, defaultKind, apiFetch]);
    function reset() {
        setName('');
        setKind(defaultKind);
        setEmail('');
        setPhone('');
        setLinkedinUrl('');
        setPrimaryMethod('');
        setCompanyId('');
        setNotes('');
        setError(null);
    }
    async function handleSubmit(e) {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const r = await apiFetch('/contacts', {
                method: 'POST',
                body: JSON.stringify({
                    name: name.trim(),
                    kind,
                    email: email.trim() || undefined,
                    phone: phone.trim() || undefined,
                    linkedin_url: linkedinUrl.trim() || undefined,
                    primary_method: primaryMethod || undefined,
                    company_id: companyId ? Number(companyId) : undefined,
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
    return (_jsx(Modal, { show: show, onHide: onHide, size: "lg", children: _jsxs(Form, { onSubmit: handleSubmit, children: [_jsx(Modal.Header, { closeButton: true, children: _jsx(Modal.Title, { children: "New contact" }) }), _jsxs(Modal.Body, { children: [error && _jsx(Alert, { variant: "danger", children: error }), _jsxs(Row, { className: "g-3", children: [_jsx(Col, { md: 8, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Name" }), _jsx(Form.Control, { required: true, value: name, onChange: (e) => setName(e.target.value) })] }) }), _jsx(Col, { md: 4, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Kind" }), _jsx(Form.Select, { value: kind, onChange: (e) => setKind(e.target.value), children: KINDS.map((k) => (_jsx("option", { value: k.short_name, children: k.label }, k.short_name))) })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Email" }), _jsx(Form.Control, { type: "email", value: email, onChange: (e) => setEmail(e.target.value) })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Phone" }), _jsx(Form.Control, { type: "tel", value: phone, onChange: (e) => setPhone(e.target.value) })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "LinkedIn URL" }), _jsx(Form.Control, { type: "url", value: linkedinUrl, onChange: (e) => setLinkedinUrl(e.target.value), placeholder: "https://linkedin.com/in/\u2026" })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Preferred outreach method" }), _jsxs(Form.Select, { value: primaryMethod, onChange: (e) => setPrimaryMethod(e.target.value), children: [_jsx("option", { value: "", children: "\u2014 none \u2014" }), METHODS.map((m) => (_jsx("option", { value: m.short_name, children: m.label }, m.short_name)))] })] }) }), _jsx(Col, { md: 6, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Company" }), _jsxs(Form.Select, { value: companyId, onChange: (e) => setCompanyId(e.target.value), children: [_jsx("option", { value: "", children: "\u2014 none \u2014" }), companies.map((c) => (_jsx("option", { value: c.id, children: c.name }, c.id)))] })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Notes" }), _jsx(Form.Control, { as: "textarea", rows: 3, value: notes, onChange: (e) => setNotes(e.target.value) })] }) })] })] }), _jsxs(Modal.Footer, { children: [_jsx(Button, { variant: "secondary", onClick: onHide, disabled: saving, children: "Cancel" }), _jsx(Button, { type: "submit", disabled: saving || !name.trim(), children: saving ? 'Saving…' : 'Create contact' })] })] }) }));
}
