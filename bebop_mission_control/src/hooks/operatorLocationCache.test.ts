// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { LOCAL_CACHE_MAX_AGE_MS, readLocalCache } from './useOperatorLocation';

const KEY = 'bmg.operator-location.v1';
const put = (entry: object) => window.localStorage.setItem(KEY, JSON.stringify(entry));

afterEach(() => window.localStorage.clear());

describe('readLocalCache', () => {
  const now = 1_790_000_000_000;

  it('offers a recent position, as a cache', () => {
    put({ latitude: -22.42, longitude: -45.45, accuracyM: 5000, at: now - 10 * 60 * 1000, source: 'host' });
    expect(readLocalCache(now)).toMatchObject({ source: 'cache', latitude: -22.42 });
  });

  it('refuses a position older than the limit, which may be from another site', () => {
    put({ latitude: -22.42, longitude: -45.45, accuracyM: 5000, at: now - LOCAL_CACHE_MAX_AGE_MS - 1, source: 'host' });
    expect(readLocalCache(now)).toBeNull();
  });

  it('refuses an entry with no timestamp', () => {
    put({ latitude: -22.42, longitude: -45.45, accuracyM: 5000, source: 'host' });
    expect(readLocalCache(now)).toBeNull();
  });

  it('keeps a configured site as the site', () => {
    put({ latitude: -23.6489, longitude: -46.7187, accuracyM: 25, at: now - 1000, source: 'site' });
    expect(readLocalCache(now)?.source).toBe('site');
  });
});
