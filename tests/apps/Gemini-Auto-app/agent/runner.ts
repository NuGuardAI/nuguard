import { InMemoryRunner, isFinalResponse } from '@google/adk';
import { buildAgentTree } from './agents.js';
import { DEFAULT_VEHICLE_STATE } from './types.js';
import type { ChatRequest, ChatResponse, ToolContext, VehicleState } from './types.js';

/**
 * Process one chat turn through the ADK agent tree.
 * Builds a fresh agent tree per request (tools capture per-request state context).
 * Uses InMemoryRunner with a persistent session so conversation history accumulates.
 */
export async function handleChat(req: ChatRequest): Promise<ChatResponse> {
  // Merge caller-supplied fields over defaults so any subset is valid
  const vehicleState: VehicleState = {
    ...DEFAULT_VEHICLE_STATE,
    ...(req.vehicleState ?? {}),
  };

  const ctx: ToolContext = {
    vehicleState,
    updates: {},
    language: req.language || 'en-US',
    googleAccessToken: req.googleAccessToken ?? undefined,
  };

  // Build a fresh agent tree that shares the mutable ctx
  const rootAgent = buildAgentTree(ctx);

  const runner = new InMemoryRunner({
    agent: rootAgent,
    appName: 'gemini-car-assistant',
  });

  // Use a fixed session per server process — history accumulates naturally
  const SESSION_ID = 'driver-session';
  const USER_ID = 'driver';

  // Create session if it doesn't exist (idempotent — no-ops if already exists)
  try {
    await runner.sessionService.createSession({
      appName: 'gemini-car-assistant',
      userId: USER_ID,
      sessionId: SESSION_ID,
    });
  } catch {
    // session already exists — this is fine
  }

  let responseText = '';
  const sources: Array<{ uri: string; title: string }> = [];

  // Stream events and collect the last final response text across all agents.
  // The ADK routes through sub-agents; we want the text from the final event
  // regardless of which agent authored it.
  let lastFinalText = '';
  const seenSources = new Set<string>();

  for await (const event of runner.runAsync({
    userId: USER_ID,
    sessionId: SESSION_ID,
    newMessage: { role: 'user', parts: [{ text: req.message }] },
    stateDelta: { vehicleState: req.vehicleState, language: req.language },
  })) {
    if (!isFinalResponse(event)) continue;

    // Accumulate text from the last final response
    const eventText = (event.content?.parts ?? [])
      .map((p: { text?: string }) => p.text ?? '')
      .join('');
    if (eventText) lastFinalText = eventText;

    // Collect Google Search grounding sources if present
    const groundingMeta = (event as unknown as {
      groundingMetadata?: { groundingChunks?: Array<{ web?: { uri: string; title: string } }> };
    }).groundingMetadata;
    if (groundingMeta?.groundingChunks) {
      for (const chunk of groundingMeta.groundingChunks) {
        if (chunk.web?.uri && !seenSources.has(chunk.web.uri)) {
          seenSources.add(chunk.web.uri);
          sources.push({ uri: chunk.web.uri, title: chunk.web.title ?? chunk.web.uri });
        }
      }
    }
  }
  responseText = lastFinalText;

  return {
    text: responseText.trim(),
    sources: sources.slice(0, 4),
    vehicleUpdates: ctx.updates,
    calendarEvents: ctx.calendarEvents,
  };
}
