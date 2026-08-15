import { FunctionTool } from '@google/adk';
import { z } from 'zod';
import type { ToolContext } from '../types.js';

export function createMediaTools(ctx: ToolContext): FunctionTool[] {
  return [
    new FunctionTool({
      name: 'playMedia',
      description: 'Play music, podcasts, or radio stations via the in-car audio system.',
      parameters: z.object({
        query: z.string()
          .describe('Track name, artist, podcast title, or station frequency.'),
        mediaType: z.enum(['music', 'podcast', 'radio']).optional()
          .describe('Type of media to play.'),
      }),
      execute: ({ query, mediaType }) => {
        const provider = process.env.VITE_SPOTIFY_CLIENT_ID ? 'spotify' : 'simulated';
        ctx.updates.music = {
          playing: true,
          track: query,
          artist: provider === 'spotify' ? 'Spotify' : 'Artist',
          provider,
        };
        return `Now playing "${query}" via ${provider}${mediaType ? ` (${mediaType})` : ''}.`;
      },
    }),
  ];
}
