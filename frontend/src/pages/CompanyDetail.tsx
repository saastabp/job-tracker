import { useCallback, useEffect, useState } from 'react';
import {
  Card,
  Form,
  Button,
  Spinner,
  Alert,
  Table,
  Row,
  Col,
} from 'react-bootstrap';
import { Link, useParams } from 'react-router-dom';
import { useApi } from '../api/client';
import { StatusBadge } from './Submissions';

interface CompanyDetailData {
  id: number;
  name: string;
  notes: string | null;
  submission_count: number;
  submissions: {
    id: number;
    role_title: string | null;
    submitted_on: string | null;
    status: string;
    notes: string | null;
  }[];
}

export default function CompanyDetail() {
  const apiFetch = useApi();
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<CompanyDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editNotes, setEditNotes] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await apiFetch(`/companies/${id}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d: CompanyDetailData = await r.json();
      setData(d);
      setEditName(d.name);
      setEditNotes(d.notes ?? '');
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [apiFetch, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave() {
    setSaving(true);
    try {
      const r = await apiFetch(`/companies/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ name: editName, notes: editNotes || null }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const d: CompanyDetailData = await r.json();
      setData(d);
      setEditName(d.name);
      setEditNotes(d.notes ?? '');
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  if (error && !data) return <Alert variant="danger">{error}</Alert>;
  if (!data) return <Spinner animation="border" size="sm" />;

  const dirty = editName !== data.name || editNotes !== (data.notes ?? '');

  return (
    <>
      <div className="d-flex align-items-center mb-3">
        <Link to="/companies" className="me-3">
          ← Back
        </Link>
        <h3 className="mb-0">{data.name}</h3>
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      <Card className="mb-3">
        <Card.Body>
          <Row className="g-3">
            <Col md={6}>
              <Form.Group>
                <Form.Label>Name</Form.Label>
                <Form.Control
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={12}>
              <Form.Group>
                <Form.Label>Notes</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={3}
                  value={editNotes}
                  onChange={(e) => setEditNotes(e.target.value)}
                />
              </Form.Group>
            </Col>
          </Row>
          <div className="mt-3">
            <Button onClick={handleSave} disabled={!dirty || saving}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </Card.Body>
      </Card>

      <h5 className="mt-4">Submissions ({data.submission_count})</h5>
      {data.submissions.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">No submissions yet.</Card.Body>
        </Card>
      ) : (
        <Table hover responsive className="align-middle">
          <thead>
            <tr>
              <th>Date</th>
              <th>Role</th>
              <th>Status</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {data.submissions.map((s) => (
              <tr key={s.id}>
                <td>{s.submitted_on ?? '—'}</td>
                <td>
                  <Link to={`/submissions/${s.id}`}>
                    {s.role_title ?? '(untitled)'}
                  </Link>
                </td>
                <td>
                  <StatusBadge status={s.status} />
                </td>
                <td className="text-truncate" style={{ maxWidth: 320 }}>
                  {s.notes ?? ''}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}