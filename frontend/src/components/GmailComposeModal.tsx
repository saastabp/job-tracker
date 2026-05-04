import { useEffect, useState } from 'react';
import { Modal, Button, Form, Alert, Spinner, Collapse } from 'react-bootstrap';
import { useApi } from '../api/client';

interface ResumeOption {
  id: number;
  title: string | null;
  is_master: boolean;
}

interface ReplyContext {
  /** Gmail message id of the response being replied to (NOT the local id). */
  gmail_message_id: string;
  /** The original from-address — pre-fills To. */
  from_email: string | null;
  /** The original subject — pre-fills Subject with `Re: ` prepended. */
  subject: string | null;
  /** The original body — prefilled into the textarea as `> `-prefixed quoted lines. */
  body: string | null;
  /** The original received_at — used for the "On <date>, <from> wrote:" attribution. */
  received_at: string | null;
}

interface Props {
  show: boolean;
  onHide: () => void;
  submissionId: number;
  /** Compose mode: undefined. Reply mode: response context. */
  replyTo?: ReplyContext;
  /** Default resume id (typically the master). */
  defaultResumeId?: number | null;
  /** Called after a successful send; parent should refresh submission detail. */
  onSent: () => void;
  /** Bubbles up to the parent so it can redirect to OAuth start. */
  onNeedReconsent: () => void;
}

