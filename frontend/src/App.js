import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Container, Navbar, Nav, Button } from 'react-bootstrap';
import { useAuth } from 'react-oidc-context';
import Health from './pages/Health';
import Login from './pages/Login';
import { cognitoLogout } from './auth/config';
export default function App() {
    const auth = useAuth();
    if (auth.isLoading) {
        return _jsx(Container, { className: "mt-5", children: "Loading\u2026" });
    }
    if (auth.error) {
        return (_jsxs(Container, { className: "mt-5", children: ["Auth error: ", auth.error.message] }));
    }
    const username = auth.user?.profile.email ?? auth.user?.profile.sub;
    async function handleSignOut() {
        await auth.removeUser();
        cognitoLogout();
    }
    return (_jsxs(_Fragment, { children: [_jsx(Navbar, { bg: "dark", variant: "dark", children: _jsxs(Container, { children: [_jsx(Navbar.Brand, { children: "Job Tracker" }), _jsx(Nav, { className: "ms-auto align-items-center", children: auth.isAuthenticated ? (_jsxs(_Fragment, { children: [_jsx(Navbar.Text, { className: "me-3", children: username }), _jsx(Button, { variant: "outline-light", size: "sm", onClick: handleSignOut, children: "Sign out" })] })) : (_jsx(Button, { variant: "outline-light", size: "sm", onClick: () => auth.signinRedirect(), children: "Sign in" })) })] }) }), auth.isAuthenticated ? _jsx(Health, {}) : _jsx(Login, {})] }));
}
