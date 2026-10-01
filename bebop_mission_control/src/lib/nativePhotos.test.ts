import { describe, expect, it, vi } from 'vitest';
import { MEDIA_FETCH_ARGS, createNativePhotoFetch } from '../../electron/nativePhotos.cjs';

const touchdown = { kind: 'milestone', key: 'mission.touchdown', payload: { at_base: true } };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe('createNativePhotoFetch', () => {
  it('fetches once the mission that reported a touchdown has exited', async () => {
    const run = vi.fn(async () => ({ code: 0, stdout: '{"fetched": [{"local_path": "native/a.jpg"}]}\n' }));
    const onResult = vi.fn();
    const fetcher = createNativePhotoFetch({ run, onResult, log: () => {} });
    fetcher.observe(touchdown);
    expect(run).not.toHaveBeenCalled();
    await fetcher.missionExited();
    expect(run).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith({ fetched: [{ local_path: 'native/a.jpg' }] });
  });

  it('does nothing for a mission that never touched down', async () => {
    const run = vi.fn(async () => ({ code: 0, stdout: '{"fetched": []}' }));
    const fetcher = createNativePhotoFetch({ run, onResult: () => {}, log: () => {} });
    fetcher.observe({ kind: 'milestone', key: 'mission.capture_done', payload: {} });
    fetcher.observe({ kind: 'alert', key: 'mission.touchdown', payload: {} });
    await fetcher.missionExited();
    expect(run).not.toHaveBeenCalled();
  });

  it('forgets the touchdown after one fetch', async () => {
    const run = vi.fn(async () => ({ code: 0, stdout: '{"fetched": []}' }));
    const fetcher = createNativePhotoFetch({ run, onResult: () => {}, log: () => {} });
    fetcher.observe(touchdown);
    await fetcher.missionExited();
    await fetcher.missionExited();
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('never runs two fetches at once', async () => {
    const pending = deferred<{ code: number; stdout: string }>();
    const run = vi.fn(() => pending.promise);
    const fetcher = createNativePhotoFetch({ run, onResult: () => {}, log: () => {} });
    fetcher.observe(touchdown);
    const first = fetcher.missionExited();
    fetcher.observe(touchdown);
    const second = fetcher.missionExited();
    pending.resolve({ code: 0, stdout: '{"fetched": []}' });
    await Promise.all([first, second]);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('logs a failure and an unreadable report without throwing', async () => {
    const log = vi.fn();
    const onResult = vi.fn();
    const run = vi
      .fn()
      .mockResolvedValueOnce({ code: 1, stdout: '{"fetched": [], "error": "No route to host"}' })
      .mockResolvedValueOnce({ code: 0, stdout: 'garbage' })
      .mockRejectedValueOnce(new Error('spawn failed'));
    const fetcher = createNativePhotoFetch({ run, onResult, log });
    for (let i = 0; i < 3; i += 1) {
      fetcher.observe(touchdown);
      await fetcher.missionExited();
    }
    expect(onResult).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith({ fetched: [], error: 'No route to host' });
    expect(log).toHaveBeenCalledTimes(3);
  });

  it('runs the media fetch script against the mission output directory', () => {
    expect(MEDIA_FETCH_ARGS('/s/media_fetch.py', '/m/out')).toEqual([
      'python3',
      '/s/media_fetch.py',
      '--output-dir',
      '/m/out',
    ]);
  });
});
