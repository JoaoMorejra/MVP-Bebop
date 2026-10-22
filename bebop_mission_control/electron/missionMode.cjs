/**
 * Mission profile of a launch document, as the station sends it to `mission.py`.
 *
 * The profile travels only inside the document (`--params-json` on a cold
 * start, the `go` of a standby): never as `--mode`. A standby is prepared at
 * the saved document before the click, and a `--mode` fixed at that spawn
 * would win over the profile of the document sent with the go
 * (`mission_mode.apply_mission_mode`). The station validates the value here so
 * a corrupt document is refused on the ground instead of being flown as a
 * default, and the mission echoes the profile it resolved (`mission.mode`) so
 * the station can confirm it before takeoff.
 */

const MISSION_MODES = Object.freeze(['simple', 'advanced']);

/**
 * Refusal of the removed profile. The renderer migrates stored documents before
 * they reach here (`launchDocument.ts:migrateLegacyMode`), so only an external
 * document still names it.
 */
const INTERMEDIATE_REMOVED = 'Modo Intermediário removido: use Avançado sem RTL.';

/**
 * @param {unknown} doc Launch document (parsed JSON object).
 * @returns {{ok: true, doc: object, mode: string, defaulted: boolean} | {ok: false, error: string}}
 *   `defaulted` is true when the document carried no profile and the full
 *   mission was selected; the caller logs it. An unknown value is refused.
 */
function normalizeLaunchMode(doc) {
  const source = doc && typeof doc === 'object' && !Array.isArray(doc) ? doc : {};
  const raw = source.mission_mode;
  if (raw === undefined || raw === null || (typeof raw === 'string' && raw.trim() === '')) {
    return { ok: true, doc: { ...source, mission_mode: 'advanced' }, mode: 'advanced', defaulted: true };
  }
  const token = typeof raw === 'string' ? raw.trim().toLowerCase() : '';
  if (token === 'intermediate') return { ok: false, error: INTERMEDIATE_REMOVED };
  if (!MISSION_MODES.includes(token)) {
    return {
      ok: false,
      error: `mission_mode inválido (${JSON.stringify(raw)}): use ${MISSION_MODES.join(', ')}. Lançamento bloqueado.`,
    };
  }
  return { ok: true, doc: { ...source, mission_mode: token }, mode: token, defaulted: false };
}

module.exports = { INTERMEDIATE_REMOVED, MISSION_MODES, normalizeLaunchMode };
