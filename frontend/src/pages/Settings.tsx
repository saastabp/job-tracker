import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Card, Button, Alert, Spinner, Badge } from 'react-bootstrap';
import { useApi } from '../api/client';
import { useGmailStatus } from '../api/gmail';

const GMAIL_SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send';

function shortScope(scope: string): string {
  // Trim "https://www.googleapis.com/auth/" prefix for compact display.
  return scope.replace(/^https:\/\/www\.googleapis\.com\/auth\//, '');
}

export default function Settings() {
  const apiFetch = useApi();
  const { status, loading, error, refresh } = useGmailStatus();
  const [searchParams, setSearchParams] = useSearchParams();
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionInProgress, setActionInProgress] = useState<string | null>(null);

  // Detect post-OAuth redirect query params and surface to the user.
  // Capture into state on mount so banners survive the URL strip below
  // (a derived `searchParams.get(...)` would flip to null on the
  // post-strip re-render, blanking the banner before the user sees it).
  const [oauthSuccess] = useState(
    searchParams.get('gmail_connected') === '1',
  );
  const [oauthError] = useState(searchParams.get('gmail_error'));

  useEffect(() => {
    if (oauthSuccess || oauthError) {
      // Refresh status once the redirect has been handled, then clear the
      // query params so a refresh doesn't re-fire the banner.
      refresh();
      const next = new URLSearchParams(searchParams);
      next.delete('gmail_connected');
      next.delete('gmail_error');
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleConnect() {
    setActionError(null);
    setActionInProgress('connect');
    try {
      const r = await apiFetch('/integrations/gmail/oauth/start');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: { consent_url: string } = await r.json();
      // Full-page redirect to Google. The OAuth callback will land back
      // at this page with ?gmail_connected=1 or ?gmail_error=...
      window.location.href = data.consent_url;
    } catch (e) {
      setActionError(`Connect failed: ${e}`);
      setActionInProgress(null);
    }
  }

  async function handleDisconnect() {
    if (!window.confirm('Disconnect Gmail? Existing thread links stay; new responses stop arriving.')) {
      return;
    }
    setActionError(null);
    setActionInProgress('disconnect');
    try {
      const r = await apiFetch('/integrations/gmail', { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      refresh();
    } catch (e) {
      setActionError(`Disconnect failed: ${e}`);
    } finally {
      setActionInProgress(null);
    }
  }

  if (loading && !status) {
    return <Spinner animation="border" size="sm" />;
  }

  if (error) {
    return <Alert variant="danger">Settings error: {error}</Alert>;
  }

  if (!status || !status.available) {
    return (
      <>
        <h3 className="mb-4">Settings</h3>
        <Card>
          <Card.Body>
            <Card.Title>Gmail integration</Card.Title>
            <p className="text-muted mb-0">
              The Gmail integration isn't deployed in this environment. To
              enable, deploy the gmail stack
              (<code>make deploy-gmail</code>) and re-deploy the api stack
              so the OAuth and admin routes register.
            </p>
          </Card.Body>
        </Card>
      </>
    );
  }

  const hasSend = status.scopes.includes(GMAIL_SEND_SCOPE);

  return (
    <>
      <h3 className="mb-4">Settings</h3>

      {oauthSuccess && (
        <Alert variant="success" dismissible onClose={() => {}}>
          Gmail connected.
        </Alert>
      )}
      {oauthError && (
        <Alert variant="danger" dismissible onClose={() => {}}>
          Gmail connect failed: <code>{oauthError}</code>. Try again from
          the Connect button below.
        </Alert>
      )}
      {actionError && <Alert variant="danger">{actionError}</Alert>}

      <Card>
        <Card.Body>
          <Card.Title>Gmail integration</Card.Title>
          <Card.Subtitle className="text-muted mb-3">
            Pulls recruiter responses into linked submissions and lets you
            compose / reply from inside the app.
          </Card.Subtitle>

          {!status.connected && (
            <>
              <p className="mb-3">
                Not connected. Connecting opens Google's consent screen
                and grants this app permission to read messages in linked
                threads and send messages on your behalf.
              </p>
              <Button onClick={handleConnect} disabled={actionInProgress !== null}>
                {actionInProgress === 'connect'
                  ? 'Redirecting to Google…'
                  : 'Connect Gmail'}
              </Button>
            </>
          )}

          {status.connected && (
            <>
              <dl className="row mb-3">
                <dt className="col-sm-3">Connected as</dt>
                <dd className="col-sm-9">
                  {status.gmail_address}{' '}
                  <Badge bg="success" className="ms-1">
                    active
                  </Badge>
                </dd>

                <dt className="col-sm-3">Permissions</dt>
                <dd className="col-sm-9">
                  {status.scopes.length > 0 ? (
                    status.scopes.map((s) => (
                      <Badge
                        key={s}
                        bg={s === GMAIL_SEND_SCOPE ? 'primary' : 'secondary'}
                        className="me-1"
                      >
                        {shortScope(s)}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-muted">none</span>
                  )}
                </dd>

                <dt className="col-sm-3">Last polled</dt>
                <dd className="col-sm-9">
                  {status.last_polled_at ?? (
                    <span className="text-muted">not yet</span>
                  )}
                </dd>
              </dl>

              {!hasSend && (
                <Alert variant="warning" className="mb-3">
                  <strong>Compose / Reply disabled:</strong> the
                  <code className="mx-1">gmail.send</code> permission isn't
                  granted yet. Click below to re-run the consent screen
                  with the upgraded scope set.
                  <div className="mt-2">
                    <Button
                      size="sm"
                      onClick={handleConnect}
                      disabled={actionInProgress !== null}
                    >
                      Grant send permission
                    </Button>
                  </div>
                </Alert>
              )}

              <Button
                variant="outline-danger"
                onClick={handleDisconnect}
                disabled={actionInProgress !== null}
              >
                {actionInProgress === 'disconnect'
                  ? 'Disconnecting…'
                  : 'Disconnect Gmail'}
              </Button>
            </>
          )}
        </Card.Body>
      </Card>
    </>
  );
}