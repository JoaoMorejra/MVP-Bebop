import { describe, expect, it } from 'vitest';
import { allMissionPhrases } from './copilotPhrases';
import { allFindingSentences, buildForensicReport } from './forensics';

describe('sentences known before the flight (3.9)', () => {
  it('renders every pool with the mission parameters, leaving no placeholder', () => {
    const phrases = allMissionPhrases({ target_altitude_m: 2.3, cruise_mps: 0.35 });
    expect(phrases).toContain('Decolagem autorizada. Subindo para 2,3 metros.');
    expect(phrases).toContain('Varredura iniciada a 0,35 metro por segundo.');
    expect(phrases).toContain('Pouso comandado. Confirmação de toque indisponível. Verifique visualmente.');
    for (const line of phrases) expect(line).not.toMatch(/\{[a-z_]+\}/);
    expect(new Set(phrases).size).toBe(phrases.length);
  });

  it('leaves out the target call, whose range is the flight estimate', () => {
    const phrases = allMissionPhrases({ target_altitude_m: 2.3 });
    expect(phrases.some((line) => /Sinistro detectado|Alvo identificado/.test(line))).toBe(false);
  });

  it('enumerates every sentence the forensic report can say (D1)', () => {
    const sentences = allFindingSentences();
    expect(sentences.length).toBe(304);
    const report = buildForensicReport({ storage: null, random: () => 0.3 });
    for (const finding of report) expect(sentences).toContain(finding.speech);
  });
});
