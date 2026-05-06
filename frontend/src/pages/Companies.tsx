import { useEffect, useState } from 'react';
import { Table, Spinner, Alert, Card, Badge, Button } from 'react-bootstrap';
import { Link } from 'react-router-dom';
import { useApi } from '../api/client';
import NewCompanyModal from '../components/NewCompanyModal';

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
  const [showNew, setShowNew] = useState(false);

  function load() {
    apiFetch('/companies')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setRows)
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiFetch]);

  return (
    <>
      <div className="d-flex align-items-center mb-4">
        <h3 className="mb-0">Companies</h3>
        <Button
          size="sm"
          variant="primary"
          className="ms-auto"
          onClick={() => setShowNew(true)}
        >
          New company
        </Button>
      </div>
      {error && <Alert variant="danger">{error}</Alert>}
      {!rows ? (
        <Spinner animation="border" size="sm" />
      ) : rows.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">
            No companies yet. Use “New company” above, or log a submission —
            companies referenced inline on a submission are created automatically.
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
      <NewCompanyModal
        show={showNew}
        onHide={() => setShowNew(false)}
        onCreated={() => {
          setShowNew(false);
          load();
        }}
      />
    </>
  );
}