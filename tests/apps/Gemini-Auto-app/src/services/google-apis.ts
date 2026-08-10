/**
 * Google APIs — Gmail (send) and Calendar (create/list events).
 * Uses the stored OAuth access token from google-auth.ts.
 */

import { getStoredToken, requestAccessToken, clearToken } from './google-auth';

async function getToken(): Promise<string> {
  return getStoredToken() ?? requestAccessToken();
}

/** Parse a Google API error response body and throw a typed Error. */
async function throwGoogleError(resp: Response, context: string): Promise<never> {
  const body = await resp.json().catch(() => ({}));
  const msg: string = body?.error?.message ?? `${context} failed (${resp.status})`;
  const status: string = body?.error?.status ?? '';

  // Insufficient OAuth scope — the token was issued before these scopes were
  // added to the consent screen. Force the user to sign in again.
  if (
    resp.status === 403 &&
    (status === 'PERMISSION_DENIED' && msg.toLowerCase().includes('scope')) ||
    msg.includes('insufficientPermissions') ||
    msg.includes('insufficient authentication scopes')
  ) {
    clearToken();
    throw new Error(
      'Google sign-in needs to be refreshed to include Calendar / Gmail permissions. ' +
      'Please click the sign-in button in the sidebar to re-authorize.'
    );
  }

  throw new Error(msg);
}

export interface CalendarEvent {
  id: string;
  summary: string;
  start: string;
  end: string;
  location?: string;
}

/**
 * Send an email via the Gmail REST API.
 * `to` must be a valid email address or "Name <email>" format.
 */
export async function sendGmail(
  to: string,
  subject: string,
  body: string
): Promise<void> {
  const token = await getToken();

  // Build RFC 2822 message
  const rawMessage = [
    `To: ${to}`,
    `Subject: =?UTF-8?B?${btoa(unescape(encodeURIComponent(subject)))}?=`,
    'MIME-Version: 1.0',
    'Content-Type: text/plain; charset=UTF-8',
    'Content-Transfer-Encoding: base64',
    '',
    btoa(unescape(encodeURIComponent(body))),
  ].join('\r\n');

  const encoded = btoa(rawMessage).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  const resp = await fetch(
    'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ raw: encoded }),
    }
  );

  if (!resp.ok) {
    await throwGoogleError(resp, 'Gmail send');
  }
}

/**
 * Create a Google Calendar event on the user's primary calendar.
 */
export async function createCalendarEvent(
  title: string,
  dateTime: string,
  durationMinutes: number = 60,
  description?: string,
  location?: string
): Promise<CalendarEvent> {
  const token = await getToken();

  const start = new Date(dateTime);
  const end = new Date(start.getTime() + durationMinutes * 60_000);

  const eventBody: Record<string, any> = {
    summary: title,
    start: { dateTime: start.toISOString(), timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone },
    end: { dateTime: end.toISOString(), timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone },
  };
  if (description) eventBody.description = description;
  if (location) eventBody.location = location;

  const resp = await fetch(
    'https://www.googleapis.com/calendar/v3/calendars/primary/events',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(eventBody),
    }
  );

  if (!resp.ok) {
    await throwGoogleError(resp, 'Calendar create');
  }

  const data = await resp.json();
  return {
    id: data.id,
    summary: data.summary,
    start: data.start.dateTime ?? data.start.date,
    end: data.end.dateTime ?? data.end.date,
    location: data.location,
  };
}

export interface EmailMessage {
  id: string;
  subject: string;
  from: string;
  snippet: string;
  date: string;
  isUnread: boolean;
}

/**
 * Fetch the most recent emails from the user's inbox.
 */
export async function listRecentEmails(maxResults = 5): Promise<EmailMessage[]> {
  const token = await getToken();

  const listResp = await fetch(
    `https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=${maxResults}&labelIds=INBOX`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!listResp.ok) await throwGoogleError(listResp, 'Gmail list');
  const listData = await listResp.json();
  const ids: { id: string }[] = listData.messages ?? [];

  const results = await Promise.all(
    ids.map(async ({ id }) => {
      const r = await fetch(
        `https://gmail.googleapis.com/gmail/v1/users/me/messages/${id}?format=metadata` +
          `&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!r.ok) return null;
      const d = await r.json();
      const hdrs: { name: string; value: string }[] = d.payload?.headers ?? [];
      const hdr = (n: string) => hdrs.find(h => h.name.toLowerCase() === n.toLowerCase())?.value ?? '';
      return {
        id,
        subject: hdr('Subject') || '(No subject)',
        from: hdr('From'),
        snippet: d.snippet ?? '',
        date: hdr('Date'),
        isUnread: (d.labelIds ?? []).includes('UNREAD'),
      } as EmailMessage;
    })
  );

  return results.filter(Boolean) as EmailMessage[];
}

/**
 * Fetch upcoming events from the user's primary calendar.
 */
export async function getUpcomingEvents(maxResults = 5): Promise<CalendarEvent[]> {
  const token = await getToken();

  const params = new URLSearchParams({
    timeMin: new Date().toISOString(),
    maxResults: String(maxResults),
    singleEvents: 'true',
    orderBy: 'startTime',
  });

  const resp = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/primary/events?${params}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );

  if (!resp.ok) {
    await throwGoogleError(resp, 'Calendar fetch');
  }

  const data = await resp.json();
  return (data.items ?? []).map((item: any) => ({
    id: item.id,
    summary: item.summary ?? '(No title)',
    start: item.start.dateTime ?? item.start.date,
    end: item.end.dateTime ?? item.end.date,
    location: item.location,
  }));
}
