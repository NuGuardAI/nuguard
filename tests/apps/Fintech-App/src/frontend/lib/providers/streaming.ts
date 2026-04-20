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

export async function streamCompletion(
  messages: Array<{ role: string; content: string }>,
  onChunk: (chunk: string) => void,
  model?: string,
): Promise<void> {
  // Dynamic import — not a static import statement
  const { OpenAI } = await import('openai');

  const client = new OpenAI({
    apiKey: (globalThis as Record<string, unknown>)['__OPENAI_KEY__'] as string
      ?? import.meta.env?.VITE_OPENAI_API_KEY
      ?? '',
  });

  const stream = await client.chat.completions.create({
    model: model ?? 'gpt-4o',
    messages,
    stream: true,
  });

  for await (const chunk of stream) {
    const delta = chunk.choices[0]?.delta?.content;
    if (delta) onChunk(delta);
  }
}
