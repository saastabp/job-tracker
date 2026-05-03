import { useCallback, useEffect, useState } from 'react';
import {
  Card,
  Form,
  Button,
  Row,
  Col,
  Spinner,
  Alert,
  Badge,
  ListGroup,
} from 'react-bootstrap';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useApi } from '../api/client';
import ResumeUploadModal from './ResumeUploadModal';

interface SubmissionLink {
  id: number;
  role_title: string | null;
  submitted_on: string | null;
  status: string;
  company_name: string | null;
}

interface ResumeDetailData {
  id: number;
  title: string | null;
  summary: string | null;
  is_master: boolean;
  has_file: boolean;
  original_filename: string | null;
  submission_count: number;
  download_url: string | null;
  submissions: SubmissionLink[];
}

export default function ResumeDetail() {
  const apiFetch = useApi();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();

  const [data, setData] = useState<ResumeDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editSummary, setEditSummary] = useState('');
  const [savingField, setSavingField] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await apiFetch(`/resumes/${id}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d: ResumeDetailData = await r.json();
      setData(d);
      setEditTitle(d.title ?? '');
      setEditSummary(d.summary ?? '');
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [apiFetch, id]);

  useEffect(() => {
    load();
  }, [load]);

  async function patchField(field: string, value: unknown) {
    setSavingField(field);
    try {
      const r = await apiFetch(`/resumes/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ [field]: value }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingField(null);
    }
  }

  async function handleSaveText() {
    if (!data) return;
    const body: Record<string, unknown> = {};
    if (editTitle.trim() !== (data.title ?? '')) body.title = editTitle.trim();
    if ((editSummary || null) !== (data.summary ?? null)) {
      body.summary = editSummary || null;
    }
    if (Object.keys(body).length === 0) return;
    setSavingField('save');
    try {
      const r = await apiFetch(`/resumes/${id}`, {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingField(null);
    }
  }

  async function handleDelete() {
    if (!data) return;
    if (
      !window.confirm(
        `Delete resume "${data.title}"?\n\n` +
          `This is reversible — toggle "Show deleted" on the Resumes list to ` +
          `restore it, or to permanently remove it.`,
      )
    ) {
      return;
    }
    setSavingField('delete');
    try {
      const r = await apiFetch(`/resumes/${id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      navigate('/resumes');
    } catch (e) {
      setError(String(e));
      setSavingField(null);
    }
  }

  if (error && !data) return <Alert variant="danger">{error}</Alert>;
  if (!data) return <Spinner animation="border" size="sm" />;

  const dirty =
    editTitle.trim() !== (data.title ?? '') ||
    (editSummary || null) !== (data.summary ?? null);

  return (
    <>
      <div className="d-flex align-items-center mb-3">
        <Link to="/resumes" className="me-3">
          ← Back
        </Link>
        <h3 className="mb-0">
          {data.title || <span className="text-muted">Untitled</span>}
        </h3>
        {data.is_master && (
          <Badge bg="primary" className="ms-3">
            master
          </Badge>
        )}
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      <Row className="g-3 mb-3">
        <Col md={8}>
          <Card>
            <Card.Body>
              <Form.Group className="mb-3">
                <Form.Label>Title</Form.Label>
                <Form.Control
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                />
              </Form.Group>
              <Form.Group>
                <Form.Label>Summary</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={8}
                  value={editSummary}
                  onChange={(e) => setEditSummary(e.target.value)}
                />
              </Form.Group>
              <div className="mt-3">
                <Button
                  onClick={handleSaveText}
                  disabled={!dirty || savingField === 'save'}
                >
                  {savingField === 'save' ? 'Saving…' : 'Save changes'}
                </Button>
              </div>
            </Card.Body>
          </Card>
        </Col>
        <Col md={4}>
          <Card className="mb-3">
            <Card.Body>
              <Card.Subtitle className="text-muted mb-2">Master</Card.Subtitle>
              <Form.Check
                type="switch"
                id="resume-master-switch"
                label={data.is_master ? 'Master resume' : 'Set as master'}
                checked={data.is_master}
                disabled={savingField === 'is_master'}
                onChange={(e) => patchField('is_master', e.target.checked)}
              />
              <Form.Text className="text-muted">
                Only one resume per account is the master at a time.
              </Form.Text>
            </Card.Body>
          </Card>

          <Card className="mb-3">
            <Card.Body>
              <Card.Subtitle className="text-muted mb-2">File</Card.Subtitle>
              {data.has_file ? (
                <>
                  <div className="mb-2">
                    {data.original_filename ?? 'attached'}
                    {data.download_url && (
                      <a
                        href={data.download_url}
                        target="_blank"
                        rel="noreferrer"
                        className="ms-2"
                      >
                        Open
                      </a>
                    )}
                  </div>
                </>
              ) : (
                <div className="text-muted mb-2">No file attached.</div>
              )}
              <Button size="sm" onClick={() => setShowUpload(true)}>
                {data.has_file ? 'Replace PDF' : 'Upload PDF'}
              </Button>
              <Form.Text className="d-block mt-2 text-muted">
                PDF only, max 5 MB.
              </Form.Text>
            </Card.Body>
          </Card>

          <Card>
            <Card.Body>
              <Button
                variant="outline-danger"
                size="sm"
                onClick={handleDelete}
                disabled={savingField === 'delete'}
              >
                {savingField === 'delete' ? 'Deleting…' : 'Delete resume'}
              </Button>
            </Card.Body>
          </Card>
        </Col>
      </Row>

      {data.has_file && data.download_url && (
        <Card className="mb-3">
          <Card.Body>
            <Card.Subtitle className="text-muted mb-2">Preview</Card.Subtitle>
            <iframe
              src={data.download_url}
              title="Resume PDF preview"
              style={{ width: '100%', height: 600, border: '1px solid #dee2e6' }}
            />
          </Card.Body>
        </Card>
      )}

      <Card>
        <Card.Body>
          <Card.Subtitle className="text-muted mb-2">
            Submissions using this resume
          </Card.Subtitle>
          {data.submissions.length === 0 ? (
            <div className="text-muted">None yet.</div>
          ) : (
            <ListGroup variant="flush">
              {data.submissions.map((s) => (
                <ListGroup.Item key={s.id}>
                  <Link to={`/submissions/${s.id}`}>
                    {s.role_title || '(untitled role)'}
                  </Link>
                  {s.company_name && <span className="text-muted"> — {s.company_name}</span>}
                  <span className="text-muted ms-2">{s.submitted_on ?? ''}</span>
                  <Badge bg="secondary" className="ms-2">
                    {s.status}
                  </Badge>
                </ListGroup.Item>
              ))}
            </ListGroup>
          )}
        </Card.Body>
      </Card>

      {id && (
        <ResumeUploadModal
          show={showUpload}
          onHide={() => setShowUpload(false)}
          resumeId={id}
          replacing={!!data.has_file}
          onUploaded={load}
        />
      )}
    </>
  );
}