import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { useRef, useState } from 'react';
import { Button, Form } from 'react-bootstrap';
export const PDF_MAX_BYTES = 5 * 1024 * 1024;
export function formatFileSize(bytes) {
    if (bytes < 1024)
        return `${bytes} B`;
    if (bytes < 1024 * 1024)
        return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
export function validatePdf(f) {
    if (f.type !== 'application/pdf') {
        return `Only PDF uploads supported (got ${f.type || 'unknown'})`;
    }
    if (f.size > PDF_MAX_BYTES) {
        return `File too large (${formatFileSize(f.size)}; max ${formatFileSize(PDF_MAX_BYTES)})`;
    }
    return null;
}
/**
 * Click-or-drop PDF picker. Validates the chosen file synchronously and
 * surfaces validation errors via ``onError``; the parent owns the error
 * display. Clearing the selection is the parent's job too — call
 * ``onFile(null)`` from outside to reset.
 */
export default function PdfDropZone({ file, onFile, onError, disabled }) {
    const inputRef = useRef(null);
    const [dragOver, setDragOver] = useState(false);
    function handleFile(f) {
        const err = validatePdf(f);
        if (err) {
            onError(err);
            return;
        }
        onFile(f);
    }
    function handleDrop(e) {
        e.preventDefault();
        e.stopPropagation();
        setDragOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f)
            handleFile(f);
    }
    function handleInputChange(e) {
        const f = e.target.files?.[0];
        if (f)
            handleFile(f);
        e.target.value = '';
    }
    const style = {
        border: `2px dashed ${dragOver ? '#0d6efd' : '#ced4da'}`,
        borderRadius: 6,
        padding: '2.5rem 1rem',
        textAlign: 'center',
        backgroundColor: dragOver ? '#e7f1ff' : '#f8f9fa',
        cursor: disabled ? 'not-allowed' : 'pointer',
        transition: 'background-color 0.15s, border-color 0.15s',
    };
    return (_jsxs(_Fragment, { children: [_jsx("div", { style: style, onClick: () => !disabled && inputRef.current?.click(), onDragOver: (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    if (!disabled)
                        setDragOver(true);
                }, onDragLeave: (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setDragOver(false);
                }, onDrop: disabled ? undefined : handleDrop, children: file ? (_jsxs(_Fragment, { children: [_jsx("div", { className: "fw-semibold", children: file.name }), _jsx("div", { className: "text-muted small", children: formatFileSize(file.size) }), _jsx("div", { className: "mt-2 small text-muted", children: "Drop a different file or click to choose another." })] })) : (_jsxs(_Fragment, { children: [_jsxs("div", { className: "mb-2", children: [_jsx("strong", { children: "Drop a PDF here" }), _jsx("span", { className: "text-muted", children: " or click to browse" })] }), _jsx(Button, { size: "sm", variant: "outline-primary", onClick: (e) => {
                                e.stopPropagation();
                                inputRef.current?.click();
                            }, disabled: disabled, children: "Browse\u2026" })] })) }), _jsx(Form.Control, { ref: inputRef, type: "file", accept: "application/pdf", onChange: handleInputChange, style: { display: 'none' } })] }));
}
