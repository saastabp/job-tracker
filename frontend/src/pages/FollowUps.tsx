import { useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Form,
  Spinner,
  Table,
} from 'react-bootstrap';
import { Link } from 'react-router-dom';
import { useApi } from '../api/client';

interface FollowUpRow {
  id: number;
  submission_id: number;
  due_at: string | null;
  actioned_at: string | null;
  notified_at: string | null;
  notes: string | null;
  auto_created: boolean;
  role_title: string | null;
  company_name: string | null;
  status: string;
}

export default function FollowUps() {
  const apiFetch = useApi();
  const [rows, setRows] = useState<FollowUpRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingOnly, setPendingOnly] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function load() {
    try {
      const qs = pendingOnly ? '?pending=1' : '';
      const r = await apiFetch(`/follow-ups${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows(await r.json());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingOnly]);

  async function markActioned(id: number) {
    setBusyId(id);
    try {
      const r = await apiFetch(`/follow-ups/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ actioned: true }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(id: number) {
    if (!confirm('Delete this follow-up?')) return;
    setBusyId(id);
    try {
      const r = await apiFetch(`/follow-ups/${id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  if (error && !rows) return <Alert variant="danger">{error}</Alert>;
  if (!rows) return <Spinner animation="border" size="sm" />;

  return (
    <>
      <div className="d-flex align-items-center mb-3">
        <h3 className="mb-0 me-3">Follow-ups</h3>
        <Form.Check
          type="switch"
          id="pending-only"
          label="Pending only"
          checked={pendingOnly}
          onChange={(e) => setPendingOnly(e.target.checked)}
        />
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      {rows.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">
            {pendingOnly
              ? 'No pending follow-ups. Nicely done.'
              : 'No follow-ups recorded yet.'}
          </Card.Body>
        </Card>
      ) : (
        <Card>
          <Table hover className="mb-0">
            <thead>
              <tr>
                <th>Due</th>
                <th>Submission</th>
                <th>Status</th>
                <th>Notes</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {r.due_at ?? '—'}
                    {r.auto_created && (
                      <Badge bg="light" text="dark" className="ms-2">
                        auto
                      </Badge>
                    )}
                  </td>
                  <td>
                    <Link to={`/submissions/${r.submission_id}`}>
                      {r.role_title ?? '(untitled)'}
                    </Link>
                    {r.company_name && (
                      <span className="text-muted"> at {r.company_name}</span>
                    )}
                  </td>
                  <td>{r.status}</td>
                  <td className="text-muted small">{r.notes ?? ''}</td>
                  <td className="text-end" style={{ whiteSpace: 'nowrap' }}>
                    {!r.actioned_at && (
                      <Button
                        size="sm"
                        variant="outline-success"
                        onClick={() => markActioned(r.id)}
                        disabled={busyId === r.id}
                        className="me-2"
                      >
                        Mark done
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="outline-danger"
                      onClick={() => remove(r.id)}
                      disabled={busyId === r.id}
                    >
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </>
  );
}
