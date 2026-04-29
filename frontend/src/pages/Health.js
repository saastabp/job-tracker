import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { Container, Card, Spinner, Alert } from 'react-bootstrap';
import { useApi } from '../api/client';
export default function Health() {
    const apiFetch = useApi();
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    useEffect(() => {
        apiFetch('/health')
            .then((r) => {
            if (!r.ok)
                throw new Error(`HTTP ${r.status}`);
            return r.json();
        })
            .then(setData)
            .catch((e) => setError(String(e)));
    }, [apiFetch]);
    return (_jsx(Container, { className: "mt-4", children: _jsx(Card, { children: _jsxs(Card.Body, { children: [_jsx(Card.Title, { children: "Health" }), _jsx(Card.Subtitle, { className: "mb-3 text-muted", children: "Round-trip: SPA \u2192 API Gateway (JWT) \u2192 Lambda in VPC \u2192 RDS via IAM auth" }), error && _jsx(Alert, { variant: "danger", children: error }), !data && !error && _jsx(Spinner, { animation: "border", size: "sm" }), data ? (_jsx("pre", { className: "mb-0", children: JSON.stringify(data, null, 2) })) : null] }) }) }));
}
