/**
 * Agent client — calls the server-side ADK agent via REST.
 * The ADK runner (Node.js) handles all Gemini API calls and tool execution.
 */

import type { VehicleState, ChatResponse } from './agent-types';

export type { ChatResponse };

/** Send a message to the ADK agent. Returns the text reply plus any state updates. */
export async function chatWithAgent(
  message: string,
  vehicleState: VehicleState,
  language: string,
  googleAccessToken?: string
): Promise<ChatResponse> {
  const resp = await fetch('/api/agent/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, vehicleState, language, googleAccessToken }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const raw: string = body?.error ?? `HTTP ${resp.status}`;
    if (raw.includes('Failed to fetch') || raw.includes('NetworkError') || raw.includes('net::ERR')) {
      throw new Error('Failed to fetch. Check your network connection and try again.');
    }
    throw new Error(raw);
  }
  return resp.json() as Promise<ChatResponse>;
}

/** Kept for backwards-compatibility (MapSimulator → resolveLocation). */
export async function resolveLocation(query: string, nearContext?: string): Promise<string> {
  try {
    const resp = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message:
          `Navigation destination: "${query}"${nearContext ? ` near ${nearContext}` : ''}. ` +
          `Reply with ONLY the single best full address. One line, no markdown.`,
        vehicleState: null,
        language: 'en-US',
      }),
    });
    if (!resp.ok) return query;
    const data: ChatResponse = await resp.json();
    return data.text?.split('\n').find((l: string) => l.trim().length > 5) ?? query;
  } catch {
    return query;
  }
}
