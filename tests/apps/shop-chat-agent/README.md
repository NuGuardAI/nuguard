# shop-chat-agent — NuGuard Test Target

[shop-chat-agent](https://github.com/Shopify/shop-chat-agent) is Shopify's
reference embedded app: a Claude-powered storefront shopping assistant
(React Router backend, Prisma/SQLite session storage) that speaks the Model
Context Protocol (MCP) against Shopify's own storefront and customer-account
MCP servers, plus a theme-extension chat widget for the storefront UI.

**No source is vendored here.** `nuguard.yaml`'s `source:` points directly at
`https://github.com/Shopify/shop-chat-agent` — `nuguard sbom generate` clones
it internally for the duration of the scan. To actually *run* a live
instance, `clone-shop-chat-agent.sh` shallow-clones the source into `./repo/`
(gitignored), and `start-local.sh` builds/runs its own `Dockerfile`.

> **Important — this is a Shopify-embedded app, not a standalone service.**
> It is designed to run via `shopify app dev` against a real Shopify Partner
> app + development store (OAuth, App Bridge, Shopify CLI tunnel). This
> folder's `start-local.sh` runs it **standalone/degraded**: Claude answers
> via `CLAUDE_API_KEY`, but the storefront/customer MCP tool connections in
> `app/mcp-client.js` cannot succeed without a real store domain, so
> `app/routes/chat.jsx` falls back to "continuing without tools". Dynamic
> behavior/redteam scans against this local target exercise the base chat
> loop and endpoint surface only — not the product-search/cart/order MCP
> tools. To test those, connect a real dev store (see "Connecting a real dev
> store" below) and point `target.url`/`redteam.target` at the tunneled URL.

## Layout

| File | Purpose |
|---|---|
| `nuguard.yaml` | Config for the local Docker target (`http://localhost:3000`) |
| `.env.example` / `.env` | Claude + Shopify app identity env vars (`.env` is gitignored) |
| `clone-shop-chat-agent.sh` | Shallow-clone the source into `./repo/` (gitignored) |
| `start-local.sh` | Build/run shop-chat-agent locally via its own `Dockerfile` |
| `canary.example.json` | Synthetic conversation canary for behavior/redteam scanning |
| `shop-chat-agent-test.sh` | Full pipeline: sbom → policy draft/compile/check → analyze → behavior → redteam |
| `shop-chat-agent.ground-truth.sbom.json` | Hand-curated reference SBOM to benchmark `nuguard sbom generate` output against |

Cognitive policy (`cognitive-policy.md` / `cognitive-policy.json`) is **not**
checked in — `shop-chat-agent-test.sh` drafts it at runtime with
`nuguard init --llm`, grounded in the generated SBOM, then compiles it.
Delete it and re-run to regenerate.

## 1. Configure

```bash
cp .env.example .env
# Edit .env: set CLAUDE_API_KEY (required for any chat response at all).
# SHOPIFY_API_KEY/SHOPIFY_API_SECRET/SCOPES/REDIRECT_URL can stay as
# placeholders for the standalone local run (see warning above).
```

## 2. Run shop-chat-agent

### Local (Docker, standalone/degraded — no dev store)

```bash
./start-local.sh
```

Clones `./repo` on first run, builds the image from its `Dockerfile`, and
runs it on `http://localhost:3000` with a persisted SQLite volume at `./data`.

### Connecting a real dev store (full MCP tool functionality)

To exercise the actual product-search/cart/order MCP tools instead of the
degraded fallback, follow Shopify's own instructions instead of
`start-local.sh`:

```bash
cd repo   # after ./clone-shop-chat-agent.sh
npm install
npm run dev   # shopify app dev — logs into a Partner account, tunnels the
              # local server, and installs the app on a chosen dev store
```

Then update `target.url` / `redteam.target` in `nuguard.yaml` (or a copy of
it) to the printed tunnel URL before running the pipeline.

## 3. Run the pipeline

```bash
./shop-chat-agent-test.sh
```

Static stages (sbom/policy/analyze) work identically in both run modes since
they scan the GitHub source directly. Dynamic stages (behavior/redteam)
reflect whichever run mode is actually live at `target.url`.
