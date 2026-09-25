import { describe, expect, it } from 'vitest';
import { feedState } from './OpticalFeed';

const up = { bridgeUp: true, failed: false, live: true, connected: true };

describe('feedState', () => {
  it('shows the stream only while frames are arriving', () => {
    expect(feedState(up)).toBe('image');
    expect(feedState({ ...up, connected: false })).toBe('image');
  });

  it('replaces a frozen last frame with a stall notice when the bridge answers without frames', () => {
    expect(feedState({ ...up, live: false })).toBe('stalled');
  });

  it('blames the bridge when it does not answer or the image connection failed', () => {
    expect(feedState({ ...up, bridgeUp: false, live: false })).toBe('no-bridge');
    expect(feedState({ ...up, failed: true })).toBe('no-bridge');
  });

  it('reports the missing link first, before the bridge or the camera', () => {
    expect(feedState({ ...up, connected: false, live: false })).toBe('no-link');
    expect(feedState({ bridgeUp: false, failed: true, live: false, connected: false })).toBe('no-link');
  });
});
