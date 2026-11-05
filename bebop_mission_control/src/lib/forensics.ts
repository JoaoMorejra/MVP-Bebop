/**
 * The preliminary report the station reads out once the aircraft is down.
 *
 * Four findings, one per question an adjuster has to clear before a claim can
 * be closed without sending anyone to the scene: police, ambulance, the
 * casualty, the vehicle. Police, SAMU and the vehicle are fixed clearances, the
 * vehicle one always naming the tow truck called to clear the road; the
 * casualty line follows the victim assessment of the inspection frame
 * (`mission.inspection_ready`) and is neutral when no inference concluded.
 * Each exists in several wordings and the report is drawn per flight with the
 * copilot's cross-flight memory (`copilotPhrases`): no topic is read with the
 * wording it had on the previous flight. The report opens with the casualty
 * when one was detected and with the vehicle otherwise (see
 * {@link leadTopicsFor}), then SAMU and police, whose order alternates from
 * one flight to the next.
 *
 * The same tables exist in `mvp_mission_bebop/telemetry/inspection_report.py`
 * for the mission process to narrate on its own. This copy is the authority whenever
 * the station is driving: it decides the order, shows the card and hands the
 * copilot the exact sentence to read, so the line in the operator's ear and the
 * line on screen are the same string rather than two renderings of one idea.
 */

import type { InspectionAssessment, VictimStatus } from '../types/bmg';
import { victimStatusOf } from '../types/bmg';
import type { FindingHistoryKey, StorageLike } from './copilotPhrases';
import { loadHistory, nextPhrase, pickVariant, recordVariant, saveHistory } from './copilotPhrases';

export type FindingTopic = 'police' | 'samu' | 'victim' | 'vehicle';

export interface Finding {
  topic: FindingTopic;
  /** The line as the card shows it. */
  card: string;
  /** The same line as the copilot reads it, led by a connector that ties it to the one before. */
  speech: string;
}

/**
 * The fixed clearances. Every vehicle wording states that a tow truck was
 * called to clear the road, by order of the station's operator. Mirrored by
 * `FIXED_FINDINGS` in `telemetry/inspection_report.py`.
 */
export const FIXED_WORDINGS: Readonly<Record<'police' | 'samu' | 'vehicle', readonly string[]>> = {
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
  vehicle: [
    'Veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via',
    'Veículo sem danos estruturais aparentes, com guincho já acionado devido à obstrução da via',
    'Sem danos estruturais aparentes no veículo. Guincho acionado para liberar a via',
    'Integridade do automóvel preservada. Guincho acionado para desobstrução da via',
    'Veículo sem avarias aparentes. Guincho já acionado para remover a obstrução da via',
  ],
};

/**
 * The victim line per victim status. `unknown` is neutral: without a completed
 * inference nothing is said about presence, absence, consciousness or injuries.
 * Mirrored by `VICTIM_FINDINGS` in `telemetry/inspection_report.py`.
 */
export const VICTIM_WORDINGS: Readonly<Record<VictimStatus, readonly string[]>> = {
  detected: [
    'Vítima consciente e sem ferimentos aparentes',
    'Acidentado consciente, sem ferimentos aparentes',
    'Vítima identificada, consciente e sem lesões aparentes',
    'Estado do acidentado não é grave, sem ferimentos aparentes',
    'Acidentado consciente e sem ferimentos críticos aparentes',
  ],
  not_detected: [
    'Nenhuma vítima visível na cena',
    'Sem vítimas visíveis no local',
    'Nenhum acidentado identificado na imagem',
    'Cena sem vítimas aparentes',
    'Ausência de vítimas visíveis na imagem',
    'Nenhuma vítima visível no local',
  ],
  unknown: [
    'Avaliação da vítima não incluída neste laudo preliminar',
    'Condição da vítima a confirmar em verificação presencial',
    'Sem dados para avaliar a vítima neste registro',
    'Avaliação da vítima pendente de conferência no local',
  ],
};

/** Topics that may open the report. */
export const LEAD_TOPICS: readonly ['vehicle', 'victim'] = ['vehicle', 'victim'];

