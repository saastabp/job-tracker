import { useCallback, useEffect, useState } from 'react';
import {
  Card,
  Form,
  Button,
  Row,
  Col,
  Spinner,
  Alert,
  Collapse,
  Badge,
} from 'react-bootstrap';
import { Link, useParams } from 'react-router-dom';
import { useApi } from '../api/client';
import { STATUSES } from './SubmissionForm';
import { StatusBadge } from './Submissions';

interface JdSnapshot {
  id: number;
  s3_key: string;
  source_url: string | null;
  captured_at: string | null;
}

interface FollowUp {
  id: number;
  due_at: string | null;
  actioned_at: string | null;
  notes: string | null;
}

interface ResponseRow {
  id: number;
  received_at: string | null;
  from_email: string | null;
  subject: string | null;
  classification: string;
}

interface SubmissionDetail {
  id: number;
  role_title: string | null;
  submitted_on: string | null;
  notes: string | null;
  company_id: number | null;
  company_name: string | null;
  status: string;
  tailored_title: string | null;
  tailored_summary: string | null;
  jd_url: string | null;
  resume_id: number | null;
  resume_title: string | null;
  jd_snapshot: JdSnapshot | null;
  jd_text: string | null;
  follow_ups: FollowUp[];
  responses: ResponseRow[];
}

interface ResumeOption {
  id: number;
  title: string | null;
  is_master: boolean;
}

