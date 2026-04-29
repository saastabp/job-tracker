import { useEffect, useState } from 'react';
import { Table, Spinner, Alert, Card, Badge } from 'react-bootstrap';
import { Link } from 'react-router-dom';
import { useApi } from '../api/client';

interface CompanyRow {
  id: number;
  name: string;
  notes: string | null;
  submission_count: number;
}

export default function Companies() {
  const apiFetch = useApi();
  const [rows, setRows] = useState<CompanyRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch('/companies')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setRows)
      .catch((e) => setError(String(e)));
  }, [apiFetch]);

  return (
    <>
      <h3 className="mb-4">Companies</h3>
      {error && <Alert variant="danger">{error}</Alert>}
      {!rows ? (
        <Spinner animation="border" size="sm" />
      ) : rows.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">
            No companies yet. Companies are created automatically when you log a
            submission.
          </Card.Body>
        </Card>
      ) : (
        <Table hover responsive className="align-middle">
          <thead>
            <tr>
              <th>Company</th>
              <th>Submissions</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id}>
                <td>
                  <Link to={`/companies/${c.id}`}>{c.name}</Link>
                </td>
                <td>
                  <Badge bg="secondary">{c.submission_count}</Badge>
                </td>
                <td className="text-truncate" style={{ maxWidth: 480 }}>
                  {c.notes ?? ''}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}