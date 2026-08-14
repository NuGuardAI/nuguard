/**
 * Google Identity Services (GIS) OAuth 2.0 Token Client
 * Handles sign-in, token acquisition, and user profile fetching.
 */

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_OAUTH_CLIENT_ID;

const SCOPES = [
  'https://www.googleapis.com/auth/gmail.send',
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/calendar',
  'https://www.googleapis.com/auth/calendar.events',
  'https://www.googleapis.com/auth/userinfo.email',
  'https://www.googleapis.com/auth/userinfo.profile',
].join(' ');

export interface GoogleUser {
  name: string;
  email: string;
  picture: string;
}

let accessToken: string | null = null;
let tokenClient: any = null;

function loadGISScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if ((window as any).google?.accounts) {
      resolve();
      return;
    }
    if (document.getElementById('gis-script')) {
      // Script already injected — wait for it
      const interval = setInterval(() => {
        if ((window as any).google?.accounts) {
          clearInterval(interval);
          resolve();
        }
      }, 100);
      return;
    }
    const script = document.createElement('script');
    script.id = 'gis-script';
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Failed to load Google Identity Services'));
    document.head.appendChild(script);
  });
}

export async function initGoogleAuth(): Promise<void> {
  await loadGISScript();
}

export function requestAccessToken(): Promise<string> {
  return new Promise(async (resolve, reject) => {
    await loadGISScript();
    const google = (window as any).google;
    if (!google?.accounts?.oauth2) {
      reject(new Error('Google Identity Services not available'));
      return;
    }
    if (!tokenClient) {
      tokenClient = google.accounts.oauth2.initTokenClient({
        client_id: GOOGLE_CLIENT_ID,
        scope: SCOPES,
        callback: (response: any) => {
          if (response.error) {
            reject(new Error(response.error_description || response.error));
            return;
          }
          accessToken = response.access_token;
          resolve(response.access_token);
        },
      });
    }
    tokenClient.requestAccessToken({ prompt: accessToken ? '' : 'consent' });
  });
}

export function getStoredToken(): string | null {
  return accessToken;
}

export function clearToken(): void {
  const google = (window as any).google;
  if (google?.accounts?.oauth2 && accessToken) {
    google.accounts.oauth2.revoke(accessToken, () => {});
  }
  accessToken = null;
  tokenClient = null;
}

export async function fetchUserProfile(token: string): Promise<GoogleUser> {
  const resp = await fetch('https://www.googleapis.com/oauth2/v2/userinfo', {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error('Failed to fetch Google user profile');
  const data = await resp.json();
  return { name: data.name, email: data.email, picture: data.picture };
}

/**
 * Invalidate a session token on the server (for 3rd-party automation tools).
 * Call this after /api/auth/login to cleanly end an automated session.
 */
export async function apiLogout(sessionToken: string): Promise<void> {
  await fetch('/api/auth/logout', {
    method: 'POST',
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
}
