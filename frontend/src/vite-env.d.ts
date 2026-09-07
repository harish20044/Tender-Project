/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Empty in development, where Vite proxies /api to the backend, so requests
   * stay same-origin. Set at build time when the API is served from another
   * host.
   */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
