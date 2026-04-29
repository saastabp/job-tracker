import { WebStorageStateStore } from 'oidc-client-ts';
import type { AuthProviderProps } from 'react-oidc-context';

const redirectUri =
  import.meta.env.VITE_REDIRECT_URL || `${window.location.origin}/`;

export const oidcConfig: AuthProviderProps = {
  authority: import.meta.env.VITE_COGNITO_AUTHORITY,
  client_id: import.meta.env.VITE_COGNITO_CLIENT_ID,
  redirect_uri: redirectUri,
  response_type: 'code',
  scope: 'openid profile email',
  userStore: new WebStorageStateStore({ store: window.localStorage }),
  // Strip ?code=…&state=… from URL after sign-in completes:
  onSigninCallback: () => {
    window.history.replaceState({}, document.title, window.location.pathname);
  },
};

// Cognito's /logout endpoint isn't in the standard OIDC discovery doc, so
// signoutRedirect() doesn't auto-work. Hand-rolled redirect:
export function cognitoLogout(): void {
  const domain = import.meta.env.VITE_COGNITO_DOMAIN;
  const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
  const url = new URL(`https://${domain}/logout`);
  url.searchParams.set('client_id', clientId);
  url.searchParams.set('logout_uri', redirectUri);
  window.location.href = url.toString();
}