/**
 * The first two topics of the report: the victim leads when one was detected;
 * with none detected, or an inference that did not conclude (`unknown`), the
 * vehicle leads and nothing is asserted about the victim before it.
 */
export function leadTopicsFor(status: VictimStatus): readonly ['vehicle', 'victim'] | readonly ['victim', 'vehicle'] {
  return status === 'detected' ? ['victim', 'vehicle'] : ['vehicle', 'victim'];
}

/** Orders of the dispatch topics, as indexed by `finding.dispatch_order`. */
export const DISPATCH_ORDERS: readonly (readonly ['samu', 'police'] | readonly ['police', 'samu'])[] = [
  ['samu', 'police'],
  ['police', 'samu'],
];

const TOPICS: readonly FindingTopic[] = ['police', 'samu', 'victim', 'vehicle'];

/**
 * What leads each finding when it is read, so the report sounds like one
 * assessment being talked through rather than a numbered list. Every finding
 * is a clearance ("sem necessidade de polícia"), so the connectors add; none
 * contrasts. Each pool is longer than the history window, so no connector
 * repeats the previous flight's in the same position.
 */
export const OPENERS = [
  'Para começar,',
  'De início,',
  'Logo de cara,',
  'Começando pelo essencial,',
  'De saída,',
  'Abrindo a análise,',
] as const;
export const LINKERS = [
  'Além disso,',
  'Na sequência,',
  'Somado a isso,',
  'Em seguida,',
  'Outro ponto:',
  'Também observamos:',
  'E mais:',
] as const;
export const CLOSERS = [
  'Por fim,',
  'Para fechar,',
  'Para arrematar,',
  'E, para concluir,',
  'Encerrando,',
  'Por último,',
] as const;

/**
 * The title of each card, so the topic is legible without reading the
 * wording. None repeats a wording of its own topic ("Segurança pública" was
 * one of the police clearances).
 */
