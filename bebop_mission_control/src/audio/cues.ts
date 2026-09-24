/**
 * Audible confirmation for state the operator may not be looking at.
 *
 * Cues are short, pitched, and distinct from each other, so a phase advance is
 * never mistaken for a capture. Synthesised rather than sampled so the app
 * carries no audio assets and works with the window muted at the OS level.
 */

type Cue =
  | 'tick'
  | 'commit'
  | 'phase'
  | 'shutter'
  | 'abort'
  | 'fault'
  | 'link'
  | 'complete';

class CueEngine {
  private ctx: AudioContext | null = null;
  private enabled = true;

  setEnabled(on: boolean) {
    this.enabled = on;
  }

  isEnabled() {
    return this.enabled;
  }

  private context(): AudioContext | null {
    if (!this.enabled) return null;
    if (typeof window === 'undefined') return null;
    if (!this.ctx) {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return null;
      try {
        this.ctx = new Ctor();
      } catch {
        return null;
      }
    }
    if (this.ctx.state === 'suspended') void this.ctx.resume();
    return this.ctx;
  }

  private tone(
    freq: number,
    startOffset: number,
    duration: number,
    peak: number,
    type: OscillatorType = 'sine',
    glideTo?: number
  ) {
    const ctx = this.context();
    if (!ctx) return;
    const t0 = ctx.currentTime + startOffset;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (glideTo !== undefined) {
      osc.frequency.exponentialRampToValueAtTime(Math.max(glideTo, 1), t0 + duration);
    }
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(peak, t0 + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t0);
    osc.stop(t0 + duration + 0.03);
  }

  private noise(startOffset: number, duration: number, peak: number) {
    const ctx = this.context();
    if (!ctx) return;
    const frames = Math.floor(ctx.sampleRate * duration);
    const buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < frames; i += 1) {
      data[i] = (Math.random() * 2 - 1) * (1 - i / frames) ** 2;
    }
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(peak, ctx.currentTime + startOffset);
    const filter = ctx.createBiquadFilter();
    filter.type = 'highpass';
    filter.frequency.value = 1800;
    src.connect(filter).connect(gain).connect(ctx.destination);
    src.start(ctx.currentTime + startOffset);
  }

  play(cue: Cue, arg?: number) {
    if (!this.enabled) return;
    switch (cue) {
      // Countdown. The final three seconds rise so the last one is unmistakable.
      case 'tick': {
        const remaining = arg ?? 9;
        const base = remaining <= 3 ? 880 : 523.25;
        this.tone(base, 0, remaining <= 3 ? 0.16 : 0.08, 0.1, 'triangle');
        break;
      }
      case 'commit':
        this.tone(196, 0, 0.5, 0.14, 'sawtooth', 392);
        this.tone(392, 0.06, 0.44, 0.08, 'sine', 784);
        break;
      case 'phase':
        this.tone(659.25, 0, 0.1, 0.09, 'sine');
        this.tone(987.77, 0.07, 0.14, 0.07, 'sine');
        break;
      case 'shutter':
        this.noise(0, 0.05, 0.22);
        this.noise(0.075, 0.07, 0.16);
        break;
      case 'abort':
        this.tone(440, 0, 0.2, 0.16, 'square');
        this.tone(330, 0.2, 0.2, 0.16, 'square');
        this.tone(440, 0.4, 0.2, 0.16, 'square');
        break;
      case 'fault':
        this.tone(220, 0, 0.34, 0.13, 'square', 146);
        break;
      case 'link':
        this.tone(587.33, 0, 0.09, 0.07, 'sine');
        this.tone(880, 0.08, 0.12, 0.06, 'sine');
        break;
      case 'complete':
        this.tone(523.25, 0, 0.13, 0.08, 'sine');
        this.tone(659.25, 0.11, 0.13, 0.08, 'sine');
        this.tone(783.99, 0.22, 0.26, 0.08, 'sine');
        break;
    }
  }
}

export const cues = new CueEngine();
