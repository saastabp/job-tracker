import { useCallback, useEffect, useMemo, useState } from 'react';
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
import Select from 'react-select';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { aiEnabled, useApi } from '../api/client';
import { hasSendScope, useGmailStatus } from '../api/gmail';
import GmailComposeModal from '../components/GmailComposeModal';
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
  notified_at: string | null;
  notes: string | null;
  auto_created: boolean;
}

interface ResponseRow {
  id: number;
  received_at: string | null;
  from_email: string | null;
  subject: string | null;
  body_text: string | null;
  gmail_message_id: string | null;
  classification: string;
}

interface ContactSummary {
  id: number;
  name: string;
  email: string | null;
  kind: string;
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
  gmail_thread_id: string | null;
  follow_ups: FollowUp[];
  responses: ResponseRow[];
  contacts: ContactSummary[];
}

interface ReplyContext {
  gmail_message_id: string;
  from_email: string | null;
  subject: string | null;
  body: string | null;
  received_at: string | null;
}

interface ContactOption {
  value: number;
  label: string;
}

interface ResumeOption {
  id: number;
  title: string | null;
  summary: string | null;
  is_master: boolean;
}

function defaultDueDate(): string {
  const d = new Date();
  d.setDate(d.getDate() + 7);
  return d.toISOString().slice(0, 10);
}

function normalizeDue(raw: string): string | null {
  // Accept YYYY-MM-DD (default to 14:00 UTC) or YYYY-MM-DDTHH:MM (assume UTC).
  const trimmed = raw.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
    return `${trimmed}T14:00:00Z`;
  }
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(trimmed)) {
    return `${trimmed}:00Z`;
  }
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(trimmed)) {
    return trimmed.endsWith('Z') ? trimmed : `${trimmed}Z`;
  }
  return null;
}

