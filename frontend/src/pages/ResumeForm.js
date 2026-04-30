import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Modal, Form, Button, Alert, Row, Col, Badge } from 'react-bootstrap';
import { useApi } from '../api/client';
import PdfDropZone, { PDF_MAX_BYTES, formatFileSize } from '../components/PdfDropZone';
export default function ResumeForm({ show, onHide, onCreated, suggestMaster }) {
    const apiFetch = useApi();
    const [title, setTitle] = useState('');
    const [summary, setSummary] = useState('');
    const [titleOrigin, setTitleOrigin] = useState('user');
    const [summaryOrigin, setSummaryOrigin] = useState('user');
    const [isMaster, setIsMaster] = useState(false);
    const [file, setFile] = useState(null);
    const [progress, setProgress] = useState(null);
    const [aiBusy, setAiBusy] = useState(false);
    const [aiHint, setAiHint] = useState(null);
    const [error, setError] = useState(null);
    useEffect(() => {
        if (show) {
            setTitle('');
            setSummary('');
            setTitleOrigin('user');
            setSummaryOrigin('user');
            setIsMaster(false);
            setFile(null);
            setProgress(null);
            setAiBusy(false);
            setAiHint(null);
            setError(null);
        }
    }, [show]);
    async function handleFileChange(f) {
        setFile(f);
        if (!f)
            return;
        if (title.trim() || summary.trim()) {
            // Don't clobber what the user has already typed.
            setAiHint('PDF set. Title/summary already filled — AI auto-prefill skipped.');
            return;
        }
        setAiBusy(true);
        setAiHint('Reading PDF…');
        try {
            // Lazy import: pdfjs-dist + its worker only ship when a user actually
            // drops a PDF, keeping the initial SPA bundle lean.
            const { extractPdfText } = await import('../components/pdfText');
            const text = await extractPdfText(f);
            if (!text.trim())
                throw new Error('PDF appeared to contain no extractable text');
            setAiHint('Asking AI for a title + summary…');
            const r = await apiFetch('/ai/mine-resume', {
                method: 'POST',
                body: JSON.stringify({ text }),
            });
            if (!r.ok) {
                const txt = await r.text();
                throw new Error(`HTTP ${r.status}: ${txt}`);
            }
            const { title: aiTitle, summary: aiSummary } = await r.json();
            setTitle(aiTitle);
            setSummary(aiSummary);
            setTitleOrigin('ai');
            setSummaryOrigin('ai');
            setAiHint('Auto-filled from your PDF — review and edit before saving.');
        }
        catch (err) {
            setAiHint(`Couldn't auto-fill from PDF (${String(err)}). Fill in manually.`);
        }
        finally {
            setAiBusy(false);
        }
    }
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
    const busy = progress !== null || aiBusy;
    return (_jsx(Modal, { show: show, onHide: busy ? undefined : onHide, size: "lg", backdrop: busy ? 'static' : true, children: _jsxs(Form, { onSubmit: handleSubmit, children: [_jsx(Modal.Header, { closeButton: !busy, children: _jsx(Modal.Title, { children: "New resume" }) }), _jsxs(Modal.Body, { children: [error && _jsx(Alert, { variant: "danger", children: error }), suggestMaster && (_jsx(Alert, { variant: "info", children: "This will be your first resume \u2014 it'll be marked as your master automatically." })), _jsxs(Row, { className: "g-3", children: [_jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsxs(Form.Label, { className: "d-flex align-items-center gap-2", children: ["Title", titleOrigin === 'ai' && (_jsx(Badge, { bg: "info", children: "auto-filled \u2014 edit if needed" }))] }), _jsx(Form.Control, { required: true, value: title, onChange: (e) => {
                                                    setTitle(e.target.value);
                                                    setTitleOrigin('user');
                                                }, placeholder: "e.g., Master \u2014 SRE / Platform Engineering", disabled: busy }), _jsx(Form.Text, { className: "text-muted", children: "How you'll recognize this resume in lists." })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsxs(Form.Label, { className: "d-flex align-items-center gap-2", children: ["Summary", summaryOrigin === 'ai' && (_jsx(Badge, { bg: "info", children: "auto-filled \u2014 edit if needed" }))] }), _jsx(Form.Control, { as: "textarea", rows: 6, value: summary, onChange: (e) => {
                                                    setSummary(e.target.value);
                                                    setSummaryOrigin('user');
                                                }, placeholder: "Paste your master summary / professional statement here.", disabled: busy }), _jsx(Form.Text, { className: "text-muted", children: "Used as a voice/style exemplar for AI-tailored summaries later." })] }) }), _jsx(Col, { md: 12, children: _jsxs(Form.Group, { children: [_jsx(Form.Label, { children: "PDF (optional)" }), _jsx(PdfDropZone, { file: file, onFile: handleFileChange, onError: setError, disabled: busy }), _jsxs(Form.Text, { className: "d-block mt-2 text-muted", children: ["PDF only, max ", formatFileSize(PDF_MAX_BYTES), ". You can upload later from the detail page. Dropping a PDF here while title/summary are blank will ask AI for a starting point."] }), aiHint && (_jsx("div", { className: "text-muted small mt-2", children: aiHint }))] }) }), !suggestMaster && (_jsx(Col, { md: 12, children: _jsx(Form.Check, { type: "checkbox", id: "resume-is-master", label: "Mark as master (replaces the current master)", checked: isMaster, onChange: (e) => setIsMaster(e.target.checked), disabled: busy }) }))] }), progress && (_jsx("div", { className: "text-muted small mt-3", children: progress }))] }), _jsxs(Modal.Footer, { children: [_jsx(Button, { variant: "secondary", onClick: onHide, disabled: busy, children: "Cancel" }), _jsx(Button, { type: "submit", disabled: busy || !title.trim(), children: busy ? 'Saving…' : 'Create resume' })] })] }) }));
}
