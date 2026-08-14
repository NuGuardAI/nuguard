import 'dotenv/config';
import express from 'express';
import jwt from 'jsonwebtoken';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { handleChat } from './agent/runner.js';
import type { ChatRequest } from './agent/types.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = Number(process.env.PORT || 3000);

const JWT_SECRET = process.env.JWT_SECRET;
if (!JWT_SECRET) {
  console.error('[FATAL] JWT_SECRET environment variable is not set. Exiting.');
  process.exit(1);
}

// Server-side OAuth 2.0 client credentials (Authorization Code / ID-token flow)
const OAUTH_CLIENT_ID = process.env.OAUTH_CLIENT_ID || '';
const OAUTH_CLIENT_SECRET = process.env.OAUTH_CLIENT_SECRET || '';
const JWT_EXPIRES_IN_SECONDS = 3600; // 1 hour

// In-memory denylist keyed by jti (JWT ID).
// Entries are pruned once their natural expiry has passed.
const revokedTokens = new Map<string, number>(); // jti -> exp (unix seconds)

function revokeToken(jti: string, exp: number): void {
  revokedTokens.set(jti, exp);
  // Prune expired entries to prevent unbounded growth
  const now = Math.floor(Date.now() / 1000);
  for (const [id, expiry] of revokedTokens) {
    if (expiry < now) revokedTokens.delete(id);
  }
}

function isRevoked(jti: string): boolean {
  return revokedTokens.has(jti);
}

app.use(express.json({ limit: '1mb' }));

// Content-Security-Policy headers required for Google Maps JS API and Gemini API
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://maps.googleapis.com https://maps.gstatic.com https://accounts.google.com",
  "connect-src 'self' https://*.googleapis.com https://*.gstatic.com https://accounts.google.com wss:",
  "img-src 'self' data: blob: https://*.googleapis.com https://*.gstatic.com https://streetviewpixels-pa.googleapis.com https://lh3.googleusercontent.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "worker-src blob: 'self'",
  "frame-src https://accounts.google.com",
  "object-src 'none'",
  "base-uri 'self'",
].join('; ');

app.use((_req, res, next) => {
  res.setHeader('Content-Security-Policy', CSP);
  next();
});

// ── Auth middleware ──────────────────────────────────────────────────────────
/**
 * Extracts a session JWT from `Authorization: Bearer <token>`.
 * If valid, injects the stored googleAccessToken into req.body so downstream
 * handlers work without the caller having to supply it again.
 * Falls through without error when no header is present (backward compat).
 */
function injectAuthFromJWT(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction,
): void {
  const authHeader = req.headers['authorization'];
  if (!authHeader?.startsWith('Bearer ')) {
    return next(); // no token — allow direct googleAccessToken in body
  }
  const token = authHeader.slice(7);
  try {
    const payload = jwt.verify(token, JWT_SECRET as string) as jwt.JwtPayload;
    if (payload.jti && isRevoked(payload.jti)) {
      res.status(401).json({ error: 'Token has been revoked — please log in again' });
      return;
    }
    // Populate googleAccessToken from JWT if caller didn't supply it explicitly
    if (!req.body.googleAccessToken && payload.googleAccessToken) {
      req.body.googleAccessToken = payload.googleAccessToken;
    }
    (req as any).jwtPayload = payload;
    next();
  } catch {
    res.status(401).json({ error: 'Invalid or expired session token' });
  }
}

// ── POST /api/auth/login ─────────────────────────────────────────────────────
/**
 * Exchange a Google OAuth 2.0 access token for a signed session JWT.
 *
 * Request body (one of):
 *   { "googleAccessToken": "<Google OAuth 2.0 access_token>" }
 *   { "googleIdToken":    "<Google Sign-In id_token>" }
 *
 * Response:
 *   { "sessionToken": "<jwt>", "user": { name, email, picture }, "expiresIn": 3600 }
 *
 * 3rd-party automation workflow:
 *   1. Obtain a Google access token or ID token via your own OAuth flow.
 *   2. POST that token here to receive a sessionToken.
 *   3. Include `Authorization: Bearer <sessionToken>` on every subsequent
 *      request to /api/agent/chat.
 */
