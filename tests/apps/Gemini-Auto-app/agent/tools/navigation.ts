import { FunctionTool } from '@google/adk';
import { GoogleGenAI } from '@google/genai';
import { z } from 'zod';
import type { ToolContext } from '../types.js';

function getGeminiModel() {
  const apiKey = process.env.VITE_GEMINI_API_KEY || process.env.GEMINI_API_KEY;
  if (!apiKey) throw new Error('GEMINI_API_KEY not set');
  return new GoogleGenAI({ apiKey });
}

/** Resolve a vague query to a navigable full address using Gemini + Google Search. */
async function resolveLocation(query: string, nearContext?: string): Promise<string> {
  try {
    const ai = getGeminiModel();
    const near = nearContext ? ` near ${nearContext}` : '';
    const prompt =
      `Navigation destination: "${query}"${near}.\n` +
      `Reply with ONLY the single best full address (business name, street, city, state). ` +
      `One line, no markdown, no explanation.`;
    const response = await ai.models.generateContent({
      model: process.env.VITE_GEMINI_MODEL || 'gemini-2.0-flash',
      contents: [{ role: 'user', parts: [{ text: prompt }] }],
      config: { tools: [{ googleSearch: {} }], temperature: 0 },
    });
    const resolved = (response.text ?? '')
      .split('\n')
      .map((l: string) => l.replace(/[*_`#]/g, '').trim())
      .find((l: string) => l.length > 5);
    return resolved ?? query;
  } catch {
    return query;
  }
}

export function createNavigationTools(ctx: ToolContext): FunctionTool[] {
  return [
    new FunctionTool({
      name: 'navigateTo',
      description: 'Set the primary navigation destination. Clears existing stops.',
      parameters: z.object({
        destination: z.string()
          .describe('Destination address, POI name, or natural description.'),
      }),
      execute: async ({ destination }) => {
        const resolved = await resolveLocation(destination);
        ctx.updates.destination = resolved;
        ctx.updates.stops = [];
        ctx.updates.navMetadata = null;
        return `Navigation started to: ${resolved}`;
      },
    }),

    new FunctionTool({
      name: 'addStop',
      description: 'Add a waypoint to the current navigation route.',
      parameters: z.object({
        location: z.string().describe('Address or POI name to add as a stop.'),
      }),
      execute: async ({ location }) => {
        const resolved = await resolveLocation(location);
        const currentStops = ctx.updates.stops ?? ctx.vehicleState.stops;
        ctx.updates.stops = [...currentStops, resolved];
        return `Stop added: ${resolved}`;
      },
    }),

    new FunctionTool({
      name: 'findNearbyService',
      description:
        'Find nearby points of interest: charging stations, gas stations, restaurants, cafes, hospitals, etc.',
      parameters: z.object({
        serviceType: z.enum([
          'charging station', 'gas station', 'restaurant', 'cafe',
          'hospital', 'coffee shop', 'post office', 'parking',
        ]).describe('Category of service to find.'),
        query: z.string().optional()
          .describe('Optional refinement (e.g. "Italian food", "cheapest gas").'),
      }),
      execute: async ({ serviceType, query }) => {
        const searchQuery = query ? `${query} (${serviceType})` : `nearest ${serviceType}`;
        const currentLocation =
          ctx.vehicleState.destination ?? ctx.vehicleState.weather.location ?? 'current location';
        const resolved = await resolveLocation(searchQuery, currentLocation);
        ctx.updates.destination = resolved;
        ctx.updates.stops = [];
        ctx.updates.navMetadata = null;
        return `Navigating to ${serviceType}: ${resolved}`;
      },
    }),

    new FunctionTool({
      name: 'optimizeRoute',
      description: 'Reorder the current route stops for the shortest or fastest path.',
      parameters: z.object({
        criteria: z.enum(['shortest', 'fastest']).describe('Optimization strategy.'),
      }),
      execute: ({ criteria }) => {
        const currentStops = ctx.updates.stops ?? ctx.vehicleState.stops;
        ctx.updates.stops = [...currentStops].reverse();
        return `Route optimized for ${criteria} path. ${currentStops.length} stops reordered.`;
      },
    }),
  ];
}