function commaList(input: string): string[] {
  return input
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function stripReplyPrefix(subject: string): string {
  // Strip leading "Re:" / "RE: " / "re:" chains so we don't end up with
  // "Re: Re: Re: ...". Idempotent: empty input → empty.
  return subject.replace(/^(\s*re\s*:\s*)+/i, '').trim();
}

/**
 * Build the quoted-original block prepended to the textarea in reply mode.
 * Format roughly matches Gmail / RFC 1849: a blank line, an attribution line,
 * then `> `-prefixed body lines. The user's reply goes ABOVE this block (caret
 * positioned at the very top by the textarea ref).
 */
function buildQuotedOriginal(replyTo: ReplyContext): string {
  if (!replyTo.body || !replyTo.body.trim()) {
    return '';
  }
  const attribution = `On ${replyTo.received_at ?? 'a previous date'}, ${
    replyTo.from_email ?? 'someone'
  } wrote:`;
  const quotedLines = replyTo.body
    .split('\n')
    .map((line) => `> ${line}`)
    .join('\n');
  return `\n\n${attribution}\n${quotedLines}`;
}

export default function GmailComposeModal({
  show,
  onHide,
  submissionId,
  replyTo,
  defaultResumeId,
  onSent,
  onNeedReconsent,
}: Props) {
  const apiFetch = useApi();

  const [to, setTo] = useState('');
  const [cc, setCc] = useState('');
  const [bcc, setBcc] = useState('');
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [resumes, setResumes] = useState<ResumeOption[] | null>(null);
  const [resumeId, setResumeId] = useState<string>('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset form on open. Reply mode pre-fills.
  useEffect(() => {
    if (!show) return;
    setError(null);
    setSending(false);
    if (replyTo) {
      setTo(replyTo.from_email ?? '');
      const stripped = stripReplyPrefix(replyTo.subject ?? '');
      setSubject(stripped ? `Re: ${stripped}` : 'Re: ');
      // Prefill the textarea with the quoted-original block. The user types
      // their reply above the blank lines at the top.
      setBody(buildQuotedOriginal(replyTo));
      setCc('');
      setBcc('');
      setShowCcBcc(false);
    } else {
      setTo('');
      setSubject('');
      setBody('');
      setCc('');
      setBcc('');
      setShowCcBcc(false);
    }
    setResumeId(defaultResumeId != null ? String(defaultResumeId) : '');
  }, [show, replyTo, defaultResumeId]);

  // Fetch resume picker options once the modal opens (cheap; small list).
  useEffect(() => {
    if (!show || resumes !== null) return;
    apiFetch('/resumes')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: { items: ResumeOption[] }) => setResumes(d.items ?? []))
      .catch((e) => setError(`couldn't load resumes: ${e}`));
  }, [show, resumes, apiFetch]);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const toList = commaList(to);
    if (toList.length === 0) {
      setError('At least one recipient is required.');
      return;
    }
    if (!subject.trim()) {
      setError('Subject is required.');
      return;
    }
    if (!body.trim()) {
      setError('Body is required.');
      return;
    }

    setSending(true);
    try {
      const payload: Record<string, unknown> = {
        to: toList,
        subject: subject.trim(),
        body,
      };
      const ccList = commaList(cc);
      if (ccList.length > 0) payload.cc = ccList;
      const bccList = commaList(bcc);
      if (bccList.length > 0) payload.bcc = bccList;
      if (resumeId) payload.resume_id = Number(resumeId);
      if (replyTo) payload.in_reply_to_message_id = replyTo.gmail_message_id;

      const r = await apiFetch(`/submissions/${submissionId}/send`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (r.status === 403) {
        const body403 = await r.json().catch(() => ({}));
        if (body403?.needs_reconsent) {
          onNeedReconsent();
          onHide();
          return;
        }
        throw new Error(body403?.error ?? `HTTP 403`);
      }

      if (!r.ok) {
        const text = await r.text();
        throw new Error(`HTTP ${r.status}: ${text}`);
      }

      onSent();
      onHide();
    } catch (err) {
      setError(String(err));
    } finally {
      setSending(false);
    }
  }

  const title = replyTo ? 'Reply' : 'Compose Gmail message';

  return (
    <Modal
      show={show}
      onHide={sending ? undefined : onHide}
      backdrop={sending ? 'static' : true}
      size="lg"
    >
      <Form onSubmit={handleSend}>
        <Modal.Header closeButton={!sending}>
          <Modal.Title>{title}</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {error && <Alert variant="danger">{error}</Alert>}
          <Form.Group className="mb-3">
            <Form.Label>To</Form.Label>
            <Form.Control
              type="text"
              placeholder="recruiter@acme.com"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              disabled={sending}
            />
            <Form.Text className="text-muted">
              Separate multiple addresses with commas.
            </Form.Text>
          </Form.Group>

          <div className="mb-2">
            <Button
              variant="link"
              size="sm"
              className="p-0"
              onClick={() => setShowCcBcc(!showCcBcc)}
              type="button"
            >
              {showCcBcc ? 'Hide Cc / Bcc' : 'Add Cc / Bcc'}
            </Button>
          </div>
          <Collapse in={showCcBcc}>
            <div>
              <Form.Group className="mb-3">
                <Form.Label>Cc</Form.Label>
                <Form.Control
                  type="text"
                  value={cc}
                  onChange={(e) => setCc(e.target.value)}
                  disabled={sending}
                />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>Bcc</Form.Label>
                <Form.Control
                  type="text"
                  value={bcc}
                  onChange={(e) => setBcc(e.target.value)}
                  disabled={sending}
                />
              </Form.Group>
            </div>
          </Collapse>

          <Form.Group className="mb-3">
            <Form.Label>Subject</Form.Label>
            <Form.Control
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={sending}
            />
          </Form.Group>

          <Form.Group className="mb-3">
            <Form.Label>Body</Form.Label>
            <Form.Control
              as="textarea"
              rows={10}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={sending}
              style={{ fontFamily: 'inherit' }}
            />
            <Form.Text className="text-muted">
              Plaintext only. Recipients see your normal Gmail address as
              the sender.
            </Form.Text>
          </Form.Group>

          <Form.Group className="mb-3">
            <Form.Label>Attach resume</Form.Label>
            <Form.Select
              value={resumeId}
              onChange={(e) => setResumeId(e.target.value)}
              disabled={sending || resumes === null}
            >
              <option value="">— No attachment —</option>
              {(resumes ?? []).map((r) => (
                <option key={r.id} value={String(r.id)}>
                  {r.is_master ? '★ ' : ''}
                  {r.title ?? `Resume #${r.id}`}
                </option>
              ))}
            </Form.Select>
          </Form.Group>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide} disabled={sending}>
            Cancel
          </Button>
          <Button type="submit" disabled={sending}>
            {sending ? (
              <>
                <Spinner size="sm" animation="border" className="me-2" />
                Sending…
              </>
            ) : (
              'Send'
            )}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}