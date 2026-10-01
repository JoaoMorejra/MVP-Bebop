export type ParameterFileResult =
  | { success: true; params: Record<string, unknown>; missing?: undefined; error?: undefined }
  | { success: false; missing: true; error?: undefined; params?: undefined }
  | { success: false; error: string; missing?: undefined; params?: undefined };

export declare function readParameterFile(file: string): ParameterFileResult;
export declare function writeJsonAtomic(file: string, doc: unknown): string;
