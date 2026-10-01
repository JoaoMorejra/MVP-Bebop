export interface NativePhotoReport {
  fetched: Array<Record<string, unknown>>;
  error?: string;
}

export interface NativePhotoFetch {
  observe(message: { kind?: string; key?: string; payload?: unknown } | null | undefined): void;
  missionExited(): Promise<void>;
}

export declare function MEDIA_FETCH_ARGS(script: string, outputDir: string): string[];

export declare function createNativePhotoFetch(options: {
  run: () => Promise<{ code: number | null; stdout: string }>;
  onResult: (report: NativePhotoReport) => void;
  log: (text: string) => void;
}): NativePhotoFetch;
