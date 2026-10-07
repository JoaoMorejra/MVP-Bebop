import { REQUIRED_LAUNCH_NUMBERS } from '../launchDocument';
import { ALL_PARAMETERS } from '../parameterSchema';
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

/**
 * A document a launch accepts: every sheet field at the bottom of its range
 * and the other required numbers at plausible values. `completeDocument` is
 * for telling fields apart, not for launching (its values are 1, 2, 3...).
 */
export function validDocument(overrides: Record<string, unknown> = {}): Doc {
  let doc = completeDocument({
    'kinematics.hover_duration_sec': 7,
    'rtl.max_speed': 0.1,
    'rtl.arrival_radius_m': 0.2,
    'vision.confidence_threshold': 0.5,
  });
  for (const spec of ALL_PARAMETERS) doc = setPath(doc, spec.path, spec.min);
  for (const [path, value] of Object.entries(overrides)) doc = setPath(doc, path, value);
  return doc;
}
