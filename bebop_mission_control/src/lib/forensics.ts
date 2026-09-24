/**
 * The preliminary report the station reads out once the aircraft is down.
 *
 * Four findings, one per question an adjuster has to clear before a claim can
 * be closed without sending anyone to the scene: police, ambulance, the
 * casualty, the vehicle. Each exists in several wordings and the order is drawn
 * fresh per flight, so the same aircraft over the same scene twice does not
 * produce two identical-looking reports — what the operator is watching is an
 * assessment being presented, not a slide being replayed.
 *
 * The same table exists in `mvp_mission_bebop/telemetry/announcer.py` for the
 * mission process to narrate on its own. This copy is the authority whenever
 * the station is driving: it decides the order, shows the card and hands the
 * copilot the exact sentence to read, so the line in the operator's ear and the
 * line on screen are the same string rather than two renderings of one idea.
 */

export type FindingTopic = 'police' | 'samu' | 'victim' | 'vehicle';

export interface Finding {
  topic: FindingTopic;
  /** The line as the card shows it. */
  card: string;
  /** The same line, ordinal-prefixed, as the copilot reads it. */
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

const ORDINALS = ['Primeiro', 'Segundo', 'Terceiro', 'Quarto'] as const;

export const REPORT_INTRO = 'Inspeção do sinistro concluída. Apresentando laudo preliminar.';
export const REPORT_OUTRO = 'Relatório pericial emitido e pronto para exportação.';

/** The label under each card, so the topic is legible without reading the wording. */
export const TOPIC_LABEL: Record<FindingTopic, string> = {
  police: 'Autoridade policial',
  samu: 'Socorro médico',
  victim: 'Estado do acidentado',
  vehicle: 'Integridade do veículo',
};

function pick<T>(items: readonly T[]): T {
  return items[Math.floor(Math.random() * items.length)];
}

function shuffle<T>(items: readonly T[]): T[] {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/**
 * Lower the first letter so the line reads on after its ordinal.
 *
 * An acronym is left standing: "SAMU dispensado" must not become "sAMU".
 */
function decapitalise(line: string): string {
  if (line.length > 1 && line[1] === line[1].toUpperCase() && line[1] !== line[1].toLowerCase()) {
    return line;
  }
  return line[0].toLowerCase() + line.slice(1);
}

/** Draw one report: a wording per topic, in a fresh order. */
export function buildForensicReport(): Finding[] {
  return shuffle(TOPICS).map((topic, index) => {
    const card = pick(WORDINGS[topic]);
    return {
      topic,
      card,
      speech: `${ORDINALS[index]}: ${decapitalise(card)}.`,
    };
  });
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
