/**
 * Print the station's speech manifest as JSON on stdout.
 *
 *   npx vite-node scripts/exportSpeechManifest.ts [--parameters FILE] [--config FILE]
 *
 * Parameter sets (D7): the fair parameters, the schema defaults and the
 * machine's `mvp_mission_bebop/mission_config.json` (`BMG_MISSION_CONFIG` when
 * set; `--config` overrides both), each in the four flight plans;
 * `--parameters FILE` adds a JSON list of further `mission.parameters`
 * documents. The voice is left null: it belongs to the
 * synthesizer (`telemetry/announcer.py`), which `streamer/precache_speech.py`
 * fills in. Diagnostics go to stderr so stdout stays one JSON document.
 */

import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { MissionParametersPayload } from '../src/lib/copilotPhrases';
import {
  FAIR_PARAMETERS,
  SCHEMA_DEFAULT_PARAMETERS,
  buildSpeechManifest,
  buildSpeechTable,
  missionConfigParameters,
  speechParameterSets,
} from '../src/lib/spokenCatalog';

const SCHEMA = 'bmg.speech_manifest.v1';
const BMG_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
/** `BMG_MISSION_CONFIG` redirects the store, as in `electron/main.cjs:CONFIG_PATH`. */
const DEFAULT_CONFIG =
  process.env.BMG_MISSION_CONFIG || resolve(BMG_ROOT, '..', 'mvp_mission_bebop', 'mission_config.json');

interface Options {
  parametersFile: string | null;
  configFile: string;
}

function parseArgs(argv: readonly string[]): Options {
  const options: Options = { parametersFile: null, configFile: DEFAULT_CONFIG };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (flag === '--parameters' || flag === '--config') {
      if (value === undefined || value.startsWith('--')) throw new Error(`${flag} needs a file path`);
      if (flag === '--parameters') options.parametersFile = resolve(value);
      else options.configFile = resolve(value);
      index += 1;
      continue;
    }
    throw new Error(`unknown argument: ${flag}`);
  }
  return options;
}

function readJson(path: string): unknown {
  return JSON.parse(readFileSync(path, 'utf8')) as unknown;
}

/** A `mission.parameters` document: a JSON object whose values are finite numbers. */
function toParameters(value: unknown, where: string): MissionParametersPayload {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new TypeError(`${where} must be a JSON object`);
  }
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    if (typeof entry !== 'number' || !Number.isFinite(entry)) {
      throw new TypeError(`${where}.${key} must be a finite number`);
    }
  }
  return value as MissionParametersPayload;
}

function extraParameters(path: string): MissionParametersPayload[] {
  const doc = readJson(path);
  if (!Array.isArray(doc)) throw new TypeError(`${path} must hold a JSON list of parameter documents`);
  return doc.map((entry, index) => toParameters(entry, `${path}[${index}]`));
}

function sameParameters(a: MissionParametersPayload, b: MissionParametersPayload): boolean {
  return JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
}

function main(): void {
  const options = parseArgs(process.argv.slice(2));
  const parameters: MissionParametersPayload[] = [FAIR_PARAMETERS, SCHEMA_DEFAULT_PARAMETERS];
  if (existsSync(options.configFile)) {
    parameters.push(missionConfigParameters(readJson(options.configFile)));
  } else {
    process.stderr.write(`[speech-manifest] ${options.configFile} not found; machine parameters skipped\n`);
  }
  if (options.parametersFile) parameters.push(...extraParameters(options.parametersFile));

  const distinct = parameters.filter(
    (candidate, index) => parameters.findIndex((other) => sameParameters(other, candidate)) === index
  );
  const entries = buildSpeechManifest(speechParameterSets(distinct));
  const document = {
    schema: SCHEMA,
    voice: null,
    parameter_sets: distinct,
    entries,
    table: buildSpeechTable(entries),
  };
  process.stdout.write(`${JSON.stringify(document, null, 2)}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`[speech-manifest] ${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(2);
}
