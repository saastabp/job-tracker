import { useEffect, useState } from 'react';
import { Modal, Form, Button, Alert, Row, Col, Badge } from 'react-bootstrap';
import { aiEnabled, useApi } from '../api/client';
import PdfDropZone, { PDF_MAX_BYTES, formatFileSize } from '../components/PdfDropZone';

interface Props {
  show: boolean;
  onHide: () => void;
  onCreated: (newId: number) => void;
  /** True when the user has no resumes yet — UI nudges them that this will become master. */
  suggestMaster?: boolean;
}

type FieldOrigin = 'user' | 'ai';

export default function ResumeForm({ show, onHide, onCreated, suggestMaster }: Props) {
  const apiFetch = useApi();
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [titleOrigin, setTitleOrigin] = useState<FieldOrigin>('user');
  const [summaryOrigin, setSummaryOrigin] = useState<FieldOrigin>('user');
  const [isMaster, setIsMaster] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiHint, setAiHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (show) {
      setTitle('');
      setSummary('');
      setTitleOrigin('user');
      setSummaryOrigin('user');
      setIsMaster(false);
      setFile(null);
      setProgress(null);
      setAiBusy(false);
      setAiHint(null);
      setError(null);
    }
  }, [show]);

  async function handleFileChange(f: File | null) {
    setFile(f);
    if (!f) return;
    if (!aiEnabled) {
      setAiHint('AI auto-fill is currently disabled — fill in title/summary manually.');
      return;
    }
    if (title.trim() || summary.trim()) {
      // Don't clobber what the user has already typed.
      setAiHint('PDF set. Title/summary already filled — AI auto-prefill skipped.');
      return;
    }

    setAiBusy(true);
    setAiHint('Reading PDF…');
    try {
      // Lazy import: pdfjs-dist + its worker only ship when a user actually
      // drops a PDF, keeping the initial SPA bundle lean.
      const { extractPdfText } = await import('../components/pdfText');
      const text = await extractPdfText(f);
      if (!text.trim()) throw new Error('PDF appeared to contain no extractable text');
      setAiHint('Asking AI for a title + summary…');
      const r = await apiFetch('/ai/mine-resume', {
        method: 'POST',
        body: JSON.stringify({ text }),
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status}: ${txt}`);
      }
      const { title: aiTitle, summary: aiSummary } = await r.json();
      setTitle(aiTitle);
      setSummary(aiSummary);
      setTitleOrigin('ai');
      setSummaryOrigin('ai');
      setAiHint('Auto-filled from your PDF — review and edit before saving.');
    } catch (err) {
      setAiHint(`Couldn't auto-fill from PDF (${String(err)}). Fill in manually.`);
    } finally {
      setAiBusy(false);
    }
  }

  async function uploadFile(resumeId: number, f: File) {
    setProgress('Requesting upload URL…');
    const urlResp = await apiFetch(`/resumes/${resumeId}/upload-url`, {
      method: 'POST',
      body: JSON.stringify({
        content_type: f.type,
        original_filename: f.name,
        content_length: f.size,
      }),
    });
    if (!urlResp.ok) {
      throw new Error(
        `upload-url failed: HTTP ${urlResp.status}: ${await urlResp.text()}`,
      );
    }
    const { url } = await urlResp.json();

    setProgress(`Uploading ${f.name} (${formatFileSize(f.size)})…`);
    const putResp = await fetch(url, {
      method: 'PUT',
      headers: { 'Content-Type': f.type },
      body: f,
    });
    if (!putResp.ok) throw new Error(`S3 PUT failed: HTTP ${putResp.status}`);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setProgress('Creating resume…');
      const r = await apiFetch('/resumes', {
        method: 'POST',
        body: JSON.stringify({
          title: title.trim(),
          summary: summary || undefined,
          is_master: isMaster || undefined,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const created = await r.json();

      if (file) {
        await uploadFile(created.id, file);
      }

      setProgress(null);
      onCreated(created.id);
    } catch (err) {
      setProgress(null);
      setError(String(err));
    }
  }

  const busy = progress !== null || aiBusy;

  return (
    <Modal
      show={show}
      onHide={busy ? undefined : onHide}
      size="lg"
      backdrop={busy ? 'static' : true}
    >
      <Form onSubmit={handleSubmit}>
        <Modal.Header closeButton={!busy}>
          <Modal.Title>New resume</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {error && <Alert variant="danger">{error}</Alert>}
          {suggestMaster && (
            <Alert variant="info">
              This will be your first resume — it'll be marked as your master automatically.
            </Alert>
          )}

          <Row className="g-3">
            <Col md={12}>
              <Form.Group>
                <Form.Label className="d-flex align-items-center gap-2">
                  Title
                  {titleOrigin === 'ai' && (
                    <Badge bg="info">auto-filled — edit if needed</Badge>
                  )}
                </Form.Label>
                <Form.Control
                  required
                  value={title}
                  onChange={(e) => {
                    setTitle(e.target.value);
                    setTitleOrigin('user');
                  }}
                  placeholder="e.g., Master — SRE / Platform Engineering"
                  disabled={busy}
                />
                <Form.Text className="text-muted">
                  How you'll recognize this resume in lists.
                </Form.Text>
              </Form.Group>
            </Col>
            <Col md={12}>
              <Form.Group>
                <Form.Label className="d-flex align-items-center gap-2">
                  Summary
                  {summaryOrigin === 'ai' && (
                    <Badge bg="info">auto-filled — edit if needed</Badge>
                  )}
                </Form.Label>
                <Form.Control
                  as="textarea"
                  rows={6}
                  value={summary}
                  onChange={(e) => {
                    setSummary(e.target.value);
                    setSummaryOrigin('user');
                  }}
                  placeholder="Paste your master summary / professional statement here."
                  disabled={busy}
                />
                <Form.Text className="text-muted">
                  Used as a voice/style exemplar for AI-tailored summaries later.
                </Form.Text>
              </Form.Group>
            </Col>
            <Col md={12}>
              <Form.Group>
                <Form.Label>PDF (optional)</Form.Label>
                <PdfDropZone
                  file={file}
                  onFile={handleFileChange}
                  onError={setError}
                  disabled={busy}
                />
                <Form.Text className="d-block mt-2 text-muted">
                  PDF only, max {formatFileSize(PDF_MAX_BYTES)}. You can upload later from the detail page.
                  Dropping a PDF here while title/summary are blank will ask AI for a starting point.
                </Form.Text>
                {aiHint && (
                  <div className="text-muted small mt-2">{aiHint}</div>
                )}
              </Form.Group>
            </Col>
            {!suggestMaster && (
              <Col md={12}>
                <Form.Check
                  type="checkbox"
                  id="resume-is-master"
                  label="Mark as master (replaces the current master)"
                  checked={isMaster}
                  onChange={(e) => setIsMaster(e.target.checked)}
                  disabled={busy}
                />
              </Col>
            )}
          </Row>

          {progress && (
            <div className="text-muted small mt-3">{progress}</div>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy || !title.trim()}>
            {busy ? 'Saving…' : 'Create resume'}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}