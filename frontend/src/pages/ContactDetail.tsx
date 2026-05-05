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
  Badge,
  Collapse,
} from 'react-bootstrap';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useApi } from '../api/client';
import { hasSendScope, useGmailStatus } from '../api/gmail';
import GmailComposeModal from '../components/GmailComposeModal';
import { KindBadge } from './Contacts';
import { KINDS, METHODS, DIRECTIONS } from './ContactForm';
import { StatusBadge } from './Submissions';

interface OutreachEvent {
  id: number;
  outreach_at: string | null;
  method: string | null;
  direction: string;
  notes: string | null;
  subject: string | null;
  body_text: string | null;
  from_email: string | null;
  gmail_thread_id: string | null;
  gmail_message_id: string | null;
}

interface LinkedSubmission {
  id: number;
  role_title: string | null;
  company_name: string | null;
  status: string;
}

interface ContactDetailData {
  id: number;
  kind: string;
  name: string;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  primary_method: string | null;
  company_id: number | null;
  company_name: string | null;
  notes: string | null;
  outreach_count: number;
  last_outreach_at: string | null;
  outreach: OutreachEvent[];
  linked_submissions: LinkedSubmission[];
}

interface CompanyOption {
  id: number;
  name: string;
}

function methodLabel(m: string | null): string {
  if (!m) return '';
  return METHODS.find((x) => x.short_name === m)?.label ?? m;
}

