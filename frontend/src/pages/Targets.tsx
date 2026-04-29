import { useEffect, useState } from 'react';
import { Form, Button, Table, Spinner, Alert, Card } from 'react-bootstrap';
import { useApi } from '../api/client';

interface TargetType {
  id: number;
  short_name: string;
  description: string;
  daily: number | null;
  weekly: number | null;
}

type Edits = Record<number, { daily: string; weekly: string }>;

function toEdits(types: TargetType[]): Edits {
  return Object.fromEntries(
    types.map((t) => [
      t.id,
      {
        daily: t.daily?.toString() ?? '',
        weekly: t.weekly?.toString() ?? '',
      },
    ]),
  );
}

export default function Targets() {
  const apiFetch = useApi();
  const [types, setTypes] = useState<TargetType[] | null>(null);
  const [edits, setEdits] = useState<Edits>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<Date | null>(null);

  useEffect(() => {
    apiFetch('/targets')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: { types: TargetType[] }) => {
        setTypes(d.types);
        setEdits(toEdits(d.types));
      })
      .catch((e) => setError(String(e)));
  }, [apiFetch]);

  function setField(typeId: number, cadence: 'daily' | 'weekly', value: string) {
    setEdits((prev) => ({
      ...prev,
      [typeId]: { ...prev[typeId], [cadence]: value },
    }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!types) return;
    setSaving(true);
    setError(null);
    const goals: { target_type_id: number; cadence: string; goal_count: number }[] = [];
    for (const t of types) {
      for (const cadence of ['daily', 'weekly'] as const) {
        const raw = edits[t.id]?.[cadence] ?? '';
        if (raw === '') continue;
        const n = Number(raw);
        if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) {
          setError(`${t.description} ${cadence}: must be a non-negative integer`);
          setSaving(false);
          return;
        }
        goals.push({ target_type_id: t.id, cadence, goal_count: n });
      }
    }
    try {
      const r = await apiFetch('/targets', {
        method: 'PUT',
        body: JSON.stringify({ goals }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d: { types: TargetType[] } = await r.json();
      setTypes(d.types);
      setEdits(toEdits(d.types));
      setSavedAt(new Date());
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  if (error && !types) return <Alert variant="danger">{error}</Alert>;
  if (!types) return <Spinner animation="border" size="sm" />;

  return (
    <>
      <h3 className="mb-4">Targets</h3>
      <Card>
        <Card.Body>
          <Card.Subtitle className="text-muted mb-3">
            Daily and weekly goals for each activity type. Leave blank to clear.
          </Card.Subtitle>
          <Form onSubmit={handleSave}>
            <Table responsive borderless className="align-middle">
              <thead>
                <tr>
                  <th>Activity</th>
                  <th style={{ width: 160 }}>Daily goal</th>
                  <th style={{ width: 160 }}>Weekly goal</th>
                </tr>
              </thead>
              <tbody>
                {types.map((t) => (
                  <tr key={t.id}>
                    <td>{t.description}</td>
                    <td>
                      <Form.Control
                        type="number"
                        min={0}
                        value={edits[t.id]?.daily ?? ''}
                        onChange={(e) => setField(t.id, 'daily', e.target.value)}
                      />
                    </td>
                    <td>
                      <Form.Control
                        type="number"
                        min={0}
                        value={edits[t.id]?.weekly ?? ''}
                        onChange={(e) => setField(t.id, 'weekly', e.target.value)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
            {error && <Alert variant="danger">{error}</Alert>}
            <div className="d-flex align-items-center gap-3">
              <Button type="submit" disabled={saving}>
                {saving ? 'Saving…' : 'Save targets'}
              </Button>
              {savedAt && (
                <span className="text-muted small">
                  Saved at {savedAt.toLocaleTimeString()}
                </span>
              )}
            </div>
          </Form>
        </Card.Body>
      </Card>
    </>
  );
}