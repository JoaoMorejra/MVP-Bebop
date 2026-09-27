import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { readSiteAnchorFile } from '../../electron/siteAnchor.cjs';

const dir = mkdtempSync(join(tmpdir(), 'bmg-site-'));
const write = (name: string, body: unknown) => {
  const file = join(dir, name);
  writeFileSync(file, typeof body === 'string' ? body : JSON.stringify(body));
  return file;
};

describe('readSiteAnchorFile', () => {
  it('reads the shipped demonstration site', () => {
    const site = readSiteAnchorFile(resolve(__dirname, '../../config/site-anchor.json'));
    expect(site).toMatchObject({ name: 'Transamerica Expo Center' });
    expect(site!.latitude).toBeCloseTo(-23.64889, 4);
    expect(site!.longitude).toBeCloseTo(-46.71871, 4);
  });

  it('sits inside the venue as OpenStreetMap bounds it', () => {
    const shipped = JSON.parse(readFileSync(resolve(__dirname, '../../config/site-anchor.json'), 'utf-8'));
    expect(shipped.latitude).toBeGreaterThan(-23.6507398);
    expect(shipped.latitude).toBeLessThan(-23.6469327);
    expect(shipped.longitude).toBeGreaterThan(-46.720218);
    expect(shipped.longitude).toBeLessThan(-46.7172589);
  });

  it('is off unless enabled is exactly true', () => {
    expect(readSiteAnchorFile(write('off.json', { enabled: false, latitude: 1, longitude: 2 }))).toBeNull();
    expect(readSiteAnchorFile(write('str.json', { enabled: 'yes', latitude: 1, longitude: 2 }))).toBeNull();
  });

  it('refuses coordinates that are not a position', () => {
    expect(readSiteAnchorFile(write('nan.json', { enabled: true, latitude: 'x', longitude: 2 }))).toBeNull();
    expect(readSiteAnchorFile(write('range.json', { enabled: true, latitude: 95, longitude: 2 }))).toBeNull();
    expect(readSiteAnchorFile(write('bad.json', '{not json'))).toBeNull();
    expect(readSiteAnchorFile(join(dir, 'missing.json'))).toBeNull();
  });

  it('defaults the name and accuracy', () => {
    expect(readSiteAnchorFile(write('bare.json', { enabled: true, latitude: -10, longitude: -40 }))).toEqual({
      latitude: -10,
      longitude: -40,
      accuracyM: 25,
      name: 'local configurado',
    });
  });
});
