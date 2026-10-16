import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
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

/**
 * `MissionParameters.factory()` as `mission.py --dump-defaults` serves it.
 *
 * Read from `test/fixtures/mission_factory_document.json`, which
 * `test/test_shared_fixtures.py` holds equal to the dataclass defaults.
 */
export function factoryDocument(overrides: Record<string, unknown> = {}): Doc {
  let doc = JSON.parse(
    readFileSync(resolve(__dirname, '../../../../test/fixtures/mission_factory_document.json'), 'utf-8')
  ) as Doc;
  for (const [path, value] of Object.entries(overrides)) doc = setPath(doc, path, value);
  return doc;
}

/**
 * A document a launch accepts: the factory document with the return cruise
 * coupled to the forward cruise, as the station always sends it.
 */
export function validDocument(overrides: Record<string, unknown> = {}): Doc {
  let doc = factoryDocument({ 'rtl.reverse_cruise_velocity': -0.2 });
  for (const [path, value] of Object.entries(overrides)) doc = setPath(doc, path, value);
  return doc;
}
