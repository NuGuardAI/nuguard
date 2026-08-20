# shop-chat-agent — NuGuard Test Target

[shop-chat-agent](https://github.com/Shopify/shop-chat-agent) is Shopify's
reference embedded app: a Claude-powered (patched here to Azure OpenAI —
see `repo/app/services/claude.server.js`) storefront shopping assistant
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
> folder's `start-local.sh` runs it **standalone/degraded**: the model answers
> via `AZURE_OPENAI_*` (patched from Claude — see "Model backend" below), but
> the storefront/customer MCP tool connections in
> `app/mcp-client.js` cannot succeed without a real store domain, so
> `app/routes/chat.jsx` falls back to "continuing without tools". Dynamic
> behavior/redteam scans against this local target exercise the base chat
> loop and endpoint surface only — not the product-search/cart/order MCP
> tools. To test those, connect a real dev store (see "Connecting a real dev
> store" below) and point `target.url`/`redteam.target` at the tunneled URL.

## Model backend (patched: Azure OpenAI instead of Claude)

Upstream `shop-chat-agent` calls Anthropic's Claude API directly
(`@anthropic-ai/sdk`) in `app/services/claude.server.js`. For this test
target, that file has been patched (in `./repo`, the gitignored checkout —
not upstream) to call **Azure OpenAI** Chat Completions instead, via a plain
streamed `fetch()`. The rest of the app (`chat.jsx`, `mcp-client.js`,
`tool.server.js`) is untouched: the patch translates to/from the same
Claude-shaped message contract (`role`/`content` blocks of type
`text`/`tool_use`/`tool_result`, `stop_reason`) those files already depend on.

Set `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_KEY` / `AZURE_OPENAI_MODEL_NAME`
(the deployment name) in `.env`. Because this patch lives only in the local
`./repo` checkout, `nuguard sbom generate` (which scans the pristine
`source:` URL) still reports the upstream Claude/Anthropic `MODEL` node —
this is an intentional, expected divergence between the static SBOM and the
locally-running dynamic target.

Two small resilience patches were also needed to make `/chat` usable at all
without a real Shopify store (both are pre-existing bugs, not introduced by
the model swap — `getCustomerAccountUrls()` already caught its own errors and
returned `null`, but the caller didn't guard against that): `chat.jsx` no
longer crashes destructuring `mcpApiUrl` from a `null` result, and
`mcp-client.js`'s constructor no longer crashes calling `.replace()` on a
`null` `hostUrl` when no `Origin` header is sent.

## Layout

| File | Purpose |
|---|---|
| `nuguard.yaml` | Config for the local Docker target (`http://localhost:5501`) |
| `.env.example` / `.env` | Azure OpenAI + Shopify app identity env vars (`.env` is gitignored) |
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
# Edit .env: set AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_KEY/AZURE_OPENAI_MODEL_NAME
# (required for any chat response at all — see "Model backend" below).
# SHOPIFY_API_KEY/SHOPIFY_API_SECRET/SCOPES/REDIRECT_URL can stay as
# placeholders for the standalone local run (see warning above).
```

## 2. Run shop-chat-agent

### Local (Docker, standalone/degraded — no dev store)

```bash
./start-local.sh
```

Clones `./repo` on first run, builds the image from its `Dockerfile`, and
runs it on `http://localhost:5501` with the SQLite database persisted in a
named Docker volume (`shop-chat-agent-nuguard-prisma`).

### Connecting a real dev store (full MCP tool functionality)

The storefront/customer MCP servers are **hosted by Shopify itself**, at
`https://<your-store>.myshopify.com/api/mcp` (storefront) and
`https://<your-store>.account.myshopify.com/customer/api/mcp` (customer) —
nothing in this repo runs them. `MCPClient` (`app/mcp-client.js`) derives
those URLs from the client-supplied `Origin` header on each `/chat` request
(`shopDomain`), so making the tools operational is entirely about connecting
a real store and sending the right headers — no code changes needed.

1. **Get a dev store.** Create a [Shopify Partner account](https://partners.shopify.com)
   (free) and a development store from it, or reuse an existing one.
2. **Disable the storefront password.** Dev stores are password-protected by
   default (Online Store → Preferences → uncheck "Restrict access"), otherwise
   the storefront MCP endpoint 404s/redirects for anonymous requests.
3. **Run the real dev flow instead of `start-local.sh`:**
   ```bash
   cd repo   # after ./clone-shop-chat-agent.sh
   npm install
   npm run dev   # shopify app dev — logs into your Partner account, tunnels
                 # the local server, and installs the app on the chosen dev store
   ```
   This prints a tunnel URL and installs the theme extension on your store —
   visit the storefront and the chat widget will now have working storefront
   tools (`search_shop_catalog`, `update_cart`, `get_cart`,
   `search_shop_policies_and_faqs`) because the widget sends a real `Origin`.
4. **Point NuGuard at it.** For automated behavior/redteam scans (no browser,
   so no widget), set `target.url` to the tunnel URL and add the store's
   `Origin` header explicitly so NuGuard's requests are indistinguishable
   from the widget's:
   ```yaml
   target:
     url: https://REPLACE-WITH-TUNNEL-URL
     endpoint: /chat
     headers:
       Origin: https://your-store.myshopify.com
   ```
   Verify manually first: `curl -X POST <tunnel-url>/chat -H 'Accept: text/event-stream' -H 'Origin: https://your-store.myshopify.com' -d '{"message":"search for snowboards"}'`
   should now show `tool_use` events for `search_shop_catalog` in the SSE stream.

5. **Customer tools (`get_order_status`, `get_most_recent_order_status`)
   need more than an `Origin` header** — they require the Customer Account
   API scopes commented out in `shopify.app.toml`
   (`customer_read_customers,customer_read_orders,...`) plus a real,
   logged-in customer completing the PKCE OAuth flow (`app/auth.server.js`).
   That flow is interactive (a redirect + login), so it can't be driven by
   NuGuard's HTTP-only dynamic scanner out of the box — treat customer-tool
   coverage as a manual/browser-driven test, or pre-seed a `CustomerToken` row
   (see `prisma/schema.prisma`) for a test conversation ID before scanning.

## 3. Run the pipeline

```bash
./shop-chat-agent-test.sh
```

Static stages (sbom/policy/analyze) work identically in both run modes since
they scan the GitHub source directly. Dynamic stages (behavior/redteam)
reflect whichever run mode is actually live at `target.url`.
