import { REQUIRED_LAUNCH_NUMBERS } from '../launchDocument';
import { setPath, type Doc } from '../paths';

/** A parameter document with every number a launch requires, plus fields the schema does not list. */
export function completeDocument(overrides: Record<string, unknown> = {}): Doc {
  let doc: Doc = {
    no_fly: false,
    lateral_pid: { kp: 0.42, ki: 0.01, kd: 0.05 },
    calibration: { samples: 12 },
    vision: { model_path: 'yolov8n.pt' },
    network: { drone_ip: '192.168.42.1' },
  };
  REQUIRED_LAUNCH_NUMBERS.forEach((path, index) => {
    doc = setPath(doc, path, index + 1);
  });
  for (const [path, value] of Object.entries(overrides)) doc = setPath(doc, path, value);
  return doc;
}