export default function SubmissionDetail() {
  const apiFetch = useApi();
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<SubmissionDetail | null>(null);
  const [resumes, setResumes] = useState<ResumeOption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showJd, setShowJd] = useState(false);
  const [savingField, setSavingField] = useState<string | null>(null);
  const [editNotes, setEditNotes] = useState('');
  const [editTailoredTitle, setEditTailoredTitle] = useState('');
  const [editTailoredSummary, setEditTailoredSummary] = useState('');
  const [editSubmittedOn, setEditSubmittedOn] = useState('');
  const [editJdUrl, setEditJdUrl] = useState('');
  const [editJdText, setEditJdText] = useState('');
  const [editingRoleTitle, setEditingRoleTitle] = useState(false);
  const [roleTitleDraft, setRoleTitleDraft] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await apiFetch(`/submissions/${id}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d: SubmissionDetail = await r.json();
      setData(d);
      setEditNotes(d.notes ?? '');
      setEditTailoredTitle(d.tailored_title ?? '');
      setEditTailoredSummary(d.tailored_summary ?? '');
      setEditSubmittedOn(d.submitted_on ?? '');
      setEditJdUrl(d.jd_url ?? '');
      setEditJdText(d.jd_text ?? '');
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [apiFetch, id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    apiFetch('/resumes')
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) =>
        setResumes(
          rows.map((r: any) => ({
            id: r.id,
            title: r.title,
            is_master: r.is_master,
          })),
        ),
      )
      .catch(() => setResumes([]));
  }, [apiFetch]);

  function startEditingRoleTitle() {
    setRoleTitleDraft(data?.role_title ?? '');
    setEditingRoleTitle(true);
  }

  async function commitRoleTitle() {
    if (!data) return;
    const next = roleTitleDraft.trim();
    const current = data.role_title ?? '';
    setEditingRoleTitle(false);
    if (next === current) return;
    await patchField('role_title', next || null, 'role_title');
  }

  async function patchField(field: string, value: unknown, label: string) {
    setSavingField(label);
    try {
      const r = await apiFetch(`/submissions/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ [field]: value }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const d: SubmissionDetail = await r.json();
      setData(d);
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingField(null);
    }
  }

  async function handleSave() {
    if (!data) return;
    const body: Record<string, unknown> = {};
    if (editNotes !== (data.notes ?? '')) body.notes = editNotes || null;
    if (editTailoredTitle !== (data.tailored_title ?? ''))
      body.tailored_title = editTailoredTitle || null;
    if (editTailoredSummary !== (data.tailored_summary ?? ''))
      body.tailored_summary = editTailoredSummary || null;
    if (editSubmittedOn !== (data.submitted_on ?? ''))
      body.submitted_on = editSubmittedOn || null;
    if (editJdUrl.trim() !== (data.jd_url ?? ''))
      body.jd_url = editJdUrl.trim() || null;
    if (editJdText !== (data.jd_text ?? '')) body.jd_text = editJdText;
    if (Object.keys(body).length === 0) return;

    setSavingField('save');
    try {
      const r = await apiFetch(`/submissions/${id}`, {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const d: SubmissionDetail = await r.json();
      setData(d);
      setEditNotes(d.notes ?? '');
      setEditTailoredTitle(d.tailored_title ?? '');
      setEditTailoredSummary(d.tailored_summary ?? '');
      setEditSubmittedOn(d.submitted_on ?? '');
      setEditJdUrl(d.jd_url ?? '');
      setEditJdText(d.jd_text ?? '');
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingField(null);
    }
  }

  if (error && !data) return <Alert variant="danger">{error}</Alert>;
  if (!data) return <Spinner animation="border" size="sm" />;

  return (
    <>
      <div className="d-flex align-items-center mb-3">
        <Link to="/submissions" className="me-3">
          ← Back
        </Link>
        {editingRoleTitle ? (
          <Form.Control
            autoFocus
            size="lg"
            value={roleTitleDraft}
            onChange={(e) => setRoleTitleDraft(e.target.value)}
            onBlur={commitRoleTitle}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitRoleTitle();
              } else if (e.key === 'Escape') {
                setEditingRoleTitle(false);
              }
            }}
            disabled={savingField === 'role_title'}
            placeholder="Role title"
            style={{ maxWidth: 480 }}
          />
        ) : (
          <h3
            className="mb-0"
            onClick={startEditingRoleTitle}
            title="Click to edit"
            style={{ cursor: 'pointer' }}
          >
            {data.role_title || (
              <span className="text-muted">Untitled role</span>
            )}
          </h3>
        )}
        <span className="ms-3">
          <StatusBadge status={data.status} />
        </span>
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      <Row className="g-3 mb-3">
        <Col md={6}>
          <Card>
            <Card.Body>
              <Card.Subtitle className="text-muted mb-2">Company</Card.Subtitle>
              {data.company_id ? (
                <Link to={`/companies/${data.company_id}`}>
                  {data.company_name}
                </Link>
              ) : (
                <span className="text-muted">—</span>
              )}
              <hr />
              <Form.Group className="mb-3">
                <Form.Label className="text-muted small mb-1">
                  Submitted on
                </Form.Label>
                <Form.Control
                  type="date"
                  value={editSubmittedOn}
                  onChange={(e) => setEditSubmittedOn(e.target.value)}
                />
              </Form.Group>
              <Form.Group>
                <Form.Label className="text-muted small mb-1">
                  Link to Job Description
                </Form.Label>
                <Form.Control
                  type="url"
                  value={editJdUrl}
                  onChange={(e) => setEditJdUrl(e.target.value)}
                  placeholder="https://..."
                />
                {data.jd_url && (
                  <Form.Text>
                    <a href={data.jd_url} target="_blank" rel="noreferrer">
                      Open current link
                    </a>
                  </Form.Text>
                )}
              </Form.Group>
            </Card.Body>
          </Card>
        </Col>
        <Col md={6}>
          <Card>
            <Card.Body>
              <Card.Subtitle className="text-muted mb-2">Status</Card.Subtitle>
              <Form.Select
                value={data.status}
                disabled={savingField === 'status'}
                onChange={(e) => patchField('status', e.target.value, 'status')}
              >
                {STATUSES.map((s) => (
                  <option key={s.short_name} value={s.short_name}>
                    {s.label}
                  </option>
                ))}
              </Form.Select>

              <hr />
              <Card.Subtitle className="text-muted mb-2">Resume</Card.Subtitle>
              <Form.Select
                value={data.resume_id ?? ''}
                disabled={savingField === 'resume_id'}
                onChange={(e) =>
                  patchField(
                    'resume_id',
                    e.target.value ? Number(e.target.value) : null,
                    'resume_id',
                  )
                }
              >
                <option value="">— none —</option>
                {resumes.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.title || `Resume #${r.id}`}
                    {r.is_master ? ' (master)' : ''}
                  </option>
                ))}
              </Form.Select>
              {data.resume_id && (
                <div className="mt-1 small">
                  <Link to={`/resumes/${data.resume_id}`}>
                    Open {data.resume_title || `resume #${data.resume_id}`}
                  </Link>
                </div>
              )}

              <hr />
              <Card.Subtitle className="text-muted mb-2">Actions</Card.Subtitle>
              <div className="d-flex gap-2 flex-wrap">
                <Button variant="outline-primary" size="sm" disabled>
                  Tailor with AI <Badge bg="light" text="dark">slice 06</Badge>
                </Button>
                <Button variant="outline-secondary" size="sm" disabled>
                  Trigger follow-up <Badge bg="light" text="dark">slice 08</Badge>
                </Button>
              </div>
            </Card.Body>
          </Card>
        </Col>
      </Row>

      <Card className="mb-3">
        <Card.Body>
          <Card.Subtitle className="text-muted mb-2">Notes</Card.Subtitle>
          <Form.Control
            as="textarea"
            rows={3}
            value={editNotes}
            onChange={(e) => setEditNotes(e.target.value)}
          />
        </Card.Body>
      </Card>

      <Card className="mb-3">
        <Card.Body>
          <div className="d-flex justify-content-between align-items-center mb-2">
            <Card.Subtitle className="text-muted">Job Description</Card.Subtitle>
            {data.jd_snapshot && (
              <Button
                variant="link"
                size="sm"
                onClick={() => setShowJd((v) => !v)}
              >
                {showJd ? 'Collapse' : 'Expand'}
              </Button>
            )}
          </div>
          <Collapse in={showJd || !data.jd_snapshot}>
            <div>
              <Form.Control
                as="textarea"
                rows={data.jd_snapshot ? 16 : 6}
                value={editJdText}
                onChange={(e) => setEditJdText(e.target.value)}
                placeholder="Paste the job description body here. Archived to S3 on save."
                style={{ fontFamily: 'monospace' }}
              />
              <Form.Text className="text-muted">
                Used as input to AI tailoring later. Leaving this empty clears the saved snapshot.
              </Form.Text>
            </div>
          </Collapse>
        </Card.Body>
      </Card>

      <Card className="mb-3">
        <Card.Body>
          <Card.Subtitle className="text-muted mb-2">
            Tailored title / summary
          </Card.Subtitle>
          <Form.Group className="mb-3">
            <Form.Label>Tailored title</Form.Label>
            <Form.Control
              value={editTailoredTitle}
              onChange={(e) => setEditTailoredTitle(e.target.value)}
            />
          </Form.Group>
          <Form.Group>
            <Form.Label>Tailored summary</Form.Label>
            <Form.Control
              as="textarea"
              rows={4}
              value={editTailoredSummary}
              onChange={(e) => setEditTailoredSummary(e.target.value)}
            />
          </Form.Group>
        </Card.Body>
      </Card>

      <div className="mb-3">
        <Button
          onClick={handleSave}
          disabled={
            savingField === 'save' ||
            (editNotes === (data.notes ?? '') &&
              editTailoredTitle === (data.tailored_title ?? '') &&
              editTailoredSummary === (data.tailored_summary ?? '') &&
              editSubmittedOn === (data.submitted_on ?? '') &&
              editJdUrl.trim() === (data.jd_url ?? '') &&
              editJdText === (data.jd_text ?? ''))
          }
        >
          {savingField === 'save' ? 'Saving…' : 'Save changes'}
        </Button>
      </div>

      {data.follow_ups.length > 0 && (
        <Card className="mb-3">
          <Card.Body>
            <Card.Subtitle className="text-muted mb-2">Follow-ups</Card.Subtitle>
            <ul className="mb-0">
              {data.follow_ups.map((f) => (
                <li key={f.id}>
                  Due {f.due_at ?? '—'}
                  {f.actioned_at ? ` (actioned ${f.actioned_at})` : ' (pending)'}
                </li>
              ))}
            </ul>
          </Card.Body>
        </Card>
      )}

      {data.responses.length > 0 && (
        <Card className="mb-3">
          <Card.Body>
            <Card.Subtitle className="text-muted mb-2">Responses</Card.Subtitle>
            <ul className="mb-0">
              {data.responses.map((r) => (
                <li key={r.id}>
                  <Badge bg="info" className="me-2">
                    {r.classification}
                  </Badge>
                  {r.subject ?? '(no subject)'} — {r.received_at ?? ''}
                </li>
              ))}
            </ul>
          </Card.Body>
        </Card>
      )}
    </>
  );
}