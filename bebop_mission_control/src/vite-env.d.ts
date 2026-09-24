/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MAP_API_KEY?: string;
  readonly VITE_MAPBOX_TOKEN?: string;
  readonly VITE_MAPTILER_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
