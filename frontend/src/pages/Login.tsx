import { Container, Card, Button } from 'react-bootstrap';
import { useAuth } from 'react-oidc-context';

export default function Login() {
  const auth = useAuth();
  return (
    <Container className="mt-5" style={{ maxWidth: 480 }}>
      <Card>
        <Card.Body className="text-center">
          <Card.Title>Job Tracker</Card.Title>
          <Card.Text className="text-muted">Sign in to continue.</Card.Text>
          <Button onClick={() => auth.signinRedirect()}>
            Sign in with Cognito
          </Button>
        </Card.Body>
      </Card>
    </Container>
  );
}