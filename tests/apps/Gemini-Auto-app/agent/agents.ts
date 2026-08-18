import { LlmAgent, Gemini } from '@google/adk';
import { createClimateTools } from './tools/climate.js';
import { createNavigationTools } from './tools/navigation.js';
import { createMediaTools } from './tools/media.js';
import { createCommunicationTools } from './tools/communication.js';
import { createWeatherTools } from './tools/weather.js';
import { createSearchTools } from './tools/search.js';
import type { ToolContext, VehicleState } from './types.js';

// VITE_GEMINI_MODEL may be a non-existent placeholder; always fall back to a known good model
const _envModel = process.env.VITE_GEMINI_MODEL || '';
const MODEL = /^gemini-[12]\.[05]/.test(_envModel) ? _envModel : 'gemini-3.1-flash-lite';
const API_KEY = process.env.VITE_GEMINI_API_KEY || process.env.GEMINI_API_KEY || '';

const LANG_NAMES: Record<string, string> = {
  'en-US': 'English', 'es-ES': 'Spanish', 'fr-FR': 'French',
  'de-DE': 'German', 'ja-JP': 'Japanese', 'pt-BR': 'Portuguese', 'zh-CN': 'Chinese',
};

function buildVehicleContext(vs: VehicleState): string {
  return `
CURRENT VEHICLE STATE:
- Fuel: ${vs.fuel}%
- Battery: ${vs.battery}%
- Interior Temp: ${vs.temp}°C
- Tire Pressure (PSI): FL:${vs.tirePressure[0]} FR:${vs.tirePressure[1]} RL:${vs.tirePressure[2]} RR:${vs.tirePressure[3]}
- Destination: ${vs.destination ?? 'None'}
- Route: ${vs.navMetadata ? `${vs.navMetadata.distance} away, ETA ${vs.navMetadata.eta}` : 'No active route'}
- Stops: ${vs.stops.join(', ') || 'None'}
- Weather: ${vs.weather.temp}°C, ${vs.weather.condition} in ${vs.weather.location}
- Music: ${vs.music.playing ? `▶ ${vs.music.track} — ${vs.music.artist}` : 'Paused'}
`.trim();
}

/** Build the four domain sub-agents and the root orchestrator for a single request. */
export function buildAgentTree(ctx: ToolContext) {
  const gemini = new Gemini({ model: MODEL, apiKey: API_KEY });
  const langName = LANG_NAMES[ctx.language] ?? 'English';

  const navigationAgent = new LlmAgent({
    name: 'navigation_agent',
    description:
      'Handles all navigation, routing, and location-finding requests: set destination, add stops, find nearby services, optimize route.',
    model: gemini,
    instruction: `You are the navigation system of a GM vehicle.
Use your tools to set destinations, add waypoints, find nearby services, and optimize routes.
Always confirm the action with a brief, driving-safe response.
Respond in ${langName}.`,
    tools: createNavigationTools(ctx),
    disallowTransferToPeers: true,
  });

  const climateAgent = new LlmAgent({
    name: 'climate_agent',
    description:
      'Controls cabin temperature and reads vehicle diagnostics: tire pressure, fuel, battery, engine status.',
    model: gemini,
    instruction: `You are the climate and diagnostics module of a GM vehicle.
Use your tools to adjust temperature and query real-time sensor data.
Read actual sensor values — never ask the driver to check manually.
Respond in ${langName}.`,
    tools: createClimateTools(ctx),
    disallowTransferToPeers: true,
  });

  const mediaAgent = new LlmAgent({
    name: 'media_agent',
    description: 'Controls in-car audio: plays music, podcasts, and radio stations.',
    model: gemini,
    instruction: `You are the media system of a GM vehicle.
Use your tools to play music, podcasts, or radio stations as requested.
Confirm with a brief, friendly acknowledgement.
Respond in ${langName}.`,
    tools: createMediaTools(ctx),
    disallowTransferToPeers: true,
  });

  const communicationAgent = new LlmAgent({
    name: 'communication_agent',
    description:
      'Manages driver communications and calendar: read/send messages, send emails, read/list recent emails, create calendar events, fetch upcoming events.',
    model: gemini,
    instruction: `You are the communications hub of a GM vehicle.
Use your tools to manage messages, emails, and Google Calendar on the driver's behalf.
To show recent emails use the getRecentEmails tool — never say you cannot read emails.
If a Google sign-in token is unavailable, inform the driver to sign in via the sidebar.
Respond in ${langName}.`,
    tools: createCommunicationTools(ctx),
    disallowTransferToPeers: true,
  });

  const weatherAgent = new LlmAgent({
    name: 'weather_agent',
    description: 'Fetches real-time weather conditions for the current or a specified location.',
    model: gemini,
    instruction: `You are the weather service integration of a GM vehicle.
Fetch weather and report conditions concisely for safe driving awareness.
Respond in ${langName}.`,
    tools: createWeatherTools(ctx),
    disallowTransferToPeers: true,
  });

  const vehicleContext = buildVehicleContext(ctx.vehicleState);

  const rootAgent = new LlmAgent({
    name: 'car_assistant',
    description: 'Root orchestrator for the Gemini in-car AI assistant.',
    model: gemini,
    instruction: `You are the Gemini AI built into a General Motors vehicle.
You have full control over the vehicle's systems via specialized sub-agents and tools.

${vehicleContext}

CURRENT DATE/TIME: ${new Date().toLocaleString('en-US', { dateStyle: 'full', timeStyle: 'short' })}

DELEGATION RULES:
- Navigation requests → navigation_agent
- Temperature / climate / vehicle diagnostics → climate_agent
- Music / podcast / radio → media_agent
- Email / calendar / messages → communication_agent
- Weather → weather_agent
- General knowledge / current events / news / web search → use the webSearch tool directly

STRICT RULES:
- NEVER say "I'm an AI and cannot..." — you ARE the car.
- NEVER tell the driver to manually check sensors — use the diagnostic tools.
- Keep responses concise; drivers are behind the wheel.
- If multiple tasks are needed, delegate each to the right sub-agent or tool.

LANGUAGE: Always respond in ${langName}.`,
    tools: createSearchTools(),
    subAgents: [navigationAgent, climateAgent, mediaAgent, communicationAgent, weatherAgent],
    generateContentConfig: { temperature: 0 },
  });

  return rootAgent;
}
