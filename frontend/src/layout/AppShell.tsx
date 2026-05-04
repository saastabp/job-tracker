import { Container, Navbar, Nav, Button } from 'react-bootstrap';
import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from 'react-oidc-context';
import { cognitoLogout } from '../auth/config';

const NAV_ITEMS: { to: string; label: string }[] = [
  { to: '/', label: 'Dashboard' },
  { to: '/submissions', label: 'Submissions' },
  { to: '/companies', label: 'Companies' },
  { to: '/resumes', label: 'Resumes' },
  { to: '/contacts', label: 'Contacts' },
  { to: '/follow-ups', label: 'Follow-ups' },
  { to: '/targets', label: 'Targets' },
  { to: '/settings', label: 'Settings' },
];

export default function AppShell() {
  const auth = useAuth();
  const username =
    (auth.user?.profile.email as string | undefined) ?? auth.user?.profile.sub;

  async function handleSignOut() {
    await auth.removeUser();
    cognitoLogout();
  }

  return (
    <div className="d-flex flex-column min-vh-100">
      <Navbar bg="dark" variant="dark" className="px-3">
        <Navbar.Brand>Job Tracker</Navbar.Brand>
        <Nav className="ms-auto align-items-center">
          <Navbar.Text className="me-3">{username}</Navbar.Text>
          <Button variant="outline-light" size="sm" onClick={handleSignOut}>
            Sign out
          </Button>
        </Nav>
      </Navbar>
      <div className="d-flex flex-grow-1">
        <aside
          className="bg-light border-end p-3"
          style={{ width: 220, minHeight: '100%' }}
        >
          <Nav className="flex-column">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) =>
                  `nav-link ${isActive ? 'fw-bold text-primary' : 'text-dark'}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </Nav>
        </aside>
        <main className="flex-grow-1">
          <Container fluid className="p-4">
            <Outlet />
          </Container>
        </main>
      </div>
    </div>
  );
}