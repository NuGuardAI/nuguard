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

/** Mutable context shared across all tool execute functions in one request. */
export interface ToolContext {
  vehicleState: VehicleState;
  updates: Partial<VehicleState>;
  calendarEvents?: CalendarEvent[];
  googleAccessToken?: string;
  language: string;
}

/** Default vehicle state used when the caller omits vehicleState. */
export const DEFAULT_VEHICLE_STATE: VehicleState = {
  fuel: 100,
  battery: 100,
  temp: 22,
  tirePressure: [32, 32, 32, 32],
  music: { playing: false, track: '', artist: '', provider: '' },
  destination: null,
  stops: [],
  navMetadata: null,
  weather: { temp: 22, condition: 'Unknown', location: 'Unknown' },
};

export interface ChatRequest {
  message: string;
  /** Full vehicle telemetry. All fields optional — missing ones are defaulted. */
  vehicleState?: Partial<VehicleState> | null;
  /** BCP-47 language tag. Defaults to 'en-US'. */
  language?: string | null;
  googleAccessToken?: string | null;
}

export interface ChatResponse {
  text: string;
  sources: Array<{ uri: string; title: string }>;
  vehicleUpdates: Partial<VehicleState>;
  calendarEvents?: CalendarEvent[];
}
