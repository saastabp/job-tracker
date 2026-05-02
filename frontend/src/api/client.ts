import { useCallback } from 'react';
import { useAuth } from 'react-oidc-context';

const API_URL = import.meta.env.VITE_API_URL;
const AI_API_URL = import.meta.env.VITE_AI_API_URL;

export const aiEnabled = Boolean(AI_API_URL);

/**
 * Routes a request to the right HttpApi base. Paths under ``/ai/`` go to the
 * AI stack's URL (which lives in its own tearable-down SAM stack and is
 * absent until the AI stack is deployed). Everything else hits the main API.
 */
function resolveBase(path: string): string {
  if (path.startsWith('/ai/')) {
    if (!AI_API_URL) {
      throw new Error(
        'AI endpoint requested but VITE_AI_API_URL is not set — deploy infra/ai and re-run gen-frontend-env',
      );
    }
    return AI_API_URL;
  }
  return API_URL;
}

/** Hook returning a `fetch` wrapper that attaches the current Cognito access token. */
export function useApi() {
  const auth = useAuth();
  return useCallback(
    async (path: string, init: RequestInit = {}): Promise<Response> => {
      const token = auth.user?.access_token;
      if (!token) throw new Error('Not authenticated');
      return fetch(`${resolveBase(path)}${path}`, {
        ...init,
        headers: {
          ...init.headers,
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
      });
    },
    [auth.user?.access_token],
  );
}