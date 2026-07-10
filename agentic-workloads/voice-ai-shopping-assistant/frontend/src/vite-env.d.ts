/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SESSION_API_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
