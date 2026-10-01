import { describe, expect, it } from 'vitest';
import { flyingStateLabel } from './flightState';

describe('flyingStateLabel', () => {
  it('shows the state the aircraft reported', () => {
    expect(flyingStateLabel('hovering', true)).toBe('hovering');
    expect(flyingStateLabel('landed', false)).toBe('landed');
  });

  it('never claims "em solo" for a state nobody reported', () => {
    // A connected link with no FlyingStateChanged is not a landed aircraft.
    expect(flyingStateLabel('unknown', true)).toBe('estado desconhecido');
    expect(flyingStateLabel(undefined, true)).toBe('estado desconhecido');
    expect(flyingStateLabel('', true)).toBe('estado desconhecido');
  });

  it('shows a dash with no link', () => {
    expect(flyingStateLabel(undefined, false)).toBe('—');
    expect(flyingStateLabel('unknown', false)).toBe('—');
  });
});
