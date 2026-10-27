import type { ServerResponse } from 'node:http';

export declare function resolveTilePath(distDir: string, urlPath: string): string | null;
export declare function serveTile(distDir: string, urlPath: string, res: ServerResponse): void;
