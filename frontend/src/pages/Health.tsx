import { useEffect, useState } from 'react';
import { Container, Card, Spinner, Alert } from 'react-bootstrap';
import { useApi } from '../api/client';

export default function Health() {
  const apiFetch = useApi();
  const [data, setData] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch('/health')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [apiFetch]);

  return (
    <Container className="mt-4">
      <Card>
        <Card.Body>
          <Card.Title>Health</Card.Title>
          <Card.Subtitle className="mb-3 text-muted">
            Round-trip: SPA → API Gateway (JWT) → Lambda in VPC → RDS via IAM auth
          </Card.Subtitle>
          {error && <Alert variant="danger">{error}</Alert>}
          {!data && !error && <Spinner animation="border" size="sm" />}
          {data ? (
            <pre className="mb-0">{JSON.stringify(data, null, 2)}</pre>
          ) : null}
        </Card.Body>
      </Card>
    </Container>
  );
}