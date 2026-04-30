import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Container } from 'react-bootstrap';
import { useAuth } from 'react-oidc-context';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import AppShell from './layout/AppShell';
import Dashboard from './pages/Dashboard';
import Targets from './pages/Targets';
import Submissions from './pages/Submissions';
import SubmissionDetail from './pages/SubmissionDetail';
import Companies from './pages/Companies';
import CompanyDetail from './pages/CompanyDetail';
import Resumes from './pages/Resumes';
import ResumeDetail from './pages/ResumeDetail';
import Health from './pages/Health';
import Login from './pages/Login';
import ComingSoon from './pages/ComingSoon';
export default function App() {
    const auth = useAuth();
    if (auth.isLoading) {
        return _jsx(Container, { className: "mt-5", children: "Loading\u2026" });
    }
    if (auth.error) {
        return (_jsxs(Container, { className: "mt-5", children: ["Auth error: ", auth.error.message] }));
    }
    if (!auth.isAuthenticated) {
        return _jsx(Login, {});
    }
    return (_jsx(BrowserRouter, { children: _jsx(Routes, { children: _jsxs(Route, { element: _jsx(AppShell, {}), children: [_jsx(Route, { index: true, element: _jsx(Dashboard, {}) }), _jsx(Route, { path: "submissions", element: _jsx(Submissions, {}) }), _jsx(Route, { path: "submissions/:id", element: _jsx(SubmissionDetail, {}) }), _jsx(Route, { path: "companies", element: _jsx(Companies, {}) }), _jsx(Route, { path: "companies/:id", element: _jsx(CompanyDetail, {}) }), _jsx(Route, { path: "resumes", element: _jsx(Resumes, {}) }), _jsx(Route, { path: "resumes/:id", element: _jsx(ResumeDetail, {}) }), _jsx(Route, { path: "contacts", element: _jsx(ComingSoon, { title: "Contacts", slice: "slice 05" }) }), _jsx(Route, { path: "targets", element: _jsx(Targets, {}) }), _jsx(Route, { path: "health", element: _jsx(Health, {}) }), _jsx(Route, { path: "*", element: _jsx(ComingSoon, { title: "Not found", slice: "404" }) })] }) }) }));
}
