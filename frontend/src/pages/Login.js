import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Container, Card, Button } from 'react-bootstrap';
import { useAuth } from 'react-oidc-context';
export default function Login() {
    const auth = useAuth();
    return (_jsx(Container, { className: "mt-5", style: { maxWidth: 480 }, children: _jsx(Card, { children: _jsxs(Card.Body, { className: "text-center", children: [_jsx(Card.Title, { children: "Job Tracker" }), _jsx(Card.Text, { className: "text-muted", children: "Sign in to continue." }), _jsx(Button, { onClick: () => auth.signinRedirect(), children: "Sign in with Cognito" })] }) }) }));
}
