/**
 * Pure state of the top bar tray: the ROS 2 indicator, the topic names, the
 * volume icon and the order of the network list. No React, no bridge.
 */
import type { VoiceLevel, WifiNetwork } from '../types/bmg';
import { isBebopSsid } from './wifi';

export type Ros2Tone = 'off' | 'busy' | 'ready';

/**
 * Tone of the ROS 2 glyph: `ready` only while the driver is up, every
 * essential topic flows and no bring-up is running; `busy` while one runs or
 * the driver is up without the topics; `off` otherwise.
 */
export function ros2Tone(input: { driverRunning: boolean; busy: boolean; ready: boolean }): Ros2Tone {
  if (input.busy) return 'busy';
  if (!input.driverRunning) return 'off';
  return input.ready ? 'ready' : 'busy';
}

const BEBOP_PREFIX = '/bebop/';

/** A driver topic without its `/bebop/` namespace; any other topic as it is. */
export function topicLabel(topic: string): string {
  return topic.startsWith(BEBOP_PREFIX) ? topic.slice(BEBOP_PREFIX.length) : topic;
}

/** Icon level of the copilot's voice: 0 muted or silent, then thirds of the range. */
export function volumeLevel(level: VoiceLevel): 0 | 1 | 2 | 3 {
  if (level.muted || !(level.volume > 0)) return 0;
  if (level.volume <= 0.33) return 1;
  if (level.volume <= 0.66) return 2;
  return 3;
}

/** The aircraft's networks first; the relative order of the rest is kept. */
export function sortNetworks(networks: readonly WifiNetwork[]): WifiNetwork[] {
  const bebop = (network: WifiNetwork) => network.isBebop || isBebopSsid(network.ssid);
  return [...networks.filter(bebop), ...networks.filter((network) => !bebop(network))];
}

/** nmcli signal percentage to the four-level scale used across the station. */
export function wifiLevel(pct: number): 0 | 1 | 2 | 3 | 4 {
  if (!(pct > 0)) return 0;
  if (pct >= 75) return 4;
  if (pct >= 55) return 3;
  return pct >= 35 ? 2 : 1;
}

/** Charge at or below which the battery is drawn in the alarm colour. */
export const BATTERY_ALERT_PCT = 20;

/** Whether a measured charge is in the alarm band; an unmeasured one never is. */
export function batteryAlert(known: boolean, pct: number): boolean {
  return known && Number.isFinite(pct) && pct <= BATTERY_ALERT_PCT;
}

/** The ROS 2 tile's second line: what the driver is doing, in a few words. */
export function ros2Subtitle(tone: Ros2Tone, topics: ReadonlyArray<{ receiving: boolean }> | null): string {
  if (tone === 'off') return 'Parado';
  if (tone === 'busy') return 'Iniciando';
  const total = topics?.length ?? 0;
  const flowing = topics?.filter((topic) => topic.receiving).length ?? 0;
  return `${flowing} de ${total} tópicos`;
}

/**
 * Scramble-reveal of a topic name while its node comes up.
 *
 * Glyph set and timing follow the kinetics "scramble reveal": a 35 ms frame,
 * 26 frames, each character 1.4 frames behind its left neighbour and locked
 * once it is past 55 % of the run.
 */
export const SCRAMBLE_CHARS = '!<>-_/[]{}=+*^?#________';
export const SCRAMBLE_FRAME_MS = 35;
const SCRAMBLE_FRAMES = 26;
const SCRAMBLE_STAGGER = 1.4;
const SCRAMBLE_LOCK = SCRAMBLE_FRAMES * 0.55;

/**
 * `text` at `frame` of its reveal; `null` scrambles every character. Spaces
 * stay spaces so the word shapes hold while the glyphs churn.
 */
export function scrambleFrame(text: string, frame: number | null, random: () => number = Math.random): string {
  let out = '';
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    const locked = frame !== null && frame - i * SCRAMBLE_STAGGER > SCRAMBLE_LOCK;
    out += char === ' ' || locked ? char : SCRAMBLE_CHARS[Math.floor(random() * SCRAMBLE_CHARS.length)] ?? '_';
  }
  return out;
}

/** Frames until every character of `text` has locked. */
export function scrambleRevealFrames(text: string): number {
  return Math.floor(SCRAMBLE_LOCK + Math.max(0, text.length - 1) * SCRAMBLE_STAGGER) + 1;
}