export default function SubmissionDetail() {
  const apiFetch = useApi();
  const navigate = useNavigate();
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
  const [tailorBusy, setTailorBusy] = useState(false);
  const [tailorMessage, setTailorMessage] = useState<string | null>(null);
  const [followUpBusyId, setFollowUpBusyId] = useState<number | null>(null);
  const [addFollowUpBusy, setAddFollowUpBusy] = useState(false);
  const [contactOptions, setContactOptions] = useState<ContactOption[]>([]);
  const [selectedContactIds, setSelectedContactIds] = useState<number[]>([]);
  const [savingContacts, setSavingContacts] = useState(false);
  // Gmail integration state. Compose-modal is one of:
  //   null               → modal closed
  //   { replyTo: ctx }   → open in reply mode pre-filled from ctx
  //   {}                 → open in compose mode (fresh message)
  const [composeOpen, setComposeOpen] = useState<{
    replyTo?: ReplyContext;
  } | null>(null);
  const [expandedResponseIds, setExpandedResponseIds] = useState<Set<number>>(
    new Set(),
  );
  const [linkInput, setLinkInput] = useState('');
  const [linkSaving, setLinkSaving] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const { status: gmailStatus } = useGmailStatus();
  const sendEnabled = hasSendScope(gmailStatus);
  const gmailAvailable = Boolean(
    gmailStatus && gmailStatus.available && gmailStatus.connected,
  );

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
      setSelectedContactIds(d.contacts.map((c) => c.id));
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
            summary: r.summary,
            is_master: r.is_master,
          })),
        ),
      )
      .catch(() => setResumes([]));
  }, [apiFetch]);

  useEffect(() => {
    apiFetch('/contacts')
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) =>
        setContactOptions(
          rows.map((c: any) => ({
            value: c.id,
            label: c.company_name ? `${c.name} — ${c.company_name}` : c.name,
          })),
        ),
      )
      .catch(() => setContactOptions([]));
  }, [apiFetch]);

  const selectedContactOptions = useMemo(() => {
    const idSet = new Set(selectedContactIds);
    // Surface every selected id even if /contacts hasn't loaded yet (or the
    // contact was created after this page mounted) — fall back to a label
    // derived from data.contacts.
    const fromOptions = contactOptions.filter((o) => idSet.has(o.value));
    if (fromOptions.length === selectedContactIds.length) return fromOptions;
    const known = new Set(fromOptions.map((o) => o.value));
    const fallbacks: ContactOption[] = [];
    for (const c of data?.contacts ?? []) {
      if (idSet.has(c.id) && !known.has(c.id)) {
        fallbacks.push({ value: c.id, label: c.name });
      }
    }
    return [...fromOptions, ...fallbacks];
  }, [contactOptions, selectedContactIds, data]);

  const contactsDirty = useMemo(() => {
    if (!data) return false;
    const loaded = new Set(data.contacts.map((c) => c.id));
    if (loaded.size !== selectedContactIds.length) return true;
    for (const id of selectedContactIds) if (!loaded.has(id)) return true;
    return false;
  }, [data, selectedContactIds]);

  async function saveContacts() {
    if (!data) return;
    setSavingContacts(true);
    try {
      const r = await apiFetch(`/submissions/${id}/contacts`, {
        method: 'PUT',
        body: JSON.stringify({ contact_ids: selectedContactIds }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const d: SubmissionDetail = await r.json();
      setData(d);
      setSelectedContactIds(d.contacts.map((c) => c.id));
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingContacts(false);
    }
  }

  async function handleTailor() {
    if (!data) return;
    setTailorMessage(null);
    const master = resumes.find((r) => r.is_master);
    if (!master?.title || !master?.summary) {
      setTailorMessage(
        'Set a master resume with both a title and a summary before tailoring.',
      );
      return;
    }
    if (!editJdText.trim()) {
      setTailorMessage(
        'Paste the job description into the field below before tailoring.',
      );
      return;
    }
    setTailorBusy(true);
    try {
      const r = await apiFetch('/ai/tailor', {
        method: 'POST',
        body: JSON.stringify({
          master_title: master.title,
          master_summary: master.summary,
          jd_text: editJdText,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const { tailored_title, tailored_summary } = await r.json();
      setEditTailoredTitle(tailored_title);
      setEditTailoredSummary(tailored_summary);
      setTailorMessage(
        'AI suggestion loaded into the tailored fields below. Review, edit, then Save changes.',
      );
    } catch (e) {
      setTailorMessage(`Tailoring failed: ${String(e)}`);
    } finally {
      setTailorBusy(false);
    }
  }

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

  async function addFollowUp() {
    const ans = prompt(
      'Due date for the new follow-up (YYYY-MM-DD or YYYY-MM-DDTHH:MM):',
      defaultDueDate(),
    );
    if (!ans) return;
    const isoDue = normalizeDue(ans);
    if (!isoDue) {
      setError(`Invalid date: ${ans}`);
      return;
    }
    setAddFollowUpBusy(true);
    try {
      const r = await apiFetch(`/submissions/${id}/follow-ups`, {
        method: 'POST',
        body: JSON.stringify({ due_at: isoDue }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setAddFollowUpBusy(false);
    }
  }

  async function actionFollowUp(fuId: number, body: object) {
    setFollowUpBusyId(fuId);
    try {
      const r = await apiFetch(`/follow-ups/${fuId}`, {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setFollowUpBusyId(null);
    }
  }

  async function deleteFollowUp(fuId: number) {
    if (!confirm('Delete this follow-up?')) return;
    setFollowUpBusyId(fuId);
    try {
      const r = await apiFetch(`/follow-ups/${fuId}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setFollowUpBusyId(null);
    }
  }

  async function rescheduleFollowUp(fuId: number, currentDueAt: string | null) {
    const ans = prompt(
      'New due date (YYYY-MM-DD or YYYY-MM-DDTHH:MM):',
      currentDueAt?.slice(0, 16) ?? defaultDueDate(),
    );
    if (!ans) return;
    const iso = normalizeDue(ans);
    if (!iso) {
      setError(`Invalid date: ${ans}`);
      return;
    }
    await actionFollowUp(fuId, { due_at: iso });
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

  async function handleLinkThread(e: React.FormEvent) {
    e.preventDefault();
    if (!linkInput.trim()) return;
    setLinkSaving(true);
    setLinkError(null);
    try {
      const r = await apiFetch(`/submissions/${id}/gmail-link`, {
        method: 'PUT',
        body: JSON.stringify({ mid: linkInput }),
      });
      if (!r.ok) {
        const text = await r.text();
        throw new Error(`HTTP ${r.status}: ${text}`);
      }
      setLinkInput('');
      await load();
    } catch (err) {
      setLinkError(String(err));
    } finally {
      setLinkSaving(false);
    }
  }

  async function handleDeleteSubmission() {
    if (
      !window.confirm(
        'Delete this submission? Linked follow-ups, responses, and the JD snapshot will be removed too. This cannot be undone from the UI.',
      )
    ) {
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      const r = await apiFetch(`/submissions/${id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      navigate('/submissions');
    } catch (e) {
      setError(String(e));
      setDeleting(false);
    }
  }

  async function handleUnlinkThread() {
    if (!window.confirm('Unlink this Gmail thread? Existing responses stay; new messages stop arriving.')) {
      return;
    }
    setLinkError(null);
    try {
      const r = await apiFetch(`/submissions/${id}/gmail-link`, {
        method: 'DELETE',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await load();
    } catch (err) {
      setLinkError(String(err));
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
        <div className="ms-auto d-flex gap-2">
          {sendEnabled && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setComposeOpen({})}
              disabled={data.gmail_thread_id !== null}
              title={
                data.gmail_thread_id
                  ? 'Submission already linked to a thread; reply from a response row instead'
                  : undefined
              }
            >
              Compose
            </Button>
          )}
          <Button
            size="sm"
            variant="outline-danger"
            onClick={handleDeleteSubmission}
            disabled={deleting}
          >
            {deleting ? 'Deleting…' : 'Delete'}
          </Button>
        </div>
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
                {aiEnabled && (
                  <Button
                    variant="outline-primary"
                    size="sm"
                    onClick={handleTailor}
                    disabled={tailorBusy}
                  >
                    {tailorBusy ? 'Tailoring…' : 'Tailor with AI'}
                  </Button>
                )}
                <Button
                  variant="outline-secondary"
                  size="sm"
                  onClick={addFollowUp}
                  disabled={addFollowUpBusy}
                >
                  {addFollowUpBusy ? 'Adding…' : 'Add follow-up'}
                </Button>
              </div>
              {!aiEnabled && (
                <div className="text-muted small mt-2">
                  AI tailoring temporarily disabled.
                </div>
              )}
              {tailorMessage && (
                <div className="text-muted small mt-2">{tailorMessage}</div>
              )}
            </Card.Body>
          </Card>
        </Col>
      </Row>

      <Card className="mb-3">
        <Card.Body>
          <Card.Subtitle className="text-muted mb-2">Contacts</Card.Subtitle>
          <Select
            isMulti
            options={contactOptions}
            value={selectedContactOptions}
            onChange={(next) =>
              setSelectedContactIds(next.map((o) => o.value))
            }
            placeholder="Link contacts to this submission…"
            classNamePrefix="rs"
          />
          <div className="mt-2">
            <Button
              size="sm"
              onClick={saveContacts}
              disabled={!contactsDirty || savingContacts}
            >
              {savingContacts ? 'Saving…' : 'Save contacts'}
            </Button>
            {data.contacts.length > 0 && (
              <span className="ms-3 text-muted small">
                {data.contacts.length} linked —{' '}
                {data.contacts.map((c, i) => (
                  <span key={c.id}>
                    {i > 0 && ', '}
                    <Link to={`/contacts/${c.id}`}>{c.name}</Link>
                  </span>
                ))}
              </span>
            )}
          </div>
        </Card.Body>
      </Card>

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

      <Card className="mb-3">
        <Card.Body>
          <Card.Subtitle className="text-muted mb-2">Follow-ups</Card.Subtitle>
          {data.follow_ups.length === 0 ? (
            <div className="text-muted small">
              No follow-ups yet — use “Add follow-up” above to schedule one.
            </div>
          ) : (
            <ul className="list-unstyled mb-0">
              {data.follow_ups.map((f) => {
                const busy = followUpBusyId === f.id;
                return (
                  <li
                    key={f.id}
                    className="py-2 border-bottom d-flex align-items-center"
                  >
                    <div className="me-auto">
                      <span className="me-2">Due {f.due_at ?? '—'}</span>
                      {f.auto_created && (
                        <Badge bg="light" text="dark" className="me-2">
                          auto
                        </Badge>
                      )}
                      {f.actioned_at ? (
                        <Badge bg="success" className="me-2">
                          done {f.actioned_at}
                        </Badge>
                      ) : (
                        <Badge bg="secondary" className="me-2">
                          pending
                        </Badge>
                      )}
                      {f.notes && (
                        <span className="text-muted small">— {f.notes}</span>
                      )}
                    </div>
                    {!f.actioned_at && (
                      <>
                        <Button
                          size="sm"
                          variant="outline-success"
                          className="me-2"
                          onClick={() =>
                            actionFollowUp(f.id, { actioned: true })
                          }
                          disabled={busy}
                        >
                          Mark done
                        </Button>
                        <Button
                          size="sm"
                          variant="outline-secondary"
                          className="me-2"
                          onClick={() => rescheduleFollowUp(f.id, f.due_at)}
                          disabled={busy}
                        >
                          Reschedule
                        </Button>
                      </>
                    )}
                    <Button
                      size="sm"
                      variant="outline-danger"
                      onClick={() => deleteFollowUp(f.id)}
                      disabled={busy}
                    >
                      Delete
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card.Body>
      </Card>

      {gmailAvailable && (
        <Card className="mb-3">
          <Card.Body>
            <Card.Subtitle className="text-muted mb-2">Gmail thread</Card.Subtitle>
            {data.gmail_thread_id ? (
              <div className="d-flex align-items-center gap-2">
                <Badge bg="success">Linked</Badge>
                <code className="small">
                  {data.gmail_thread_id.slice(0, 16)}…
                </code>
                <Button
                  size="sm"
                  variant="outline-secondary"
                  onClick={handleUnlinkThread}
                >
                  Unlink
                </Button>
              </div>
            ) : (
              <Form onSubmit={handleLinkThread}>
                <Form.Text className="text-muted d-block mb-2">
                  For submissions you sent through another mail client (or
                  recruiter-originated chains): paste any Message-ID from
                  the conversation. In Thunderbird:{' '}
                  <strong>Right-click → Organize → Copy Message Link</strong>.
                  In Gmail web: <strong>⋮ → Show original</strong>, then
                  copy the Message-ID line. The parser handles the
                  <code className="mx-1">mid:</code> URI form, bare IDs,
                  and even whole pasted header blocks.
                </Form.Text>
                <div className="d-flex gap-2">
                  <Form.Control
                    type="text"
                    placeholder="Paste Message-ID, mid: URI, or full header line…"
                    value={linkInput}
                    onChange={(e) => setLinkInput(e.target.value)}
                    disabled={linkSaving}
                  />
                  <Button type="submit" disabled={linkSaving || !linkInput.trim()}>
                    {linkSaving ? 'Linking…' : 'Link'}
                  </Button>
                </div>
                {linkError && (
                  <Alert variant="danger" className="mt-2 mb-0">
                    {linkError}
                  </Alert>
                )}
              </Form>
            )}
          </Card.Body>
        </Card>
      )}

      {data.responses.length > 0 && (
        <Card className="mb-3">
          <Card.Body>
            <Card.Subtitle className="text-muted mb-2">Responses</Card.Subtitle>
            <ul className="list-unstyled mb-0">
              {data.responses.map((r) => {
                const isExpanded = expandedResponseIds.has(r.id);
                const hasBody = Boolean(r.body_text && r.body_text.length > 0);
                const toggleExpanded = () => {
                  setExpandedResponseIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(r.id)) next.delete(r.id);
                    else next.add(r.id);
                    return next;
                  });
                };
                return (
                  <li key={r.id} className="border-bottom py-2">
                    <div className="d-flex align-items-center gap-2">
                      <Badge bg="info">{r.classification}</Badge>
                      <span className="flex-grow-1">
                        {hasBody ? (
                          <Button
                            variant="link"
                            size="sm"
                            className="p-0 align-baseline text-start text-decoration-none"
                            onClick={toggleExpanded}
                          >
                            <span className="me-1">{isExpanded ? '▾' : '▸'}</span>
                            {r.subject ?? '(no subject)'}
                          </Button>
                        ) : (
                          <span>{r.subject ?? '(no subject)'}</span>
                        )}
                        {r.from_email && (
                          <span className="text-muted small ms-2">
                            from {r.from_email}
                          </span>
                        )}
                        {r.received_at && (
                          <span className="text-muted small ms-2">
                            {r.received_at}
                          </span>
                        )}
                      </span>
                      {sendEnabled && r.gmail_message_id && (
                        <Button
                          size="sm"
                          variant="outline-primary"
                          onClick={() =>
                            setComposeOpen({
                              replyTo: {
                                gmail_message_id: r.gmail_message_id!,
                                from_email: r.from_email,
                                subject: r.subject,
                                body: r.body_text,
                                received_at: r.received_at,
                              },
                            })
                          }
                        >
                          Reply
                        </Button>
                      )}
                    </div>
                    {isExpanded && hasBody && (
                      <pre
                        className="small text-muted mt-2 mb-0 ms-4 p-2 bg-light rounded"
                        style={{
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          maxHeight: 400,
                          overflowY: 'auto',
                        }}
                      >
                        {r.body_text}
                      </pre>
                    )}
                  </li>
                );
              })}
            </ul>
          </Card.Body>
        </Card>
      )}

      {composeOpen && data && (
        <GmailComposeModal
          show={true}
          onHide={() => setComposeOpen(null)}
          defaultSubmissionId={Number(id)}
          defaultContactId={null}
          replyTo={composeOpen.replyTo}
          defaultResumeId={data.resume_id ?? null}
          onSent={() => {
            setComposeOpen(null);
            load();
          }}
          onNeedReconsent={() => {
            window.location.href = '/settings';
          }}
        />
      )}
    </>
  );
}