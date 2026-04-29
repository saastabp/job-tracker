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
import Health from './pages/Health';
import Login from './pages/Login';
import ComingSoon from './pages/ComingSoon';

export default function App() {
  const auth = useAuth();

  if (auth.isLoading) {
    return <Container className="mt-5">Loading…</Container>;
  }

  if (auth.error) {
    return (
      <Container className="mt-5">Auth error: {auth.error.message}</Container>
    );
  }

  if (!auth.isAuthenticated) {
    return <Login />;
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="submissions" element={<Submissions />} />
          <Route path="submissions/:id" element={<SubmissionDetail />} />
          <Route path="companies" element={<Companies />} />
          <Route path="companies/:id" element={<CompanyDetail />} />
          <Route
            path="resumes"
            element={<ComingSoon title="Resumes" slice="slice 04" />}
          />
          <Route
            path="contacts"
            element={<ComingSoon title="Contacts" slice="slice 05" />}
          />
          <Route path="targets" element={<Targets />} />
          <Route path="health" element={<Health />} />
          <Route
            path="*"
            element={<ComingSoon title="Not found" slice="404" />}
          />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}