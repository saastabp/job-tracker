import { useCallback, useEffect, useState } from 'react';
import {
  Table,
  Form,
  Button,
  Row,
  Col,
  Spinner,
  Alert,
  Badge,
  Card,
} from 'react-bootstrap';
import { Link, useNavigate } from 'react-router-dom';
import { useApi } from '../api/client';
import ContactForm, { KINDS } from './ContactForm';

interface ContactRow {
  id: number;
  kind: string;
  name: string;
  email: string | null;
  linkedin_url: string | null;
  primary_method: string | null;
  company_id: number | null;
  company_name: string | null;
  outreach_count: number;
  last_outreach_at: string | null;
}

const KIND_VARIANTS: Record<string, string> = {
  personal: 'primary',
  recruiter: 'info',
};

export function KindBadge({ kind }: { kind: string }) {
  const label = KINDS.find((k) => k.short_name === kind)?.label ?? kind;
  return <Badge bg={KIND_VARIANTS[kind] ?? 'secondary'}>{label}</Badge>;
}

export default function Contacts() {
  const apiFetch = useApi();
  const navigate = useNavigate();
  const [rows, setRows] = useState<ContactRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [filterKind, setFilterKind] = useState('');

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (filterKind) params.set('kind', filterKind);
    const qs = params.toString();
    const path = qs ? `/contacts?${qs}` : '/contacts';
    try {
      const r = await apiFetch(path);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows(await r.json());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [apiFetch, filterKind]);

  useEffect(() => {
    load();
  }, [load]);

  function handleCreated(newId: number) {
    setShowForm(false);
    navigate(`/contacts/${newId}`);
  }

  return (
    <>
      <div className="d-flex align-items-center justify-content-between mb-4">
        <h3 className="mb-0">Contacts</h3>
        <Button onClick={() => setShowForm(true)}>+ New contact</Button>
      </div>

      <Card className="mb-3">
        <Card.Body>
          <Row className="g-2 align-items-end">
            <Col md={3}>
              <Form.Label className="small text-muted">Kind</Form.Label>
              <Form.Select
                value={filterKind}
                onChange={(e) => setFilterKind(e.target.value)}
              >
                <option value="">All</option>
                {KINDS.map((k) => (
                  <option key={k.short_name} value={k.short_name}>
                    {k.label}
                  </option>
                ))}
              </Form.Select>
            </Col>
          </Row>
        </Card.Body>
      </Card>

      {error && <Alert variant="danger">{error}</Alert>}
      {!rows ? (
        <Spinner animation="border" size="sm" />
      ) : rows.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">
            No contacts yet. Click <strong>+ New contact</strong> to add one.
          </Card.Body>
        </Card>
      ) : (
        <Table hover responsive className="align-middle">
          <thead>
            <tr>
              <th>Kind</th>
              <th>Name</th>
              <th>Company</th>
              <th>Last outreach</th>
              <th>Total</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                style={{ cursor: 'pointer' }}
                onClick={() => navigate(`/contacts/${row.id}`)}
              >
                <td>
                  <KindBadge kind={row.kind} />
                </td>
                <td>
                  <Link
                    to={`/contacts/${row.id}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {row.name}
                  </Link>
                </td>
                <td>
                  {row.company_id ? (
                    <Link
                      to={`/companies/${row.company_id}`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.company_name}
                    </Link>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td>
                  {row.last_outreach_at ? (
                    row.last_outreach_at.slice(0, 10)
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td>
                  <Badge bg="secondary">{row.outreach_count}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <ContactForm
        show={showForm}
        onHide={() => setShowForm(false)}
        onCreated={handleCreated}
      />
    </>
  );
}