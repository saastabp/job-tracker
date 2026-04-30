import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Modal, Button, Alert, Form } from 'react-bootstrap';
import { useApi } from '../api/client';
import PdfDropZone, { PDF_MAX_BYTES, formatFileSize } from '../components/PdfDropZone';
export default function ResumeUploadModal({ show, onHide, resumeId, replacing, onUploaded, }) {
    const apiFetch = useApi();
    const [file, setFile] = useState(null);
    const [error, setError] = useState(null);
    const [progress, setProgress] = useState(null);
    useEffect(() => {
        if (show) {
            setFile(null);
            setError(null);
            setProgress(null);
        }
    }, [show]);
    async function handleUpload() {
        if (!file)
            return;
        setError(null);
        try {
            setProgress('Requesting upload URL…');
            const urlResp = await apiFetch(`/resumes/${resumeId}/upload-url`, {
                method: 'POST',
                body: JSON.stringify({
                    content_type: file.type,
                    original_filename: file.name,
                    content_length: file.size,
                }),
            });
            if (!urlResp.ok) {
                throw new Error(`upload-url failed: HTTP ${urlResp.status}: ${await urlResp.text()}`);
            }
            const { url } = await urlResp.json();
            setProgress(`Uploading ${file.name} (${formatFileSize(file.size)})…`);
            const putResp = await fetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': file.type },
                body: file,
            });
            if (!putResp.ok)
                throw new Error(`S3 PUT failed: HTTP ${putResp.status}`);
            setProgress(null);
            onUploaded();
            onHide();
        }
        catch (err) {
            setProgress(null);
            setError(String(err));
        }
    }
    const uploading = progress !== null;
    return (_jsxs(Modal, { show: show, onHide: uploading ? undefined : onHide, backdrop: uploading ? 'static' : true, children: [_jsx(Modal.Header, { closeButton: !uploading, children: _jsx(Modal.Title, { children: replacing ? 'Replace PDF' : 'Upload PDF' }) }), _jsxs(Modal.Body, { children: [error && _jsx(Alert, { variant: "danger", children: error }), _jsx(PdfDropZone, { file: file, onFile: setFile, onError: setError, disabled: uploading }), _jsxs(Form.Text, { className: "d-block mt-3 text-muted", children: ["PDF only, max ", formatFileSize(PDF_MAX_BYTES), "."] }), progress && (_jsx("div", { className: "mt-3 text-muted small", children: progress }))] }), _jsxs(Modal.Footer, { children: [_jsx(Button, { variant: "secondary", onClick: onHide, disabled: uploading, children: "Cancel" }), _jsx(Button, { onClick: handleUpload, disabled: !file || uploading, children: uploading ? 'Uploading…' : 'Upload' })] })] }));
}
