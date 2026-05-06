import { useEffect, useState } from 'react';
import { Modal, Form, Button, Alert } from 'react-bootstrap';
import { useApi } from '../api/client';

export interface CreatedCompany {
  id: number;
  name: string;
  notes: string | null;
}

interface Props {
  show: boolean;
  onHide: () => void;
  onCreated: (company: CreatedCompany) => void;
  initialName?: string;
}

export default function NewCompanyModal({
  show,
  onHide,
  onCreated,
  initialName = '',
}: Props) {
  const apiFetch = useApi();
  const [name, setName] = useState(initialName);
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (show) {
      setName(initialName);
      setNotes('');
      setError(null);
    }
  }, [show, initialName]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setSaving(true);
    setError(null);
    try {
      const r = await apiFetch('/companies', {
        method: 'POST',
        body: JSON.stringify({
          name: trimmed,
          notes: notes.trim() || undefined,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const created: CreatedCompany = await r.json();
      onCreated(created);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal show={show} onHide={onHide}>
      <Form onSubmit={handleSubmit}>
        <Modal.Header closeButton>
          <Modal.Title>New company</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {error && <Alert variant="danger">{error}</Alert>}
          <Form.Group className="mb-3">
            <Form.Label>Name</Form.Label>
            <Form.Control
              autoFocus
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Form.Group>
          <Form.Group>
            <Form.Label>Notes</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </Form.Group>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : 'Create company'}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}