export interface SiteAnchor {
  latitude: number;
  longitude: number;
  accuracyM: number;
  name: string;
}

export declare function readSiteAnchorFile(file: string): SiteAnchor | null;
