/** Mirrors agent/types.ts — kept in sync manually. */

export interface VehicleState {
  fuel: number;
  temp: number;
  tirePressure: [number, number, number, number];
  music: { playing: boolean; track: string; artist: string; provider: string };
  battery: number;
  destination: string | null;
  stops: string[];
  navMetadata: { distance: string; eta: string } | null;
  weather: { temp: number; condition: string; location: string };
}

export interface CalendarEvent {
  id: string;
  summary: string;
  start: string;
  end: string;
  location?: string;
}

export interface EmailMessage {
  id: string;
  subject: string;
  from: string;
  snippet: string;
  date: string;
  isUnread: boolean;
}

export interface ChatResponse {
  text: string;
  sources: Array<{ uri: string; title: string }>;
  vehicleUpdates: Partial<VehicleState>;
  calendarEvents?: CalendarEvent[];
}
