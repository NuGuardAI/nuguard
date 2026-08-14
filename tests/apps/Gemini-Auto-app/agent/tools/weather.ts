import { FunctionTool } from '@google/adk';
import { z } from 'zod';
import type { ToolContext } from '../types.js';

export function createWeatherTools(ctx: ToolContext): FunctionTool[] {
  return [
    new FunctionTool({
      name: 'getWeather',
      description: 'Fetch real-time weather for a location.',
      parameters: z.object({
        location: z.string().optional()
          .describe('City or location name. Defaults to current location.'),
      }),
      execute: async ({ location }) => {
        const loc = location || ctx.vehicleState.weather.location || 'San Francisco';
        const apiKey = process.env.OPENWEATHER_API_KEY;

        if (apiKey) {
          try {
            const resp = await fetch(
              `https://api.openweathermap.org/data/2.5/weather?q=${encodeURIComponent(loc)}&appid=${apiKey}&units=metric`
            );
            if (resp.ok) {
              const data = await resp.json();
              if (data.main) {
                const weather = {
                  temp: Math.round(data.main.temp),
                  condition: data.weather[0].main,
                  location: data.name,
                };
                ctx.updates.weather = weather;
                return `${weather.location}: ${weather.temp}°C, ${weather.condition}`;
              }
            }
          } catch {
            // fall through to simulation
          }
        }

        const simulated = { temp: 18, condition: 'Partly Cloudy', location: loc };
        ctx.updates.weather = simulated;
        return `${loc}: ${simulated.temp}°C, ${simulated.condition} (simulated)`;
      },
    }),
  ];
}
