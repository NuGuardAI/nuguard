import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig, loadEnv} from 'vite';

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react(), tailwindcss()],
    define: {
      'process.env.GEMINI_API_KEY': JSON.stringify(env.VITE_GEMINI_API_KEY || env.GEMINI_API_KEY || process.env.VITE_GEMINI_API_KEY || process.env.GEMINI_API_KEY),
      'import.meta.env.VITE_GEMINI_API_KEY': JSON.stringify(env.VITE_GEMINI_API_KEY || env.GEMINI_API_KEY || process.env.VITE_GEMINI_API_KEY || process.env.GEMINI_API_KEY),
      'import.meta.env.VITE_GEMINI_MODEL': JSON.stringify(env.VITE_GEMINI_MODEL || process.env.VITE_GEMINI_MODEL || 'gemini-3.1-flash-lite'),
      'process.env.GOOGLE_MAPS_PLATFORM_KEY': JSON.stringify(env.GOOGLE_MAPS_PLATFORM_KEY || env.VITE_GOOGLE_MAPS_API_KEY || process.env.GOOGLE_MAPS_PLATFORM_KEY || process.env.VITE_GOOGLE_MAPS_API_KEY || ''),
      'import.meta.env.VITE_GOOGLE_MAPS_API_KEY': JSON.stringify(env.VITE_GOOGLE_MAPS_API_KEY || env.GOOGLE_MAPS_PLATFORM_KEY || process.env.VITE_GOOGLE_MAPS_API_KEY || process.env.GOOGLE_MAPS_PLATFORM_KEY || ''),
      'import.meta.env.VITE_GOOGLE_OAUTH_CLIENT_ID': JSON.stringify(env.VITE_GOOGLE_OAUTH_CLIENT_ID || process.env.VITE_GOOGLE_OAUTH_CLIENT_ID || ''),
      'import.meta.env.VITE_GOOGLE_PROJECT_ID': JSON.stringify(env.VITE_GOOGLE_PROJECT_ID || process.env.VITE_GOOGLE_PROJECT_ID || ''),
      'process.env.OPENWEATHER_API_KEY': JSON.stringify(env.OPENWEATHER_API_KEY || process.env.OPENWEATHER_API_KEY || ''),
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      port: 4001,
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify — file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      proxy: {
        // Forward API calls to the ADK agent server running on port 3001 in dev
        '/api': 'http://localhost:3001',
      },
    },
  };
});
