import { Container, Navbar, Nav, Button } from 'react-bootstrap';
import { useAuth } from 'react-oidc-context';
import Health from './pages/Health';
import Login from './pages/Login';
import { cognitoLogout } from './auth/config';

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

  const username =
    (auth.user?.profile.email as string | undefined) ?? auth.user?.profile.sub;

  async function handleSignOut() {
    await auth.removeUser();
    cognitoLogout();
  }

  return (
    <>
      <Navbar bg="dark" variant="dark">
        <Container>
          <Navbar.Brand>Job Tracker</Navbar.Brand>
          <Nav className="ms-auto align-items-center">
            {auth.isAuthenticated ? (
              <>
                <Navbar.Text className="me-3">{username}</Navbar.Text>
                <Button
                  variant="outline-light"
                  size="sm"
                  onClick={handleSignOut}
                >
                  Sign out
                </Button>
              </>
            ) : (
              <Button
                variant="outline-light"
                size="sm"
                onClick={() => auth.signinRedirect()}
              >
                Sign in
              </Button>
            )}
          </Nav>
        </Container>
      </Navbar>
      {auth.isAuthenticated ? <Health /> : <Login />}
    </>
  );
}