app.post('/api/auth/login', async (req, res) => {
  try {
    const { googleAccessToken, googleIdToken } = req.body ?? {};

    if (googleIdToken && typeof googleIdToken === 'string') {
      // ── ID-token path (Google Sign-In / One Tap) ──────────────────────────
      const tokenInfoRes = await fetch(
        `https://oauth2.googleapis.com/tokeninfo?id_token=${encodeURIComponent(googleIdToken)}`,
      );
      if (!tokenInfoRes.ok) {
        res.status(401).json({ error: 'Google ID token validation failed — token may be invalid or expired' });
        return;
      }
      const tokenInfo = await tokenInfoRes.json() as {
        aud?: string; sub?: string; email?: string; name?: string; picture?: string; error?: string;
      };
      if (tokenInfo.error) {
        res.status(401).json({ error: `Google rejected ID token: ${tokenInfo.error}` });
        return;
      }
      // Verify the token was issued for this application
      if (OAUTH_CLIENT_ID && tokenInfo.aud !== OAUTH_CLIENT_ID) {
        res.status(401).json({ error: 'ID token audience does not match this application' });
        return;
      }
      const user = {
        name: tokenInfo.name ?? tokenInfo.email ?? 'Unknown',
        email: tokenInfo.email ?? '',
        picture: tokenInfo.picture ?? '',
      };
      const jti = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const sessionToken = jwt.sign(
        { sub: user.email, name: user.name, picture: user.picture, jti },
        JWT_SECRET as string,
        { expiresIn: JWT_EXPIRES_IN_SECONDS },
      );
      res.json({ sessionToken, user, expiresIn: JWT_EXPIRES_IN_SECONDS });
      return;
    }

    if (!googleAccessToken || typeof googleAccessToken !== 'string') {
      res.status(400).json({ error: '"googleAccessToken" or "googleIdToken" (string) is required' });
      return;
    }

    // ── Access-token path (existing OAuth 2.0 implicit / token flow) ─────────
    const tokenInfoRes = await fetch(
      `https://oauth2.googleapis.com/tokeninfo?access_token=${encodeURIComponent(googleAccessToken)}`,
    );
    if (!tokenInfoRes.ok) {
      res.status(401).json({ error: 'Google token validation failed — token may be invalid or expired' });
      return;
    }
    const tokenInfo = await tokenInfoRes.json() as { azp?: string; aud?: string; error?: string };
    if (tokenInfo.error) {
      res.status(401).json({ error: `Google rejected access token: ${tokenInfo.error}` });
      return;
    }
    // Verify the token was issued for this application (azp = authorized party)
    if (OAUTH_CLIENT_ID && tokenInfo.azp !== OAUTH_CLIENT_ID && tokenInfo.aud !== OAUTH_CLIENT_ID) {
      res.status(401).json({ error: 'Access token was not issued for this application' });
      return;
    }

    // Fetch user profile
    const profileRes = await fetch('https://www.googleapis.com/oauth2/v2/userinfo', {
      headers: { Authorization: `Bearer ${googleAccessToken}` },
    });
    if (!profileRes.ok) {
      res.status(401).json({ error: 'Failed to fetch Google user profile' });
      return;
    }
    const profile = await profileRes.json() as { name: string; email: string; picture: string };
    const user = { name: profile.name, email: profile.email, picture: profile.picture };

    // Sign a session JWT that embeds the google token so callers don't need to
    // re-supply it on every chat request.
    const jti = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const sessionToken = jwt.sign(
      { sub: user.email, name: user.name, picture: user.picture, googleAccessToken, jti },
      JWT_SECRET as string,
      { expiresIn: JWT_EXPIRES_IN_SECONDS },
    );

    res.json({ sessionToken, user, expiresIn: JWT_EXPIRES_IN_SECONDS });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error('[Auth] Login error:', msg);
    res.status(500).json({ error: msg });
  }
});

// ── POST /api/auth/logout ───────────────────────────────────────────────────
/**
 * Invalidate a session token immediately.
 * Adds the token's jti to the server-side denylist and optionally revokes
 * the embedded Google OAuth access token.
 *
 * Request headers:
 *   Authorization: Bearer <sessionToken>
 *
 * Response:
 *   { "message": "Logged out successfully" }
 */
app.post('/api/auth/logout', async (req, res) => {
  const authHeader = req.headers['authorization'];
  if (!authHeader?.startsWith('Bearer ')) {
    res.status(400).json({ error: 'Authorization header with Bearer token required' });
    return;
  }
  try {
    const payload = jwt.verify(authHeader.slice(7), JWT_SECRET as string) as jwt.JwtPayload;
    if (payload.jti && payload.exp) {
      revokeToken(payload.jti, payload.exp);
    }
    // Best-effort: revoke the Google OAuth token so it cannot be reused
    if (payload.googleAccessToken) {
      fetch(
        `https://oauth2.googleapis.com/revoke?token=${encodeURIComponent(payload.googleAccessToken)}`,
        { method: 'POST' },
      ).catch(() => { /* ignore — token may already be expired */ });
    }
    res.json({ message: 'Logged out successfully' });
  } catch {
    // Even if the token is already invalid/expired, treat as success
    res.json({ message: 'Logged out successfully' });
  }
});

// ── POST /api/auth/verify ────────────────────────────────────────────────────
/**
 * Verify a session token and return the decoded user payload.
 * Useful for 3rd-party tools to check token validity before a long task.
 */
app.post('/api/auth/verify', (req, res) => {
  const authHeader = req.headers['authorization'];
  if (!authHeader?.startsWith('Bearer ')) {
    res.status(401).json({ error: 'Authorization header with Bearer token required' });
    return;
  }
  try {
    const payload = jwt.verify(authHeader.slice(7), JWT_SECRET as string) as jwt.JwtPayload;
    if (payload.jti && isRevoked(payload.jti)) {
      res.status(401).json({ valid: false, error: 'Token has been revoked' });
      return;
    }
    res.json({
      valid: true,
      user: { name: payload.name, email: payload.sub, picture: payload.picture },
      expiresAt: payload.exp ? new Date(payload.exp * 1000).toISOString() : null,
    });
  } catch {
    res.status(401).json({ valid: false, error: 'Invalid or expired session token' });
  }
});

// ── ADK Agent Chat Endpoint ──────────────────────────────────────────────────
app.post('/api/agent/chat', injectAuthFromJWT, async (req, res) => {
  try {
    const body = req.body ?? {};
    if (!body.message || typeof body.message !== 'string') {
      res.status(400).json({ error: '"message" (string) is required' });
      return;
    }
    // All other fields are optional — vehicleState and language are defaulted in runner
    const chatReq: ChatRequest = {
      message: body.message,
      vehicleState: body.vehicleState ?? null,
      language: body.language ?? null,
      googleAccessToken: body.googleAccessToken ?? null,
    };
    const result = await handleChat(chatReq);
    res.json(result);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error('[ADK] Chat error:', msg);
    res.status(500).json({ error: msg });
  }
});

// ── Static file serving ──────────────────────────────────────────────────────
app.use(express.static(join(__dirname, 'dist')));
app.get('*', (_req, res) => {
  res.sendFile(join(__dirname, 'dist', 'index.html'));
});

app.listen(PORT, () => console.log(`Listening on port ${PORT}`));
