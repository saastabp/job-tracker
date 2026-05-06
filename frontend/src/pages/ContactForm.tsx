import { useEffect, useState } from 'react';
import { Modal, Form, Button, Alert, Row, Col } from 'react-bootstrap';
import { useApi } from '../api/client';
import NewCompanyModal from '../components/NewCompanyModal';

const NEW_COMPANY_SENTINEL = '__new__';

export const KINDS: { short_name: string; label: string }[] = [
  { short_name: 'personal', label: 'Personal' },
  { short_name: 'recruiter', label: 'Recruiter' },
];

export const METHODS: { short_name: string; label: string }[] = [
  { short_name: 'email', label: 'Email' },
  { short_name: 'linkedin', label: 'LinkedIn' },
  { short_name: 'phone', label: 'Phone' },
  { short_name: 'in_person', label: 'In person' },
  { short_name: 'other', label: 'Other' },
];

export const DIRECTIONS: { short_name: string; label: string }[] = [
  { short_name: 'outbound', label: 'Outbound (I reached out)' },
  { short_name: 'inbound', label: 'Inbound (they reached out)' },
];

interface CompanyOption {
  id: number;
  name: string;
}

interface Props {
  show: boolean;
  onHide: () => void;
  onCreated: (newId: number) => void;
  defaultKind?: string;
}

export default function ContactForm({
  show,
  onHide,
  onCreated,
  defaultKind = 'personal',
}: Props) {
  const apiFetch = useApi();
  const [companies, setCompanies] = useState<CompanyOption[]>([]);
  const [name, setName] = useState('');
  const [kind, setKind] = useState(defaultKind);
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [linkedinUrl, setLinkedinUrl] = useState('');
  const [primaryMethod, setPrimaryMethod] = useState('');
  const [companyId, setCompanyId] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showNewCompany, setShowNewCompany] = useState(false);

  useEffect(() => {
    if (!show) return;
    setKind(defaultKind);
    apiFetch('/companies')
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) =>
        setCompanies(rows.map((c: any) => ({ id: c.id, name: c.name }))),
      )
      .catch(() => setCompanies([]));
  }, [show, defaultKind, apiFetch]);

  function reset() {
    setName('');
    setKind(defaultKind);
    setEmail('');
    setPhone('');
    setLinkedinUrl('');
    setPrimaryMethod('');
    setCompanyId('');
    setNotes('');
    setError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const r = await apiFetch('/contacts', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          kind,
          email: email.trim() || undefined,
          phone: phone.trim() || undefined,
          linkedin_url: linkedinUrl.trim() || undefined,
          primary_method: primaryMethod || undefined,
          company_id: companyId ? Number(companyId) : undefined,
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
    <>
      <Modal show={show} onHide={onHide} size="lg">
      <Form onSubmit={handleSubmit}>
        <Modal.Header closeButton>
          <Modal.Title>New contact</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {error && <Alert variant="danger">{error}</Alert>}
          <Row className="g-3">
            <Col md={8}>
              <Form.Group>
                <Form.Label>Name</Form.Label>
                <Form.Control
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={4}>
              <Form.Group>
                <Form.Label>Kind</Form.Label>
                <Form.Select
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
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
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>Phone</Form.Label>
                <Form.Control
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>LinkedIn URL</Form.Label>
                <Form.Control
                  type="url"
                  value={linkedinUrl}
                  onChange={(e) => setLinkedinUrl(e.target.value)}
                  placeholder="https://linkedin.com/in/…"
                />
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group>
                <Form.Label>Preferred outreach method</Form.Label>
                <Form.Select
                  value={primaryMethod}
                  onChange={(e) => setPrimaryMethod(e.target.value)}
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
                  value={companyId}
                  onChange={(e) => {
                    if (e.target.value === NEW_COMPANY_SENTINEL) {
                      setShowNewCompany(true);
                    } else {
                      setCompanyId(e.target.value);
                    }
                  }}
                >
                  <option value="">— none —</option>
                  <option value={NEW_COMPANY_SENTINEL}>
                    + Add new company…
                  </option>
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
          <Button type="submit" disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : 'Create contact'}
          </Button>
        </Modal.Footer>
      </Form>
      </Modal>
      <NewCompanyModal
        show={showNewCompany}
        onHide={() => setShowNewCompany(false)}
        onCreated={(company) => {
          setCompanies((prev) =>
            [...prev, { id: company.id, name: company.name }].sort((a, b) =>
              a.name.localeCompare(b.name),
            ),
          );
          setCompanyId(String(company.id));
          setShowNewCompany(false);
        }}
      />
    </>
  );
}