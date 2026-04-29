import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Form, Button, Table, Spinner, Alert, Card } from 'react-bootstrap';
import { useApi } from '../api/client';
function toEdits(types) {
    return Object.fromEntries(types.map((t) => [
        t.id,
        {
            daily: t.daily?.toString() ?? '',
            weekly: t.weekly?.toString() ?? '',
        },
    ]));
}
export default function Targets() {
    const apiFetch = useApi();
    const [types, setTypes] = useState(null);
    const [edits, setEdits] = useState({});
    const [error, setError] = useState(null);
    const [saving, setSaving] = useState(false);
    const [savedAt, setSavedAt] = useState(null);
    useEffect(() => {
        apiFetch('/targets')
            .then((r) => {
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            return r.json();
        })
            .then((d) => {
            setTypes(d.types);
            setEdits(toEdits(d.types));
        })
            .catch((e) => setError(String(e)));
    }, [apiFetch]);
    function setField(typeId, cadence, value) {
        setEdits((prev) => ({
            ...prev,
            [typeId]: { ...prev[typeId], [cadence]: value },
        }));
    }
    async function handleSave(e) {
        e.preventDefault();
        if (!types)
            return;
        setSaving(true);
        setError(null);
        const goals = [];
        for (const t of types) {
            for (const cadence of ['daily', 'weekly']) {
                const raw = edits[t.id]?.[cadence] ?? '';
                if (raw === '')
                    continue;
                const n = Number(raw);
                if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) {
                    setError(`${t.description} ${cadence}: must be a non-negative integer`);
                    setSaving(false);
                    return;
                }
                goals.push({ target_type_id: t.id, cadence, goal_count: n });
            }
        }
        try {
            const r = await apiFetch('/targets', {
                method: 'PUT',
                body: JSON.stringify({ goals }),
            });
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            const d = await r.json();
            setTypes(d.types);
            setEdits(toEdits(d.types));
            setSavedAt(new Date());
        }
        catch (err) {
            setError(String(err));
        }
        finally {
            setSaving(false);
        }
    }
    if (error && !types)
        return _jsx(Alert, { variant: "danger", children: error });
    if (!types)
        return _jsx(Spinner, { animation: "border", size: "sm" });
    return (_jsxs(_Fragment, { children: [_jsx("h3", { className: "mb-4", children: "Targets" }), _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Subtitle, { className: "text-muted mb-3", children: "Daily and weekly goals for each activity type. Leave blank to clear." }), _jsxs(Form, { onSubmit: handleSave, children: [_jsxs(Table, { responsive: true, borderless: true, className: "align-middle", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Activity" }), _jsx("th", { style: { width: 160 }, children: "Daily goal" }), _jsx("th", { style: { width: 160 }, children: "Weekly goal" })] }) }), _jsx("tbody", { children: types.map((t) => (_jsxs("tr", { children: [_jsx("td", { children: t.description }), _jsx("td", { children: _jsx(Form.Control, { type: "number", min: 0, value: edits[t.id]?.daily ?? '', onChange: (e) => setField(t.id, 'daily', e.target.value) }) }), _jsx("td", { children: _jsx(Form.Control, { type: "number", min: 0, value: edits[t.id]?.weekly ?? '', onChange: (e) => setField(t.id, 'weekly', e.target.value) }) })] }, t.id))) })] }), error && _jsx(Alert, { variant: "danger", children: error }), _jsxs("div", { className: "d-flex align-items-center gap-3", children: [_jsx(Button, { type: "submit", disabled: saving, children: saving ? 'Saving…' : 'Save targets' }), savedAt && (_jsxs("span", { className: "text-muted small", children: ["Saved at ", savedAt.toLocaleTimeString()] }))] })] })] }) })] }));
}
