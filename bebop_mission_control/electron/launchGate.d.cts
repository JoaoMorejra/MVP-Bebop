export interface LaunchGate {
  begin(): boolean;
  cancel(): boolean;
  cancelled(): boolean;
  starting(): boolean;
  end(): void;
}

export declare function createLaunchGate(): LaunchGate;
