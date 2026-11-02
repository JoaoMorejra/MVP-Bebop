import { describe, expect, it } from 'vitest';
import type { WifiNetwork } from '../types/bmg';
import {
  SCRAMBLE_CHARS,
  batteryAlert,
  ros2Subtitle,
  ros2Tone,
  scrambleFrame,
  scrambleRevealFrames,
  sortNetworks,
  topicLabel,
  volumeLevel,
  wifiLevel,
} from './trayState';

describe('battery, wifi and ros2 labels', () => {
  it('batteryAlert is on at 20 percent and below, never for an unmeasured charge', () => {
    expect(batteryAlert(true, 20)).toBe(true);
    expect(batteryAlert(true, 5)).toBe(true);
    expect(batteryAlert(true, 21)).toBe(false);
    expect(batteryAlert(false, 0)).toBe(false);
    expect(batteryAlert(true, Number.NaN)).toBe(false);
  });

  it('wifiLevel maps the nmcli percentage onto four levels', () => {
    expect([0, 1, 34, 35, 54, 55, 74, 75, 100].map(wifiLevel)).toEqual([0, 1, 1, 2, 2, 3, 3, 4, 4]);
    expect(wifiLevel(Number.NaN)).toBe(0);
  });

  it('ros2Subtitle names the driver state and counts the flowing topics when ready', () => {
    expect(ros2Subtitle('off', null)).toBe('Parado');
    expect(ros2Subtitle('busy', null)).toBe('Iniciando');
    const topics = [{ receiving: true }, { receiving: true }, { receiving: false }];
    expect(ros2Subtitle('ready', topics)).toBe('2 de 3 tópicos');
  });
});

describe('scrambleFrame', () => {
  const zero = () => 0;

  it('scrambles every character but spaces when frame is null', () => {
    const out = scrambleFrame('cmd vel', null, zero);
    expect(out).toHaveLength(7);
    expect(out[3]).toBe(' ');
    expect([...out.replace(' ', '')].every((c) => SCRAMBLE_CHARS.includes(c))).toBe(true);
  });

  it('locks characters left to right as the frame advances', () => {
    const text = 'camera/image_raw';
    const early = scrambleFrame(text, 16, zero);
    expect(early[0]).toBe('c');
    expect(early[text.length - 1]).not.toBe('w');
  });

  it('returns the text once every character has locked', () => {
    const text = 'states/battery';
    expect(scrambleFrame(text, scrambleRevealFrames(text), zero)).toBe(text);
    expect(scrambleFrame(text, scrambleRevealFrames(text) - 1, zero)).not.toBe(text);
  });
});

const net = (ssid: string, isBebop = false): WifiNetwork => ({ ssid, signal: 50, active: false, isBebop });

describe('trayState', () => {
  it('ros2Tone is off when the driver is down, busy while starting, ready when topics flow', () => {
    expect(ros2Tone({ driverRunning: false, busy: false, ready: false })).toBe('off');
    expect(ros2Tone({ driverRunning: false, busy: true, ready: false })).toBe('busy');
    expect(ros2Tone({ driverRunning: true, busy: false, ready: false })).toBe('busy');
    expect(ros2Tone({ driverRunning: true, busy: false, ready: true })).toBe('ready');
    expect(ros2Tone({ driverRunning: true, busy: true, ready: true })).toBe('busy');
  });

  it('topicLabel strips the /bebop/ prefix and keeps nested paths', () => {
    expect(topicLabel('/bebop/camera/image_raw')).toBe('camera/image_raw');
    expect(topicLabel('/bebop/odom')).toBe('odom');
    expect(topicLabel('/other/topic')).toBe('/other/topic');
  });

  it('volumeLevel is 0 when muted even with a positive volume', () => {
    expect(volumeLevel({ volume: 0.8, muted: true })).toBe(0);
  });

  it('volumeLevel boundaries', () => {
    const at = (volume: number) => volumeLevel({ volume, muted: false });
    expect(at(0)).toBe(0);
    expect(at(0.01)).toBe(1);
    expect(at(0.33)).toBe(1);
    expect(at(0.34)).toBe(2);
    expect(at(0.66)).toBe(2);
    expect(at(0.67)).toBe(3);
    expect(at(1)).toBe(3);
  });

  it('sortNetworks puts Bebop networks first and keeps the rest stable', () => {
    const list = [net('Casa'), net('Bebop2-123', true), net('Lab'), net('Bebop-9', true), net('Rua')];
    expect(sortNetworks(list).map((n) => n.ssid)).toEqual(['Bebop2-123', 'Bebop-9', 'Casa', 'Lab', 'Rua']);
    expect(list[0].ssid).toBe('Casa');
  });
});
