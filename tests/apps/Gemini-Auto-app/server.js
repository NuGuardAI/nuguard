import express from 'express';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3000;

// Content-Security-Policy headers required for Google Maps JS API and Gemini API
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://maps.googleapis.com https://maps.gstatic.com https://accounts.google.com https://*.googleapis.com",
  "connect-src 'self' https://*.googleapis.com https://*.gstatic.com https://accounts.google.com wss:",
  "img-src 'self' data: blob: https://*.googleapis.com https://*.gstatic.com https://streetviewpixels-pa.googleapis.com https://lh3.googleusercontent.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "worker-src blob: 'self' https://maps.googleapis.com https://*.googleapis.com",
  "frame-src https://accounts.google.com",
  "object-src 'none'",
  "base-uri 'self'",
].join('; ');

app.use((_req, res, next) => {
  res.setHeader('Content-Security-Policy', CSP);
  next();
});

app.use(express.static(join(__dirname, 'dist')));
app.get('*', (_req, res) => {
  res.sendFile(join(__dirname, 'dist', 'index.html'));
});

app.listen(PORT, () => console.log(`Listening on port ${PORT}`));
