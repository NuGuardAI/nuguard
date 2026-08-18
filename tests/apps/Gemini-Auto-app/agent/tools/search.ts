import { FunctionTool } from '@google/adk';
import { GoogleGenAI } from '@google/genai';
import { z } from 'zod';

function getGenAI() {
  const apiKey = process.env.VITE_GEMINI_API_KEY || process.env.GEMINI_API_KEY;
  if (!apiKey) throw new Error('GEMINI_API_KEY not set');
  return new GoogleGenAI({ apiKey });
}

const MODEL = (() => {
  const m = process.env.VITE_GEMINI_MODEL || '';
  return /^gemini-[12]\.[05]/.test(m) ? m : 'gemini-3.1-flash-lite';
})();

export function createSearchTools(): FunctionTool[] {
  return [
    new FunctionTool({
      name: 'webSearch',
      description:
        'Search the web for current events, news, sports scores, prices, general knowledge, and any real-time information. Returns a concise summary.',
      parameters: z.object({
        query: z.string().describe('The search query to look up on the web.'),
      }),
      execute: async ({ query }) => {
        try {
          const ai = getGenAI();
          const response = await ai.models.generateContent({
            model: MODEL,
            contents: [{ role: 'user', parts: [{ text: query }] }],
            config: {
              tools: [{ googleSearch: {} }],
              systemInstruction:
                'You are a concise web search assistant. Summarize the answer in 2-3 sentences. Driver-safe: no markdown, no bullet points.',
              temperature: 0,
            },
          });
          return response.text?.trim() ?? 'No results found for that query.';
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          return `Search unavailable: ${msg}`;
        }
      },
    }),
  ];
}
