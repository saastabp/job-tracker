import { useCallback, useEffect, useState } from 'react';
import { useApi } from './client';

/** Reflects the JSON shape returned by GET /integrations/gmail/status. */
export interface GmailStatus {
  connected: boolean;
  gmail_address: string | null;
  scopes: string[];
  last_polled_at: string | null;
}

/** Set when the gmail stack isn't deployed (the api stack returns 404 for
 * the route). The SPA hides Gmail UI rather than showing scary errors. */
export interface GmailUnavailable {
  available: false;
}

export type GmailStatusResult =
  | ({ available: true } & GmailStatus)
  | GmailUnavailable;

const GMAIL_SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send';

export function hasSendScope(status: GmailStatusResult | null): boolean {
  return Boolean(
    status &&
      status.available &&
      status.connected &&
      status.scopes.includes(GMAIL_SEND_SCOPE),
  );
}

/** React hook: fetch /integrations/gmail/status on mount. Returns the result
 * plus a `refresh` callback for re-fetching after Connect / Disconnect /
 * scope upgrade. A 404 from the backend (gmail stack absent) collapses to
 * `{ available: false }` so callers can hide UI cleanly. */
export function useGmailStatus(): {
  status: GmailStatusResult | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const apiFetch = useApi();
  const [status, setStatus] = useState<GmailStatusResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch('/integrations/gmail/status');
      if (r.status === 404) {
        setStatus({ available: false });
        return;
      }
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const data: GmailStatus = await r.json();
      setStatus({ available: true, ...data });
    } catch (e) {
      setError(String(e));
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  return { status, loading, error, refresh: fetchStatus };
}