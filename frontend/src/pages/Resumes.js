import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { Table, Button, Spinner, Alert, Badge, Card, Form, } from 'react-bootstrap';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../api/client';
import ResumeForm from './ResumeForm';
export default function Resumes() {
    const apiFetch = useApi();
    const navigate = useNavigate();
    const [rows, setRows] = useState(null);
    const [error, setError] = useState(null);
    const [showForm, setShowForm] = useState(false);
    const [showDeleted, setShowDeleted] = useState(false);
    const [busyRow, setBusyRow] = useState(null);
    const load = useCallback(async () => {
        try {
            const path = showDeleted
                ? '/resumes?include_deleted=true'
                : '/resumes';
            const r = await apiFetch(path);
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            setRows(await r.json());
            setError(null);
        }
        catch (e) {
            setError(String(e));
        }
    }, [apiFetch, showDeleted]);
    useEffect(() => {
        load();
    }, [load]);
    function handleCreated(newId) {
        setShowForm(false);
        navigate(`/resumes/${newId}`);
    }
    async function restoreRow(id) {
        setBusyRow(id);
        try {
            const r = await apiFetch(`/resumes/${id}/restore`, { method: 'POST' });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            await load();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusyRow(null);
        }
    }
    async function purgeRow(id, title) {
        if (!window.confirm(`Permanently delete "${title ?? `resume #${id}`}"? This cannot be undone — ` +
            `the PDF and all metadata will be removed, and any submissions linked to this ` +
            `resume will lose the link.`)) {
            return;
        }
        setBusyRow(id);
        try {
            const r = await apiFetch(`/resumes/${id}/purge`, { method: 'POST' });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}: ${await r.text()}`);
            await load();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusyRow(null);
        }
    }
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "d-flex align-items-center justify-content-between mb-4", children: [_jsx("h3", { className: "mb-0", children: "Resumes" }), _jsxs("div", { className: "d-flex align-items-center gap-3", children: [_jsx(Form.Check, { type: "switch", id: "show-deleted-switch", label: "Show deleted", checked: showDeleted, onChange: (e) => setShowDeleted(e.target.checked) }), _jsx(Button, { onClick: () => setShowForm(true), children: "+ New resume" })] })] }), error && _jsx(Alert, { variant: "danger", children: error }), !rows ? (_jsx(Spinner, { animation: "border", size: "sm" })) : rows.length === 0 ? (_jsx(Card, { children: _jsx(Card.Body, { className: "text-muted", children: showDeleted
                        ? 'No resumes (including deleted ones).'
                        : 'No resumes yet. Click + New resume to create one. Your first resume becomes your master automatically.' }) })) : (_jsxs(Table, { hover: true, responsive: true, className: "align-middle", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Title" }), _jsx("th", { children: "Master" }), _jsx("th", { children: "File" }), _jsx("th", { children: "Submissions" }), showDeleted && _jsx("th", { children: "Status" }), _jsx("th", {})] }) }), _jsx("tbody", { children: rows.map((row) => {
                            const muted = row.is_deleted;
                            const rowStyle = {
                                cursor: muted ? 'default' : 'pointer',
                                opacity: muted ? 0.55 : 1,
                            };
                            return (_jsxs("tr", { style: rowStyle, onClick: () => {
                                    if (!muted)
                                        navigate(`/resumes/${row.id}`);
                                }, children: [_jsx("td", { children: row.title || (_jsx("span", { className: "text-muted", children: "Untitled" })) }), _jsx("td", { children: row.is_master ? (_jsx(Badge, { bg: "primary", children: "master" })) : (_jsx("span", { className: "text-muted", children: "\u2014" })) }), _jsx("td", { children: row.has_file ? (_jsx("span", { children: row.original_filename ?? 'attached' })) : (_jsx("span", { className: "text-muted", children: "no file" })) }), _jsx("td", { children: row.submission_count }), showDeleted && (_jsx("td", { children: row.is_deleted ? (_jsxs(Badge, { bg: "warning", text: "dark", children: ["deleted ", row.deleted_at?.slice(0, 10)] })) : (_jsx(Badge, { bg: "success", children: "live" })) })), _jsx("td", { className: "text-end", children: row.is_deleted && (_jsxs("span", { onClick: (e) => e.stopPropagation(), children: [_jsx(Button, { size: "sm", variant: "outline-primary", className: "me-2", disabled: busyRow === row.id, onClick: () => restoreRow(row.id), children: "Restore" }), _jsx(Button, { size: "sm", variant: "outline-danger", disabled: busyRow === row.id, onClick: () => purgeRow(row.id, row.title), children: "Delete forever" })] })) })] }, row.id));
                        }) })] })), _jsx(ResumeForm, { show: showForm, onHide: () => setShowForm(false), onCreated: handleCreated, suggestMaster: rows !== null && rows.filter((r) => !r.is_deleted).length === 0 })] }));
}
