import { FunctionTool } from '@google/adk';
import { z } from 'zod';
import type { CalendarEvent, ToolContext } from '../types.js';

async function getToken(ctx: ToolContext): Promise<string> {
  if (!ctx.googleAccessToken) {
    throw new Error(
      'Google sign-in required for email and calendar. Please sign in via the sidebar.'
    );
  }
  return ctx.googleAccessToken;
}

async function throwGoogleError(resp: Response, context: string): Promise<never> {
  const body = await resp.json().catch(() => ({}));
  const msg: string = body?.error?.message ?? `${context} failed (${resp.status})`;
  throw new Error(msg);
}

async function fetchUpcomingEvents(token: string, maxResults: number): Promise<CalendarEvent[]> {
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
    const body = await resp.json().catch(() => ({}));
    throw new Error(body?.error?.message ?? `Calendar fetch failed (${resp.status})`);
  }
  const data = await resp.json();
  return (data.items ?? []).map((item: Record<string, unknown>) => ({
    id: item.id as string,
    summary: (item.summary as string) ?? '(No title)',
    start: (item.start as Record<string, string>).dateTime ?? (item.start as Record<string, string>).date,
    end: (item.end as Record<string, string>).dateTime ?? (item.end as Record<string, string>).date,
    location: item.location as string | undefined,
  }));
}

export function createCommunicationTools(ctx: ToolContext): FunctionTool[] {
  return [
    new FunctionTool({
      name: 'manageMessages',
      description: 'Read incoming text messages or compose and send a new message.',
      parameters: z.object({
        action: z.enum(['read', 'send']).describe('Whether to read messages or send one.'),
        recipient: z.string().optional().describe('Contact name or number (for sending).'),
        content: z.string().optional().describe('Message text (for sending).'),
      }),
      execute: ({ action, recipient, content }) => {
        if (action === 'read') return 'No unread messages.';
        return `Message to ${recipient}: "${content}" — sent (simulated).`;
      },
    }),

    new FunctionTool({
      name: 'getRecentEmails',
      description: "Fetch the driver's most recent inbox emails from Gmail.",
      parameters: z.object({
        maxResults: z.number().optional().describe('Number of emails to return. Default 5.'),
      }),
      execute: async ({ maxResults = 5 }) => {
        const token = await getToken(ctx);

        const listResp = await fetch(
          `https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=${maxResults}&labelIds=INBOX`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (!listResp.ok) await throwGoogleError(listResp, 'Gmail list');
        const listData = await listResp.json();
        const ids: { id: string }[] = listData.messages ?? [];
        if (ids.length === 0) return 'Your inbox is empty.';

        const emails = await Promise.all(
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
            const fromRaw = hdr('From');
            const fromName = fromRaw.match(/^"?([^"<]+)"?\s*</)?.[1]?.trim() ?? fromRaw;
            const isUnread = (d.labelIds ?? []).includes('UNREAD');
            return `${isUnread ? '● ' : '  '}From: ${fromName}\n  Subject: ${hdr('Subject') || '(No subject)'}\n  Preview: ${(d.snippet ?? '').slice(0, 80)}`;
          })
        );

        const lines = emails.filter(Boolean) as string[];
        return `Here are your ${lines.length} most recent emails:\n\n${lines.join('\n\n')}`;
      },
    }),

    new FunctionTool({
      name: 'sendEmail',
      description: "Send an email via the driver's Gmail account.",
      parameters: z.object({
        to: z.string().describe('Recipient email address.'),
        subject: z.string().describe('Email subject.'),
        body: z.string().describe('Email body text.'),
      }),
      execute: async ({ to, subject, body }) => {
        const token = await getToken(ctx);
        const rawMessage = [
          `To: ${to}`,
          `Subject: =?UTF-8?B?${Buffer.from(subject).toString('base64')}?=`,
          'MIME-Version: 1.0',
          'Content-Type: text/plain; charset=UTF-8',
          'Content-Transfer-Encoding: base64',
          '',
          Buffer.from(body).toString('base64'),
        ].join('\r\n');
        const encoded = Buffer.from(rawMessage)
          .toString('base64')
          .replace(/\+/g, '-')
          .replace(/\//g, '_')
          .replace(/=+$/, '');

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
        if (!resp.ok) await throwGoogleError(resp, 'Gmail send');
        return `Email sent to ${to}.`;
      },
    }),

    new FunctionTool({
      name: 'createCalendarEvent',
      description: "Create a new event on the driver's Google Calendar.",
      parameters: z.object({
        title: z.string().describe('Event title.'),
        dateTime: z.string().describe('Start date/time in ISO 8601 format.'),
        durationMinutes: z.number().optional().describe('Duration in minutes. Default 60.'),
        description: z.string().optional().describe('Optional event notes.'),
        location: z.string().optional().describe('Optional physical location.'),
      }),
      execute: async ({ title, dateTime, durationMinutes = 60, description, location }) => {
        const token = await getToken(ctx);
        const start = new Date(dateTime);
        const end = new Date(start.getTime() + durationMinutes * 60_000);
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        const eventBody: Record<string, unknown> = {
          summary: title,
          start: { dateTime: start.toISOString(), timeZone: tz },
          end: { dateTime: end.toISOString(), timeZone: tz },
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
        if (!resp.ok) await throwGoogleError(resp, 'Calendar create');

        try {
          ctx.calendarEvents = await fetchUpcomingEvents(token, 5);
        } catch {
          // non-fatal — calendar refresh failure doesn't block the create
        }
        return `Calendar event "${title}" created on ${start.toLocaleDateString()}.`;
      },
    }),

    new FunctionTool({
      name: 'getUpcomingEvents',
      description: "Fetch the driver's upcoming Google Calendar events.",
      parameters: z.object({
        maxResults: z.number().optional().describe('Number of events to return. Default 5.'),
      }),
      execute: async ({ maxResults = 5 }) => {
        const token = await getToken(ctx);
        const events = await fetchUpcomingEvents(token, maxResults);
        ctx.calendarEvents = events;
        if (events.length === 0) return 'No upcoming calendar events found.';
        return events
          .map(e => {
            const dt = new Date(e.start);
            return `• ${e.summary} — ${dt.toLocaleString('en-US', { dateStyle: 'short', timeStyle: 'short' })}`;
          })
          .join('\n');
      },
    }),
  ];
}
