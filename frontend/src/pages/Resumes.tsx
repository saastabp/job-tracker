import { useCallback, useEffect, useState } from 'react';
import {
  Table,
  Button,
  Spinner,
  Alert,
  Badge,
  Card,
  Form,
} from 'react-bootstrap';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../api/client';
import ResumeForm from './ResumeForm';

interface ResumeRow {
  id: number;
  title: string | null;
  summary: string | null;
  is_master: boolean;
  has_file: boolean;
  original_filename: string | null;
  submission_count: number;
  is_deleted: boolean;
  deleted_at: string | null;
}

export default function Resumes() {
  const apiFetch = useApi();
  const navigate = useNavigate();
  const [rows, setRows] = useState<ResumeRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);
  const [busyRow, setBusyRow] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const path = showDeleted
        ? '/resumes?include_deleted=true'
        : '/resumes';
      const r = await apiFetch(path);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows(await r.json());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [apiFetch, showDeleted]);

  useEffect(() => {
    load();
  }, [load]);

  function handleCreated(newId: number) {
    setShowForm(false);
    navigate(`/resumes/${newId}`);
  }

  async function restoreRow(id: number) {
    setBusyRow(id);
    try {
      const r = await apiFetch(`/resumes/${id}/restore`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyRow(null);
    }
  }

  async function purgeRow(id: number, title: string | null) {
    if (
      !window.confirm(
        `Permanently delete "${title ?? `resume #${id}`}"? This cannot be undone — ` +
          `the PDF and all metadata will be removed, and any submissions linked to this ` +
          `resume will lose the link.`,
      )
    ) {
      return;
    }
    setBusyRow(id);
    try {
      const r = await apiFetch(`/resumes/${id}/purge`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyRow(null);
    }
  }

  return (
    <>
      <div className="d-flex align-items-center justify-content-between mb-4">
        <h3 className="mb-0">Resumes</h3>
        <div className="d-flex align-items-center gap-3">
          <Form.Check
            type="switch"
            id="show-deleted-switch"
            label="Show deleted"
            checked={showDeleted}
            onChange={(e) => setShowDeleted(e.target.checked)}
          />
          <Button onClick={() => setShowForm(true)}>+ New resume</Button>
        </div>
      </div>

      {error && <Alert variant="danger">{error}</Alert>}
      {!rows ? (
        <Spinner animation="border" size="sm" />
      ) : rows.length === 0 ? (
        <Card>
          <Card.Body className="text-muted">
            {showDeleted
              ? 'No resumes (including deleted ones).'
              : 'No resumes yet. Click + New resume to create one. Your first resume becomes your master automatically.'}
          </Card.Body>
        </Card>
      ) : (
        <Table hover responsive className="align-middle">
          <thead>
            <tr>
              <th>Title</th>
              <th>Master</th>
              <th>File</th>
              <th>Submissions</th>
              {showDeleted && <th>Status</th>}
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const muted = row.is_deleted;
              const rowStyle: React.CSSProperties = {
                cursor: muted ? 'default' : 'pointer',
                opacity: muted ? 0.55 : 1,
              };
              return (
                <tr
                  key={row.id}
                  style={rowStyle}
                  onClick={() => {
                    if (!muted) navigate(`/resumes/${row.id}`);
                  }}
                >
                  <td>
                    {row.title || (
                      <span className="text-muted">Untitled</span>
                    )}
                  </td>
                  <td>
                    {row.is_master ? (
                      <Badge bg="primary">master</Badge>
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                  <td>
                    {row.has_file ? (
                      <span>{row.original_filename ?? 'attached'}</span>
                    ) : (
                      <span className="text-muted">no file</span>
                    )}
                  </td>
                  <td>{row.submission_count}</td>
                  {showDeleted && (
                    <td>
                      {row.is_deleted ? (
                        <Badge bg="warning" text="dark">
                          deleted {row.deleted_at?.slice(0, 10)}
                        </Badge>
                      ) : (
                        <Badge bg="success">live</Badge>
                      )}
                    </td>
                  )}
                  <td className="text-end">
                    {row.is_deleted && (
                      <span onClick={(e) => e.stopPropagation()}>
                        <Button
                          size="sm"
                          variant="outline-primary"
                          className="me-2"
                          disabled={busyRow === row.id}
                          onClick={() => restoreRow(row.id)}
                        >
                          Restore
                        </Button>
                        <Button
                          size="sm"
                          variant="outline-danger"
                          disabled={busyRow === row.id}
                          onClick={() => purgeRow(row.id, row.title)}
                        >
                          Delete forever
                        </Button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}

      <ResumeForm
        show={showForm}
        onHide={() => setShowForm(false)}
        onCreated={handleCreated}
        suggestMaster={
          rows !== null && rows.filter((r) => !r.is_deleted).length === 0
        }
      />
    </>
  );
}