// Standalone type declarations — no vite package required at development time.
// When bundled with Vite these are superseded by the official vite/client types.

interface ImportMetaEnv {
  readonly VITE_AI_PROVIDER: string;
  readonly VITE_AI_MODEL: string;
  readonly VITE_OPENAI_API_KEY: string;
  readonly VITE_ANTHROPIC_API_KEY: string;
  readonly VITE_AZURE_OPENAI_API_KEY: string;
  readonly VITE_AZURE_OPENAI_ENDPOINT: string;
  readonly VITE_AZURE_OPENAI_DEPLOYMENT: string;
  [key: string]: string | undefined;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Stub module declarations so TS resolves dynamic import() without node_modules
/* eslint-disable @typescript-eslint/no-explicit-any */
declare module 'openai' {
  export class OpenAI {
    constructor(options?: Record<string, unknown>);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    chat: { completions: { create(params: Record<string, unknown>): Promise<any> } };
  }
  export class AzureOpenAI extends OpenAI {}
}

declare module '@anthropic-ai/sdk' {
  class Anthropic {
    constructor(options?: Record<string, unknown>);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    messages: { create(params: Record<string, unknown>): Promise<any> };
  }
  export default Anthropic;
}
