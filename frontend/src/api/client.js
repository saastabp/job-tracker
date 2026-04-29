import { useCallback } from 'react';
import { useAuth } from 'react-oidc-context';
const API_URL = import.meta.env.VITE_API_URL;
/** Hook returning a `fetch` wrapper that attaches the current Cognito access token. */
export function useApi() {
    const auth = useAuth();
    return useCallback(async (path, init = {}) => {
        const token = auth.user?.access_token;
        if (!token)
            throw new Error('Not authenticated');
        return fetch(`${API_URL}${path}`, {
            ...init,
            headers: {
                ...init.headers,
                Authorization: `Bearer ${token}`,
                'Content-Type': 'application/json',
            },
        });
    }, [auth.user?.access_token]);
}
