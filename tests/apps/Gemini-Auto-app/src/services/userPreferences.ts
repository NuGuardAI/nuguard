/**
 * User Preferences Service
 * Persists language, voice, and profile settings to localStorage.
 */

export interface UserPreferences {
  language: string;       // BCP-47 code, e.g. 'en-US'
  ttsEnabled: boolean;    // speak AI responses aloud
  userName?: string;      // optional display name
  // Vehicle state preferences (persist across sessions)
  temp?: number;          // last cabin temperature in °C
  music?: {               // last played track
    track: string;
    artist: string;
    playing: boolean;
    provider: 'simulated' | 'spotify';
  };
}

export const SUPPORTED_LANGUAGES = [
  { code: 'en-US', label: 'English',    flag: '🇺🇸' },
  { code: 'es-ES', label: 'Español',    flag: '🇪🇸' },
  { code: 'fr-FR', label: 'Français',   flag: '🇫🇷' },
  { code: 'de-DE', label: 'Deutsch',    flag: '🇩🇪' },
  { code: 'ja-JP', label: '日本語',      flag: '🇯🇵' },
  { code: 'pt-BR', label: 'Português',  flag: '🇧🇷' },
  { code: 'zh-CN', label: '中文',        flag: '🇨🇳' },
] as const;

export const LANGUAGE_NAMES: Record<string, string> = {
  'en-US': 'English',
  'es-ES': 'Spanish',
  'fr-FR': 'French',
  'de-DE': 'German',
  'ja-JP': 'Japanese',
  'pt-BR': 'Portuguese',
  'zh-CN': 'Chinese',
};

const STORAGE_KEY = 'gemini-car-prefs';

const DEFAULTS: UserPreferences = {
  language: 'en-US',
  ttsEnabled: true,
  temp: 21,
  music: { track: 'Starlight', artist: 'Muse', playing: true, provider: 'simulated' },
};

export function getPreferences(): UserPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    // Ignore parse errors
  }
  return { ...DEFAULTS };
}

export function savePreferences(patch: Partial<UserPreferences>): UserPreferences {
  const current = getPreferences();
  const updated = { ...current, ...patch };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
  return updated;
}
