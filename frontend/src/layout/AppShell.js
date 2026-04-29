import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Container, Navbar, Nav, Button } from 'react-bootstrap';
import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';
import { cognitoLogout } from '../auth/config';
const NAV_ITEMS = [
    { to: '/', label: 'Dashboard' },
    { to: '/submissions', label: 'Submissions' },
    { to: '/companies', label: 'Companies' },
    { to: '/resumes', label: 'Resumes' },
    { to: '/contacts', label: 'Contacts' },
    { to: '/targets', label: 'Targets' },
];
export default function AppShell() {
    const auth = useAuth();
    const username = auth.user?.profile.email ?? auth.user?.profile.sub;
    async function handleSignOut() {
        await auth.removeUser();
        cognitoLogout();
    }
    return (_jsxs("div", { className: "d-flex flex-column min-vh-100", children: [_jsxs(Navbar, { bg: "dark", variant: "dark", className: "px-3", children: [_jsx(Navbar.Brand, { children: "Job Tracker" }), _jsxs(Nav, { className: "ms-auto align-items-center", children: [_jsx(Navbar.Text, { className: "me-3", children: username }), _jsx(Button, { variant: "outline-light", size: "sm", onClick: handleSignOut, children: "Sign out" })] })] }), _jsxs("div", { className: "d-flex flex-grow-1", children: [_jsx("aside", { className: "bg-light border-end p-3", style: { width: 220, minHeight: '100%' }, children: _jsx(Nav, { className: "flex-column", children: NAV_ITEMS.map((item) => (_jsx(NavLink, { to: item.to, end: item.to === '/', className: ({ isActive }) => `nav-link ${isActive ? 'fw-bold text-primary' : 'text-dark'}`, children: item.label }, item.to))) }) }), _jsx("main", { className: "flex-grow-1", children: _jsx(Container, { fluid: true, className: "p-4", children: _jsx(Outlet, {}) }) })] })] }));
}