export default function ContactDetail() {
  const apiFetch = useApi();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<ContactDetailData | null>(null);
  const [companies, setCompanies] = useState<CompanyOption[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [editName, setEditName] = useState('');
  const [editKind, setEditKind] = useState('personal');
  const [editEmail, setEditEmail] = useState('');
  const [editPhone, setEditPhone] = useState('');
  const [editLinkedin, setEditLinkedin] = useState('');
  const [editMethod, setEditMethod] = useState('');
  const [editCompanyId, setEditCompanyId] = useState('');
  const [editNotes, setEditNotes] = useState('');
  const [saving, setSaving] = useState(false);

  const [outDir, setOutDir] = useState('outbound');
  const [outMethod, setOutMethod] = useState('');
  const [outAt, setOutAt] = useState('');
  const [outNotes, setOutNotes] = useState('');
  const [logging, setLogging] = useState(false);

  // Compose-modal state. ``null`` = closed. ``{}`` = open in compose mode
  // (fresh email). ``{ replyTo: ctx }`` = open in reply mode against an
  // existing email-sourced outreach row.
  const [composeOpen, setComposeOpen] = useState<{
    replyTo?: {
      gmail_message_id: string;
      from_email: string | null;
      subject: string | null;
      body: string | null;
      received_at: string | null;
    };
  } | null>(null);
  const [expandedEventIds, setExpandedEventIds] = useState<Set<number>>(
    new Set(),
  );
  const [linkInput, setLinkInput] = useState('');
  const [linkSaving, setLinkSaving] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [linkResult, setLinkResult] = useState<string | null>(null);
  const { status: gmailStatus } = useGmailStatus();
  const sendEnabled = hasSendScope(gmailStatus);
  const gmailAvailable = Boolean(
    gmailStatus && gmailStatus.available && gmailStatus.connected,
  );

  const load = useCallback(async () => {
    try {
      const r = await apiFetch(`/contacts/${id}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d: ContactDetailData = await r.json();
      setData(d);
      setEditName(d.name);
      setEditKind(d.kind);
      setEditEmail(d.email ?? '');
      setEditPhone(d.phone ?? '');
      setEditLinkedin(d.linkedin_url ?? '');
      setEditMethod(d.primary_method ?? '');
      setEditCompanyId(d.company_id ? String(d.company_id) : '');
      setEditNotes(d.notes ?? '');
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [apiFetch, id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    apiFetch('/companies')
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) =>
        setCompanies(rows.map((c: any) => ({ id: c.id, name: c.name }))),
      )
      .catch(() => setCompanies([]));
  }, [apiFetch]);

  async function handleSave() {
    setSaving(true);
    try {
      const r = await apiFetch(`/contacts/${id}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: editName,
          kind: editKind,
          email: editEmail || null,
          phone: editPhone || null,
          linkedin_url: editLinkedin || null,
          primary_method: editMethod || null,
          company_id: editCompanyId ? Number(editCompanyId) : null,
          notes: editNotes || null,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const d: ContactDetailData = await r.json();
      setData(d);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!data) return;
    if (!window.confirm(`Delete contact "${data.name}"?`)) return;
    try {
      const r = await apiFetch(`/contacts/${id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      navigate('/contacts');
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleLog(e: React.FormEvent) {
    e.preventDefault();
    setLogging(true);
    try {
      const r = await apiFetch(`/contacts/${id}/outreach`, {
        method: 'POST',
        body: JSON.stringify({
          direction: outDir,
          method: outMethod || undefined,
          outreach_at: outAt || undefined,
          notes: outNotes || undefined,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const d: ContactDetailData = await r.json();
      setData(d);
      setOutMethod('');
      setOutAt('');
      setOutNotes('');
    } catch (err) {
      setError(String(err));
    } finally {
      setLogging(false);
    }
  }

  async function handleDeleteOutreach(eventId: number) {
    if (!window.confirm('Delete this outreach event?')) return;
    try {
      const r = await apiFetch(`/contacts/${id}/outreach/${eventId}`, {
        method: 'DELETE',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleLinkThread(e: React.FormEvent) {
    e.preventDefault();
    if (!linkInput.trim()) return;
    setLinkSaving(true);
    setLinkError(null);
    setLinkResult(null);
    try {
      const r = await apiFetch(`/contacts/${id}/gmail-link`, {
        method: 'PUT',
        body: JSON.stringify({ mid: linkInput }),
      });
      if (!r.ok) {
        const text = await r.text();
        throw new Error(`HTTP ${r.status}: ${text}`);
      }
      const result: { imported: number; 'skipped:exists': number } =
        await r.json();
      const newRows = result.imported ?? 0;
      const dupes = result['skipped:exists'] ?? 0;
      setLinkResult(
        `Imported ${newRows} new ${newRows === 1 ? 'message' : 'messages'}` +
          (dupes > 0 ? ` (${dupes} already on file)` : '') +
          '.',
      );
      setLinkInput('');
      await load();
    } catch (err) {
      setLinkError(String(err));
    } finally {
      setLinkSaving(false);
    }
  }

  function toggleEventExpanded(eventId: number) {
    setExpandedEventIds((prev) => {
      const next = new Set(prev);
      if (next.has(eventId)) next.delete(eventId);
      else next.add(eventId);
      return next;
    });
  }

  if (error && !data) return <Alert variant="danger">{error}</Alert>;
  if (!data) return <Spinner animation="border" size="sm" />;

  return (
    <>
      <div className="d-flex align-items-center mb-3">
        <Link to="/contacts" className="me-3">
          ← Back
        </Link>
        <h3 className="mb-0 me-2">{data.name}</h3>
        <KindBadge kind={data.kind} />
        {sendEnabled && (
          <Button
            size="sm"
            variant="primary"
            className="ms-auto"
            onClick={() => setComposeOpen({})}
          >
            Compose email
          </Button>
        )}
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      <Card className="mb-3">
        <Card.Body>
          <Row className="g-3">
            <Col md={8}>
              <Form.Group>
                <Form.Label>Name</Form.Label>
                <Form.Control
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={4}>
              <Form.Group>
                <Form.Label>Kind</Form.Label>
                <Form.Select
                  value={editKind}
                  onChange={(e) => setEditKind(e.target.value)}
                >
                  {KINDS.map((k) => (
                    <option key={k.short_name} value={k.short_name}>
                      {k.label}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>Email</Form.Label>
                <Form.Control
                  type="email"
                  value={editEmail}
                  onChange={(e) => setEditEmail(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>Phone</Form.Label>
                <Form.Control
                  type="tel"
                  value={editPhone}
                  onChange={(e) => setEditPhone(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>LinkedIn URL</Form.Label>
                <Form.Control
                  type="url"
                  value={editLinkedin}
                  onChange={(e) => setEditLinkedin(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>Preferred outreach method</Form.Label>
                <Form.Select
                  value={editMethod}
                  onChange={(e) => setEditMethod(e.target.value)}
                >
                  <option value="">— none —</option>
                  {METHODS.map((m) => (
                    <option key={m.short_name} value={m.short_name}>
                      {m.label}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>Company</Form.Label>
                <Form.Select
                  value={editCompanyId}
                  onChange={(e) => setEditCompanyId(e.target.value)}
                >
                  <option value="">— none —</option>
                  {companies.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Form.Select>
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
          <div className="mt-3 d-flex gap-2">
            <Button onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
            <Button variant="outline-danger" onClick={handleDelete}>
              Delete contact
            </Button>
          </div>
        </Card.Body>
      </Card>

      <Card className="mb-3">
        <Card.Body>
          <h5>Log outreach</h5>
          <Form onSubmit={handleLog}>
            <Row className="g-2 align-items-end">
              <Col md={3}>
                <Form.Label className="small text-muted">Direction</Form.Label>
                <Form.Select
                  value={outDir}
                  onChange={(e) => setOutDir(e.target.value)}
                >
                  {DIRECTIONS.map((d) => (
                    <option key={d.short_name} value={d.short_name}>
                      {d.label}
                    </option>
                  ))}
                </Form.Select>
              </Col>
              <Col md={3}>
                <Form.Label className="small text-muted">Method</Form.Label>
                <Form.Select
                  value={outMethod}
                  onChange={(e) => setOutMethod(e.target.value)}
                >
                  <option value="">— pick —</option>
                  {METHODS.map((m) => (
                    <option key={m.short_name} value={m.short_name}>
                      {m.label}
                    </option>
                  ))}
                </Form.Select>
              </Col>
              <Col md={3}>
                <Form.Label className="small text-muted">When</Form.Label>
                <Form.Control
                  type="datetime-local"
                  value={outAt}
                  onChange={(e) => setOutAt(e.target.value)}
                  placeholder="now"
                />
              </Col>
              <Col md={12}>
                <Form.Label className="small text-muted">Notes</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={2}
                  value={outNotes}
                  onChange={(e) => setOutNotes(e.target.value)}
                />
              </Col>
              <Col md={3}>
                <Button type="submit" disabled={logging} className="w-100">
                  {logging ? 'Logging…' : 'Log outreach'}
                </Button>
              </Col>
            </Row>
          </Form>
        </Card.Body>
      </Card>

      {gmailAvailable && (
        <Card className="mb-3">
          <Card.Body>
            <Card.Subtitle className="text-muted mb-2">
              Import a Gmail thread
            </Card.Subtitle>
            <Form.Text className="text-muted d-block mb-2">
              For conversations that started outside the app: paste any
              Message-ID from the thread to import every message into
              this contact's timeline. In Thunderbird:{' '}
              <strong>Right-click → Organize → Copy Message Link</strong>.
              In Gmail web: <strong>⋮ → Show original</strong>, then
              copy the Message-ID line. The parser handles the
              <code className="mx-1">mid:</code> URI form, bare IDs,
              and pasted header blocks.
            </Form.Text>
            <Form onSubmit={handleLinkThread}>
              <div className="d-flex gap-2">
                <Form.Control
                  type="text"
                  placeholder="Paste Message-ID, mid: URI, or full header line…"
                  value={linkInput}
                  onChange={(e) => setLinkInput(e.target.value)}
                  disabled={linkSaving}
                />
                <Button
                  type="submit"
                  disabled={linkSaving || !linkInput.trim()}
                >
                  {linkSaving ? 'Importing…' : 'Import'}
                </Button>
              </div>
              {linkError && (
                <Alert variant="danger" className="mt-2 mb-0">
                  {linkError}
                </Alert>
              )}
              {linkResult && (
                <Alert variant="success" className="mt-2 mb-0">
                  {linkResult}
                </Alert>
              )}
            </Form>
          </Card.Body>
        </Card>
      )}

      <h5 className="mt-4">Outreach history ({data.outreach_count})</h5>
      {data.outreach.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">
            No outreach logged yet.
          </Card.Body>
        </Card>
      ) : (
        <Card className="mb-3">
          <Card.Body className="p-0">
            <ul className="list-unstyled mb-0">
              {data.outreach.map((evt) => {
                const isEmail = Boolean(evt.gmail_message_id);
                const hasBody = Boolean(evt.body_text && evt.body_text.length > 0);
                const isExpanded = expandedEventIds.has(evt.id);
                const arrow = evt.direction === 'inbound' ? '↓' : '↑';
                const arrowVariant =
                  evt.direction === 'inbound' ? 'success' : 'primary';
                const headline = isEmail
                  ? evt.subject || '(no subject)'
                  : evt.notes || '(no notes)';
                return (
                  <li
                    key={evt.id}
                    className="px-3 py-2 border-bottom"
                  >
                    <div className="d-flex align-items-center gap-2">
                      <Badge bg={arrowVariant} pill title={evt.direction}>
                        {arrow}
                      </Badge>
                      {evt.method && (
                        <Badge bg="secondary">{methodLabel(evt.method)}</Badge>
                      )}
                      {isEmail && (
                        <Badge bg="info" text="dark">
                          via Gmail
                        </Badge>
                      )}
                      <span className="flex-grow-1">
                        {hasBody ? (
                          <Button
                            variant="link"
                            size="sm"
                            className="p-0 align-baseline text-start text-decoration-none"
                            onClick={() => toggleEventExpanded(evt.id)}
                          >
                            <span className="me-1">
                              {isExpanded ? '▾' : '▸'}
                            </span>
                            {headline}
                          </Button>
                        ) : (
                          <span>{headline}</span>
                        )}
                        {evt.from_email && (
                          <span className="text-muted small ms-2">
                            from {evt.from_email}
                          </span>
                        )}
                        {evt.outreach_at && (
                          <span className="text-muted small ms-2">
                            {evt.outreach_at}
                          </span>
                        )}
                      </span>
                      {sendEnabled && isEmail && evt.gmail_message_id && (
                        <Button
                          size="sm"
                          variant="outline-primary"
                          onClick={() =>
                            setComposeOpen({
                              replyTo: {
                                gmail_message_id: evt.gmail_message_id!,
                                from_email: evt.from_email,
                                subject: evt.subject,
                                body: evt.body_text,
                                received_at: evt.outreach_at,
                              },
                            })
                          }
                        >
                          Reply
                        </Button>
                      )}
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() => handleDeleteOutreach(evt.id)}
                      >
                        Delete
                      </Button>
                    </div>
                    {isEmail && evt.notes && (
                      <div className="text-muted small ms-4 mt-1">
                        {evt.notes}
                      </div>
                    )}
                    <Collapse in={isExpanded && hasBody}>
                      <div>
                        <pre
                          className="small text-muted mt-2 mb-0 ms-4 p-2 bg-light rounded"
                          style={{
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                            maxHeight: 400,
                            overflowY: 'auto',
                          }}
                        >
                          {evt.body_text}
                        </pre>
                      </div>
                    </Collapse>
                  </li>
                );
              })}
            </ul>
          </Card.Body>
        </Card>
      )}

      <h5 className="mt-4">
        Linked submissions ({data.linked_submissions.length})
      </h5>
      {data.linked_submissions.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">
            No submissions linked to this contact yet — add them from the
            submission detail page.
          </Card.Body>
        </Card>
      ) : (
        <Table hover responsive className="align-middle">
          <thead>
            <tr>
              <th>Role</th>
              <th>Company</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {data.linked_submissions.map((s) => (
              <tr key={s.id}>
                <td>
                  <Link to={`/submissions/${s.id}`}>
                    {s.role_title || 'Untitled role'}
                  </Link>
                </td>
                <td>{s.company_name ?? '—'}</td>
                <td>
                  <StatusBadge status={s.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      {composeOpen && (
        <GmailComposeModal
          show={true}
          onHide={() => setComposeOpen(null)}
          defaultSubmissionId={null}
          defaultContactId={data.id}
          contactBannerLabel={data.name}
          defaultTo={data.email ?? ''}
          replyTo={composeOpen.replyTo}
          defaultResumeId={null}
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