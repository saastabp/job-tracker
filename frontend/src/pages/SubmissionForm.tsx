import { useEffect, useState } from 'react';
import { Modal, Form, Button, Alert, Row, Col } from 'react-bootstrap';
import { useApi } from '../api/client';

export const STATUSES: { short_name: string; label: string }[] = [
  { short_name: 'applied', label: 'Applied' },
  { short_name: 'responded', label: 'Responded' },
  { short_name: 'interviewing', label: 'Interviewing' },
  { short_name: 'offer', label: 'Offer' },
  { short_name: 'rejected', label: 'Rejected' },
  { short_name: 'ghosted', label: 'Ghosted' },
];

interface CompanyOption {
  id: number;
  name: string;
}

interface Props {
  show: boolean;
  onHide: () => void;
  onCreated: (newId: number) => void;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function SubmissionForm({ show, onHide, onCreated }: Props) {
  const apiFetch = useApi();
  const [companies, setCompanies] = useState<CompanyOption[]>([]);
  const [companyName, setCompanyName] = useState('');
  const [roleTitle, setRoleTitle] = useState('');
  const [status, setStatus] = useState('applied');
  const [submittedOn, setSubmittedOn] = useState(todayIso());
  const [jdUrl, setJdUrl] = useState('');
  const [jdText, setJdText] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!show) return;
    apiFetch('/companies')
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => setCompanies(rows.map((c: any) => ({ id: c.id, name: c.name }))))
      .catch(() => setCompanies([]));
  }, [show, apiFetch]);

  function reset() {
    setCompanyName('');
    setRoleTitle('');
    setStatus('applied');
    setSubmittedOn(todayIso());
    setJdUrl('');
    setJdText('');
    setNotes('');
    setError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const r = await apiFetch('/submissions', {
        method: 'POST',
        body: JSON.stringify({
          company_name: companyName.trim() || undefined,
          role_title: roleTitle.trim() || undefined,
          status,
          submitted_on: submittedOn || undefined,
          jd_url: jdUrl.trim() || undefined,
          jd_text: jdText || undefined,
          notes: notes || undefined,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const created = await r.json();
      reset();
      onCreated(created.id);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal show={show} onHide={onHide} size="lg">
      <Form onSubmit={handleSubmit}>
        <Modal.Header closeButton>
          <Modal.Title>New submission</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {error && <Alert variant="danger">{error}</Alert>}

          <Row className="g-3">
            <Col md={6}>
              <Form.Group>
                <Form.Label>Company</Form.Label>
                <Form.Control
                  list="companies-list"
                  value={companyName}
                  onChange={(e) => setCompanyName(e.target.value)}
                  placeholder="Existing or new company"
                />
                <datalist id="companies-list">
                  {companies.map((c) => (
                    <option key={c.id} value={c.name} />
                  ))}
                </datalist>
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>Role title</Form.Label>
                <Form.Control
                  value={roleTitle}
                  onChange={(e) => setRoleTitle(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={4}>
              <Form.Group>
                <Form.Label>Status</Form.Label>
                <Form.Select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  {STATUSES.map((s) => (
                    <option key={s.short_name} value={s.short_name}>
                      {s.label}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={4}>
              <Form.Group>
                <Form.Label>Submitted on</Form.Label>
                <Form.Control
                  type="date"
                  value={submittedOn}
                  onChange={(e) => setSubmittedOn(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={4}>
              <Form.Group>
                <Form.Label>JD URL</Form.Label>
                <Form.Control
                  type="url"
                  value={jdUrl}
                  onChange={(e) => setJdUrl(e.target.value)}
                  placeholder="https://..."
                />
              </Form.Group>
            </Col>
            <Col md={12}>
              <Form.Group>
                <Form.Label>JD text</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={6}
                  value={jdText}
                  onChange={(e) => setJdText(e.target.value)}
                  placeholder="Paste the job description body here"
                />
                <Form.Text className="text-muted">
                  Archived to S3 on save. Used for AI tailoring later.
                </Form.Text>
              </Form.Group>
            </Col>
            <Col md={12}>
              <Form.Group>
                <Form.Label>Notes</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={3}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />
              </Form.Group>
            </Col>
          </Row>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving}>
            {saving ? 'Saving…' : 'Create submission'}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}