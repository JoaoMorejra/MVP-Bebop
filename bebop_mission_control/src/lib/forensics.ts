/**
 * The preliminary report the station reads out once the aircraft is down.
 *
 * Four findings, one per question an adjuster has to clear before a claim can
 * be closed without sending anyone to the scene: police, ambulance, the
 * casualty, the vehicle. Each exists in several wordings and the order is drawn
 * per flight with the copilot's cross-flight memory (`copilotPhrases`): no
 * topic is read with the wording it had on the previous flight, and the topic
 * order does not repeat any of the last five — what the operator is watching
 * is an assessment being presented, not a slide being replayed.
 *
 * The same table exists in `mvp_mission_bebop/telemetry/announcer.py` for the
 * mission process to narrate on its own. This copy is the authority whenever
 * the station is driving: it decides the order, shows the card and hands the
 * copilot the exact sentence to read, so the line in the operator's ear and the
 * line on screen are the same string rather than two renderings of one idea.
 */

import type { FindingHistoryKey, StorageLike } from './copilotPhrases';
import { loadHistory, pickVariant, recordVariant, saveHistory } from './copilotPhrases';

export type FindingTopic = 'police' | 'samu' | 'victim' | 'vehicle';

export interface Finding {
  topic: FindingTopic;
  /** The line as the card shows it. */
  card: string;
  /** The same line as the copilot reads it, led by a connector that ties it to the one before. */
  speech: string;
}

const WORDINGS: Record<FindingTopic, readonly string[]> = {
  police: [
    'Sem necessidade de polícia',
    'Acionamento policial dispensado',
    'Sem demanda para autoridade policial',
    'Segurança pública não requisitada',
  ],
  samu: [
    'Sem necessidade de Samu',
    'Socorro médico dispensado',
    'Atendimento emergencial não necessário',
    'SAMU dispensado para a ocorrência',
  ],
  victim: [
    'Estado do acidentado não é grave',
    'Vítima consciente e sem gravidade',
    'Acidentado sem ferimentos críticos',
    'Condição da vítima considerada leve',
  ],
  vehicle: [
    'Veículo não danificado',
    'Sem danos estruturais aparentes no veículo',
    'Integridade do automóvel preservada',
    'Veículo sem avarias mecânicas',
  ],
};

const TOPICS: readonly FindingTopic[] = ['police', 'samu', 'victim', 'vehicle'];

/**
 * What leads each finding when it is read, so the report sounds like one
 * assessment being talked through rather than a numbered list. Every finding
 * is a clearance ("sem necessidade de polícia"), so the connectors add; none
 * contrasts. Each pool is longer than the history window, so no connector
 * repeats the previous flight's in the same position.
 */
const OPENERS = [
  'Para começar,',
  'De início,',
  'Logo de cara,',
  'Começando pelo essencial,',
  'De saída,',
  'Abrindo a análise,',
] as const;
const LINKERS = [
  'Além disso,',
  'Na sequência,',
  'Somado a isso,',
  'Em seguida,',
  'Outro ponto:',
  'Também observamos:',
  'E mais:',
] as const;
const CLOSERS = [
  'Por fim,',
  'Para fechar,',
  'Para arrematar,',
  'E, para concluir,',
  'Encerrando,',
  'Por último,',
] as const;

/** Every order the four topics can be read in, in a fixed enumeration. */
const ORDERS: readonly (readonly FindingTopic[])[] = (() => {
  const out: FindingTopic[][] = [];
  const permute = (rest: FindingTopic[], prefix: FindingTopic[]) => {
    if (rest.length === 0) {
      out.push(prefix);
      return;
    }
    rest.forEach((topic, index) =>
      permute([...rest.slice(0, index), ...rest.slice(index + 1)], [...prefix, topic])
    );
  };
  permute([...TOPICS], []);
  return out;
})();

const ORDER_LABELS: readonly string[] = ORDERS.map((order) => order.join(','));

/** The label under each card, so the topic is legible without reading the wording. */
export const TOPIC_LABEL: Record<FindingTopic, string> = {
  police: 'Autoridade policial',
  samu: 'Socorro médico',
  victim: 'Estado do acidentado',
  vehicle: 'Integridade do veículo',
};

/**
 * Lower the first letter so the line reads on after its connector.
 *
 * An acronym is left standing: "SAMU dispensado" must not become "sAMU".
 */
function decapitalise(line: string): string {
  if (line.length > 1 && line[1] === line[1].toUpperCase() && line[1] !== line[1].toLowerCase()) {
    return line;
  }
  return line[0].toLowerCase() + line.slice(1);
}

/**
 * Draw one report and remember it.
 *
 * The order is one of the 24 permutations, excluding those of the last five
 * flights; each topic's wording excludes its recent ones, which with four
 * wordings means never the one it had on the previous flight. The choices are
 * recorded when drawn, once per flight, in the same history document as the
 * copilot's milestone calls. With no storage it still draws a report, only
 * without the memory.
 */
export function buildForensicReport(
  options: { storage?: StorageLike | null; random?: () => number } = {}
): Finding[] {
  const storage = options.storage;
  let history = storage === undefined ? loadHistory() : loadHistory(storage);

  const order = pickVariant('finding.order', ORDER_LABELS, history, options.random);
  history = recordVariant(history, 'finding.order', order.index);

  const topics = ORDERS[order.index];
  const report = topics.map((topic, index) => {
    const key = `finding.${topic}` as FindingHistoryKey;
    const wording = pickVariant(key, WORDINGS[topic], history, options.random);
    history = recordVariant(history, key, wording.index);

    // Two linkers in one report are drawn one after the other, each recorded
    // before the next, so they never repeat within the report either.
    const [connectorKey, pool]: [FindingHistoryKey, readonly string[]] =
      index === 0
        ? ['finding.opener', OPENERS]
        : index === topics.length - 1
        ? ['finding.closer', CLOSERS]
        : ['finding.linker', LINKERS];
    const connector = pickVariant(connectorKey, pool, history, options.random);
    history = recordVariant(history, connectorKey, connector.index);

    return {
      topic,
      card: wording.text,
      speech: `${connector.text} ${decapitalise(wording.text)}.`,
    };
  });

  if (storage === undefined) saveHistory(history);
  else saveHistory(history, storage);
  return report;
}

/**
 * The cadence between findings when the copilot is not the one setting it.
 *
 * With the voice bridge present each card waits for its own sentence to finish,
 * which is the real pacing. This is the fallback for a browser session or a
 * copilot that never answers: roughly the same rhythm, drawn per line so the
 * sequence does not tick like a metronome.
 */
export function findingGapMs(): number {
  return 1800 + Math.random() * 400;
}
