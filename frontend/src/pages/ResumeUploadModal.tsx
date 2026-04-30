import { useEffect, useState } from 'react';
import { Modal, Button, Alert, Form } from 'react-bootstrap';
import { useApi } from '../api/client';
import PdfDropZone, { PDF_MAX_BYTES, formatFileSize } from '../components/PdfDropZone';

interface Props {
  show: boolean;
  onHide: () => void;
  resumeId: number | string;
  replacing: boolean;
  onUploaded: () => void;
}

export default function ResumeUploadModal({
  show,
  onHide,
  resumeId,
  replacing,
  onUploaded,
}: Props) {
  const apiFetch = useApi();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);

  useEffect(() => {
    if (show) {
      setFile(null);
      setError(null);
      setProgress(null);
    }
  }, [show]);

  async function handleUpload() {
    if (!file) return;
    setError(null);
    try {
      setProgress('Requesting upload URL…');
      const urlResp = await apiFetch(`/resumes/${resumeId}/upload-url`, {
        method: 'POST',
        body: JSON.stringify({
          content_type: file.type,
          original_filename: file.name,
          content_length: file.size,
        }),
      });
      if (!urlResp.ok) {
        throw new Error(
          `upload-url failed: HTTP ${urlResp.status}: ${await urlResp.text()}`,
        );
      }
      const { url } = await urlResp.json();

      setProgress(`Uploading ${file.name} (${formatFileSize(file.size)})…`);
      const putResp = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': file.type },
        body: file,
      });
      if (!putResp.ok) throw new Error(`S3 PUT failed: HTTP ${putResp.status}`);

      setProgress(null);
      onUploaded();
      onHide();
    } catch (err) {
      setProgress(null);
      setError(String(err));
    }
  }

  const uploading = progress !== null;

  return (
    <Modal
      show={show}
      onHide={uploading ? undefined : onHide}
      backdrop={uploading ? 'static' : true}
    >
      <Modal.Header closeButton={!uploading}>
        <Modal.Title>{replacing ? 'Replace PDF' : 'Upload PDF'}</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        {error && <Alert variant="danger">{error}</Alert>}
        <PdfDropZone
          file={file}
          onFile={setFile}
          onError={setError}
          disabled={uploading}
        />
        <Form.Text className="d-block mt-3 text-muted">
          PDF only, max {formatFileSize(PDF_MAX_BYTES)}.
        </Form.Text>
        {progress && (
          <div className="mt-3 text-muted small">{progress}</div>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide} disabled={uploading}>
          Cancel
        </Button>
        <Button onClick={handleUpload} disabled={!file || uploading}>
          {uploading ? 'Uploading…' : 'Upload'}
        </Button>
      </Modal.Footer>
    </Modal>
  );
}