export const TOPIC_LABEL: Record<FindingTopic, string> = {
  police: 'Apoio policial',
  samu: 'Resgate médico · SAMU',
  victim: 'Avaliação da vítima',
  vehicle: 'Análise do veículo',
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
 * Enforce the order invariants: the four topics exactly once each, opening
 * with the vehicle or the victim, never with a dispatch topic.
 *
 * @throws RangeError when `topics` breaks either.
 */
export function checkTopicOrder(topics: readonly FindingTopic[]): void {
  if (topics.length !== TOPICS.length || new Set(topics).size !== TOPICS.length) {
    throw new RangeError(`a report covers ${TOPICS.join(', ')} exactly once each, got ${topics.join(', ')}`);
  }
  if (!(LEAD_TOPICS as readonly string[]).includes(topics[0])) {
    throw new RangeError(`a report opens with ${LEAD_TOPICS.join(' or ')}, never ${topics[0]}`);
  }
}

/**
 * Draw one report and remember it.
 *
 * The report opens with the victim when one was detected and with the vehicle
 * otherwise, then the other of the two, then SAMU and police in the order the
 * previous flight did not use. Police, SAMU and the vehicle draw from their fixed pools; the victim line from the pool
 * of the assessment's victim status, `unknown` without one. Every wording and
 * connector avoids its recent variants (`pickVariant`). The choices are
 * recorded when drawn, once per flight, in the copilot history document. With
 * no storage it still draws a report, only without the memory. Same algorithm
 * as `build_inspection_report` in `telemetry/inspection_report.py`.
 */
export function buildForensicReport(
  options: {
    assessment?: InspectionAssessment | null;
    /** Imposes the victim status instead of deriving it from `assessment`. */
    victimStatus?: VictimStatus;
    storage?: StorageLike | null;
    random?: () => number;
  } = {}
): Finding[] {
  const storage = options.storage;
  const status = options.victimStatus ?? victimStatusOf(options.assessment ?? null);
  let history = storage === undefined ? loadHistory() : loadHistory(storage);

  const dispatch = pickVariant(
    'finding.dispatch_order',
    DISPATCH_ORDERS.map((order) => order.join(',')),
    history,
    options.random
  );
  history = recordVariant(history, 'finding.dispatch_order', dispatch.index);
  const topics: FindingTopic[] = [...leadTopicsFor(status), ...DISPATCH_ORDERS[dispatch.index]];
  checkTopicOrder(topics);

  const cards: Partial<Record<FindingTopic, string>> = {};
  for (const topic of ['police', 'samu', 'vehicle'] as const) {
    const key = `finding.${topic}` as const;
    const wording = pickVariant(key, FIXED_WORDINGS[topic], history, options.random);
    history = recordVariant(history, key, wording.index);
    cards[topic] = wording.text;
  }
  const victimKey: FindingHistoryKey = `finding.victim.${status}`;
  const victim = pickVariant(victimKey, VICTIM_WORDINGS[status], history, options.random);
  history = recordVariant(history, victimKey, victim.index);
  cards.victim = victim.text;

  const report = topics.map((topic, index) => {
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
    const card = cards[topic] as string;
    return { topic, card, speech: `${connector.text} ${decapitalise(card)}.` };
  });

  if (storage === undefined) saveHistory(history);
  else saveHistory(history, storage);
  return report;
}

/**
 * Everything the copilot says for one capture: the intro, the four findings
 * and the outro, drawn together so all six lines can be synthesized ahead.
 */
export interface ForensicScript {
  /** Stamp of the capture the script belongs to, or null when unknown. */
  captureStamp: string | null;
  victimStatus: VictimStatus;
  /** Why the victim line is neutral (`timeout`, the assessment's reason), or null. */
  reason: string | null;
  intro: string;
  findings: Finding[];
  outro: string;
}

/**
 * `reason` of a report whose victim finding follows the operator's switch off
 * (`vision.victim_detection_enabled`). Same string as
 * `perception/assessment.py:REASON_VICTIM_DETECTION_DISABLED`.
 */
export const REASON_VICTIM_DETECTION_DISABLED = 'victim_detection_disabled';

/**
 * Draw the report of one capture with its intro and outro.
 *
 * `assessment` null means no inference concluded (the station's own wait ran
 * out, `reason` says why): the victim line is then the neutral one. Nothing is
 * ever filled in for a missing result.
 */
export function drawForensicScript(
  options: {
    assessment: InspectionAssessment | null;
    /** Imposes the victim status instead of deriving it from `assessment`. */
    victimStatus?: VictimStatus;
    captureStamp?: string | null;
    reason?: string | null;
    storage?: StorageLike | null;
    random?: () => number;
  }
): ForensicScript {
  const { assessment, storage, random } = options;
  const phraseOptions = storage === undefined ? { random } : { storage, random };
  const victimStatus = options.victimStatus ?? victimStatusOf(assessment);
  const findings = buildForensicReport({ assessment, victimStatus, storage, random });
  return {
    captureStamp: options.captureStamp ?? assessment?.capture_stamp ?? null,
    victimStatus,
    reason: assessment ? assessment.reason : options.reason ?? null,
    intro: nextPhrase('inspection.intro', {}, phraseOptions),
    findings,
    outro: nextPhrase('inspection.outro', {}, phraseOptions),
  };
}

/** The six lines of a script in reading order: intro, findings, outro. */
export function scriptLines(script: ForensicScript): string[] {
  return [script.intro, ...script.findings.map((finding) => finding.speech), script.outro];
}

/**
 * Every sentence a finding can be read as: each connector with each wording.
 * The report stays drawn per flight (D1); this only lets its audio be
 * synthesized ahead, for the copilot's phrase cache.
 */
export function allFindingSentences(): string[] {
  const connectors = [...OPENERS, ...LINKERS, ...CLOSERS];
  const out: string[] = [];
  const wordings = [
    ...FIXED_WORDINGS.police,
    ...FIXED_WORDINGS.samu,
    ...FIXED_WORDINGS.vehicle,
    ...VICTIM_WORDINGS.detected,
    ...VICTIM_WORDINGS.not_detected,
    ...VICTIM_WORDINGS.unknown,
  ];
  for (const wording of wordings) {
    for (const connector of connectors) out.push(`${connector} ${decapitalise(wording)}.`);
  }
  return out;
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
