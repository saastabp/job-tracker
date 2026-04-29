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
import SubmissionForm, { STATUSES } from './SubmissionForm';

interface SubmissionRow {
  id: number;
  role_title: string | null;
  submitted_on: string | null;
  notes: string | null;
  company_id: number | null;
  company_name: string | null;
  status: string;
  jd_url: string | null;
}

interface CompanyOption {
  id: number;
  name: string;
}

const STATUS_VARIANTS: Record<string, string> = {
  applied: 'secondary',
  responded: 'info',
  interviewing: 'primary',
  offer: 'success',
  rejected: 'danger',
  ghosted: 'warning',
};

export function StatusBadge({ status }: { status: string }) {
  const label =
    STATUSES.find((s) => s.short_name === status)?.label ?? status;
  return (
    <Badge bg={STATUS_VARIANTS[status] ?? 'secondary'}>{label}</Badge>
  );
}

export default function Submissions() {
  const apiFetch = useApi();
  const navigate = useNavigate();
  const [rows, setRows] = useState<SubmissionRow[] | null>(null);
  const [companies, setCompanies] = useState<CompanyOption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [filterStatus, setFilterStatus] = useState('');
  const [filterCompany, setFilterCompany] = useState('');
  const [filterFrom, setFilterFrom] = useState('');
  const [filterTo, setFilterTo] = useState('');

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (filterStatus) params.set('status', filterStatus);
    if (filterCompany) params.set('company_id', filterCompany);
    if (filterFrom) params.set('from', filterFrom);
    if (filterTo) params.set('to', filterTo);
    const qs = params.toString();
    const path = qs ? `/submissions?${qs}` : '/submissions';
    try {
      const r = await apiFetch(path);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows(await r.json());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [apiFetch, filterStatus, filterCompany, filterFrom, filterTo]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    apiFetch('/companies')
      .then((r) => (r.ok ? r.json() : []))
      .then((cs) => setCompanies(cs.map((c: any) => ({ id: c.id, name: c.name }))))
      .catch(() => setCompanies([]));
  }, [apiFetch]);

  function handleCreated(newId: number) {
    setShowForm(false);
    navigate(`/submissions/${newId}`);
  }

  return (
    <>
      <div className="d-flex align-items-center justify-content-between mb-4">
        <h3 className="mb-0">Submissions</h3>
        <Button onClick={() => setShowForm(true)}>+ New submission</Button>
      </div>

      <Card className="mb-3">
        <Card.Body>
          <Row className="g-2 align-items-end">
            <Col md={3}>
              <Form.Label className="small text-muted">Status</Form.Label>
              <Form.Select
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value)}
              >
                <option value="">All</option>
                {STATUSES.map((s) => (
                  <option key={s.short_name} value={s.short_name}>
                    {s.label}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={3}>
              <Form.Label className="small text-muted">Company</Form.Label>
              <Form.Select
                value={filterCompany}
                onChange={(e) => setFilterCompany(e.target.value)}
              >
                <option value="">All</option>
                {companies.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={3}>
              <Form.Label className="small text-muted">From</Form.Label>
              <Form.Control
                type="date"
                value={filterFrom}
                onChange={(e) => setFilterFrom(e.target.value)}
              />
            </Col>
            <Col md={3}>
              <Form.Label className="small text-muted">To</Form.Label>
              <Form.Control
                type="date"
                value={filterTo}
                onChange={(e) => setFilterTo(e.target.value)}
              />
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
            No submissions yet. Click <strong>+ New submission</strong> to log one.
          </Card.Body>
        </Card>
      ) : (
        <Table hover responsive className="align-middle">
          <thead>
            <tr>
              <th>Date</th>
              <th>Company</th>
              <th>Role</th>
              <th>Status</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                style={{ cursor: 'pointer' }}
                onClick={() => navigate(`/submissions/${row.id}`)}
              >
                <td>{row.submitted_on ?? '—'}</td>
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
                <td>{row.role_title ?? <span className="text-muted">—</span>}</td>
                <td>
                  <StatusBadge status={row.status} />
                </td>
                <td className="text-truncate" style={{ maxWidth: 320 }}>
                  {row.notes ?? ''}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <SubmissionForm
        show={showForm}
        onHide={() => setShowForm(false)}
        onCreated={handleCreated}
      />
    </>
  );
}