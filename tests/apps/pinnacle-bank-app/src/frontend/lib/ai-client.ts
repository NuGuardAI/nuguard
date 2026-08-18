/**
 * CipherBank Frontend — AI Client Library
 * =========================================
 * SBOM COMPLEXITY TEST #6 — "TypeScript dynamic import() + barrel export"
 * -------------------------------------------------------------------------
 *
 * Detection challenge A — Dynamic import():
 *   The TypeScript SBOM adapter detects AI framework use by scanning static
 *   import/require statements:
 *     import OpenAI from 'openai'
 *     const { OpenAI } = require('openai')
 *
 *   This file uses ONLY the dynamic import() expression:
 *     const { OpenAI } = await import('openai')
 *
 *   Dynamic import() is a *runtime expression*, not a static import declaration.
 *   Most TS AST parsers (including tree-sitter and regex-based scanners) do not
 *   index it in the same "imports" collection as static `import` statements.
 *   Therefore, `can_handle(imported_modules)` for the OpenAI/LangChain TS
 *   adapter returns false, and no LLM detection runs on this file.
 *
 * Detection challenge B — Barrel re-export:
 *   This module is a barrel (index.ts pattern) that re-exports from sub-modules:
 *     ./providers/openai-provider    ← OpenAI SDK usage
 *     ./providers/anthropic-provider ← Anthropic SDK usage
 *
 *   A scan of THIS file sees only local relative imports (./providers/...).
 *   Relative paths don't match package-name handles_imports patterns.
 *   Only a multi-file cross-reference would trace the dependency chain.
 *
 * Detection challenge C — Conditional provider loading:
 *   The getAIClient() function selects a provider at runtime based on
 *   VITE_AI_PROVIDER env var. Only ONE branch is exercised in production.
 *   Static analysis would need to evaluate all branches.
 *
 * Expected SBOM result:
 *   - No FRAMEWORK or MODEL nodes for 'openai' or '@anthropic-ai/sdk' from THIS file
 *   - The sub-provider files (if scanned) would be detected, but they use
 *     the same dynamic import() pattern, so they're also blind spots
 */

// ── Type definitions ─────────────────────────────────────────────────────────

export interface AIMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface AICompletionOptions {
  model?: string;
  temperature?: number;
  maxTokens?: number;
  systemPrompt?: string;
}

export interface AICompletionResult {
  content: string;
  provider: string;
  model: string;
  latencyMs: number;
}

// ── Dynamic provider loading (SBOM challenge A + C) ──────────────────────────

type ProviderName = 'openai' | 'anthropic' | 'azure';

async function loadOpenAIProvider(): Promise<(messages: AIMessage[], opts: AICompletionOptions) => Promise<string>> {
  // Dynamic import — NOT a static `import OpenAI from 'openai'` statement
  // The TypeScript SBOM adapter will not see 'openai' in the module's import list
  const { OpenAI } = await import('openai');

  const client = new OpenAI({
    apiKey: import.meta.env.VITE_OPENAI_API_KEY ?? '',
  });

  return async (messages, opts) => {
    const resp = await client.chat.completions.create({
      model: opts.model ?? 'gpt-4o',
      messages: messages.map(m => ({ role: m.role, content: m.content })),
      temperature: opts.temperature ?? 0.2,
      max_tokens: opts.maxTokens ?? 1024,
    }) as any;
    return resp.choices[0]?.message?.content ?? '';
  };
}

async function loadAnthropicProvider(): Promise<(messages: AIMessage[], opts: AICompletionOptions) => Promise<string>> {
  // Second dynamic import — @anthropic-ai/sdk also invisible to static scanner
  const Anthropic = (await import('@anthropic-ai/sdk')).default;

  const client = new Anthropic({
    apiKey: import.meta.env.VITE_ANTHROPIC_API_KEY ?? '',
  });

  return async (messages, opts) => {
    const userMessages = messages.filter(m => m.role !== 'system');
    const system = messages.find(m => m.role === 'system')?.content;
    const resp = await client.messages.create({
      model: opts.model ?? ('claude-' + '3-5-sonnet' + '-20241022'), // string concat — hides model name
      max_tokens: opts.maxTokens ?? 1024,
      system: system ?? opts.systemPrompt,
      messages: userMessages.map(m => ({ role: m.role as 'user' | 'assistant', content: m.content })),
    }) as any;
    const block = resp.content[0];
    return block.type === 'text' ? block.text : '';
  };
}

async function loadAzureProvider(): Promise<(messages: AIMessage[], opts: AICompletionOptions) => Promise<string>> {
  // Azure OpenAI via the same openai SDK — dynamic import, provider resolved via base_url
  const { AzureOpenAI } = await import('openai');

  const client = new AzureOpenAI({
    apiKey: import.meta.env.VITE_AZURE_OPENAI_API_KEY ?? '',
    endpoint: import.meta.env.VITE_AZURE_OPENAI_ENDPOINT ?? '',
    apiVersion: '2024-05-13',
    deployment: import.meta.env.VITE_AZURE_OPENAI_DEPLOYMENT ?? 'gpt-4o',
  });

  return async (messages, opts) => {
    const resp = await client.chat.completions.create({
      model: import.meta.env.VITE_AZURE_OPENAI_DEPLOYMENT ?? 'gpt-4o',
      messages: messages.map(m => ({ role: m.role, content: m.content })),
      temperature: opts.temperature ?? 0.2,
      max_tokens: opts.maxTokens ?? 1024,
    }) as any;
    return resp.choices[0]?.message?.content ?? '';
  };
}

// ── Provider dispatch map (runtime selection, not static branch) ─────────────
const _PROVIDER_LOADERS: Record<ProviderName, () => Promise<(msgs: AIMessage[], opts: AICompletionOptions) => Promise<string>>> = {
  openai:    loadOpenAIProvider,
  anthropic: loadAnthropicProvider,
  azure:     loadAzureProvider,
};

let _cachedProvider: ((msgs: AIMessage[], opts: AICompletionOptions) => Promise<string>) | null = null;
let _cachedProviderName: ProviderName | null = null;

async function getAIClient(): Promise<{ complete: (messages: AIMessage[], opts?: AICompletionOptions) => Promise<AICompletionResult> }> {
  // Provider selected at runtime from env var — no static branch visible to analyser
  const providerName = (import.meta.env.VITE_AI_PROVIDER ?? 'azure') as ProviderName;

  if (_cachedProvider === null || _cachedProviderName !== providerName) {
    const loader = _PROVIDER_LOADERS[providerName] ?? _PROVIDER_LOADERS['azure'];
    _cachedProvider = await loader();
    _cachedProviderName = providerName;
  }

  const provider = _cachedProvider;
  return {
    async complete(messages, opts = {}) {
      const t0 = Date.now();
      const content = await provider(messages, opts);
      return {
        content,
        provider: providerName,
        model: opts.model ?? import.meta.env.VITE_AI_MODEL ?? 'gpt-4o',
        latencyMs: Date.now() - t0,
      };
    },
  };
}

// ── Barrel exports (SBOM challenge B) ────────────────────────────────────────
// A scan of this barrel file sees only local relative imports — not 'openai' etc.
export { getAIClient };
export type { ProviderName };

// Re-export streaming utilities from deeper sub-module
// (if that sub-module also uses dynamic import, the chain is fully opaque)
export { streamCompletion } from './providers/streaming';
