import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Modal, Form, Button, Alert, Row, Col } from 'react-bootstrap';
import { useApi } from '../api/client';
import PdfDropZone, { PDF_MAX_BYTES, formatFileSize } from '../components/PdfDropZone';
export default function ResumeForm({ show, onHide, onCreated, suggestMaster }) {
    const apiFetch = useApi();
    const [title, setTitle] = useState('');
    const [summary, setSummary] = useState('');
    const [isMaster, setIsMaster] = useState(false);
    const [file, setFile] = useState(null);
    const [progress, setProgress] = useState(null);
    const [error, setError] = useState(null);
    useEffect(() => {
        if (show) {
            setTitle('');
            setSummary('');
            setIsMaster(false);
            setFile(null);
            setProgress(null);
            setError(null);
        }
    }, [show]);
    async function uploadFile(resumeId, f) {
        setProgress('Requesting upload URL…');
        const urlResp = await apiFetch(`/resumes/${resumeId}/upload-url`, {
            method: 'POST',
            body: JSON.stringify({
                content_type: f.type,
                original_filename: f.name,
                content_length: f.size,
            }),
        });
        if (!urlResp.ok) {
            throw new Error(`upload-url failed: HTTP ${urlResp.status}: ${await urlResp.text()}`);
        }
        const { url } = await urlResp.json();
        setProgress(`Uploading ${f.name} (${formatFileSize(f.size)})…`);
        const putResp = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': f.type },
            body: f,
        });
        if (!putResp.ok)
            throw new Error(`S3 PUT failed: HTTP ${putResp.status}`);
    }
    async function handleSubmit(e) {
        e.preventDefault();
        setError(null);
        try {
            setProgress('Creating resume…');
            const r = await apiFetch('/resumes', {
                method: 'POST',
                body: JSON.stringify({
                    title: title.trim(),
                    summary: summary || undefined,
                    is_master: isMaster || undefined,
                }),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            const created = await r.json();
            if (file) {
                await uploadFile(created.id, file);
            }
            setProgress(null);
            onCreated(created.id);
        }
        catch (err) {
            setProgress(null);
            setError(String(err));
        }
    }
    const busy = progress !== null;
    return (_jsx(Modal, { show: show, onHide: busy ? undefined : onHide, size: "lg", backdrop: busy ? 'static' : true, children: _jsxs(Form, { onSubmit: handleSubmit, children: [_jsx(Modal.Header, { closeButton: !busy, children: _jsx(Modal.Title, { children: "New resume" }) }), _jsxs(Modal.Body, { children: [error && _jsx(Alert, { variant: "danger", children: error }), suggestMaster && (_jsx(Alert, { variant: "info", children: "This will be your first resume \u2014 it'll be marked as your master automatically." })), _jsxs(Row, { className: "g-3", children: [_jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Title" }), _jsx(Form.Control, { required: true, value: title, onChange: (e) => setTitle(e.target.value), placeholder: "e.g., Master \u2014 SRE / Platform Engineering", disabled: busy }), _jsx(Form.Text, { className: "text-muted", children: "How you'll recognize this resume in lists." })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "Summary" }), _jsx(Form.Control, { as: "textarea", rows: 6, value: summary, onChange: (e) => setSummary(e.target.value), placeholder: "Paste your master summary / professional statement here.", disabled: busy }), _jsx(Form.Text, { className: "text-muted", children: "Used as a voice/style exemplar for AI-tailored summaries later." })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "PDF (optional)" }), _jsx(PdfDropZone, { file: file, onFile: setFile, onError: setError, disabled: busy }), _jsxs(Form.Text, { className: "d-block mt-2 text-muted", children: ["PDF only, max ", formatFileSize(PDF_MAX_BYTES), ". You can upload later from the detail page."] })] }) }), !suggestMaster && (_jsx(Col, { md: 12, children: _jsx(Form.Check, { type: "checkbox", id: "resume-is-master", label: "Mark as master (replaces the current master)", checked: isMaster, onChange: (e) => setIsMaster(e.target.checked), disabled: busy }) }))] }), progress && (_jsx("div", { className: "text-muted small mt-3", children: progress }))] }), _jsxs(Modal.Footer, { children: [_jsx(Button, { variant: "secondary", onClick: onHide, disabled: busy, children: "Cancel" }), _jsx(Button, { type: "submit", disabled: busy || !title.trim(), children: busy ? 'Saving…' : 'Create resume' })] })] }) }));
}
