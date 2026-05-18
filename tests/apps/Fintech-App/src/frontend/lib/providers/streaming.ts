/**
 * CipherBank — Streaming Completion Provider
 * -------------------------------------------
 * SBOM COMPLEXITY TEST #6 (sub-module layer 3)
 *
 * A third level in the re-export chain.  Any scan of the barrel (ai-client.ts)
 * only sees ``export { streamCompletion } from './providers/streaming'`` —
 * a local relative path, not a package name.
 *
 * This sub-module also uses dynamic import() for the AI SDK, compounding
 * the detection gap.
 */

const STREAM_TIMEOUT_MS = 30_000;

export class StreamTimeoutError extends Error {
  constructor(timeoutMs: number = STREAM_TIMEOUT_MS) {
    super(
      `The AI service did not respond within ${timeoutMs / 1000} seconds. ` +
      'Please try again in a moment or contact support if the issue persists.',
    );
    this.name = 'StreamTimeoutError';
  }
}

export async function streamCompletion(
  messages: Array<{ role: string; content: string }>,
  onChunk: (chunk: string) => void,
  model?: string,
  timeoutMs: number = STREAM_TIMEOUT_MS,
): Promise<void> {
  // Dynamic import — not a static import statement
  const { OpenAI } = await import('openai');

  const client = new OpenAI({
    apiKey: (globalThis as Record<string, unknown>)['__OPENAI_KEY__'] as string
      ?? import.meta.env?.VITE_OPENAI_API_KEY
      ?? '',
    timeout: timeoutMs,
  });

  let stream: AsyncIterable<any>;
  try {
    stream = await client.chat.completions.create({
      model: model ?? 'gpt-4o',
      messages,
      stream: true,
    }) as any;
  } catch (err: unknown) {
    const errName = err instanceof Error ? err.constructor.name : '';
    if (errName === 'APITimeoutError' || (err instanceof Error && err.message.includes('timeout'))) {
      throw new StreamTimeoutError(timeoutMs);
    }
    throw new Error(
      'Failed to connect to the AI service. Please try again.',
    );
  }

  // Enforce a wall-clock timeout on the full stream
  const deadline = Date.now() + timeoutMs;
  try {
    for await (const chunk of stream) {
      if (Date.now() > deadline) {
        throw new StreamTimeoutError(timeoutMs);
      }
      const delta = chunk.choices[0]?.delta?.content;
      if (delta) onChunk(delta);
    }
  } catch (err: unknown) {
    if (err instanceof StreamTimeoutError) throw err;
    const errName = err instanceof Error ? err.constructor.name : '';
    if (errName === 'APITimeoutError') {
      throw new StreamTimeoutError(timeoutMs);
    }
    throw new Error(
      'The AI response stream was interrupted. Please try again.',
    );
  }
}
