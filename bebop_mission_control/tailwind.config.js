/** @type {import('tailwindcss').Config} */

/**
 * Tech for Humans — ground control station.
 *
 * The palette is sampled from the brand field (`T4H_bg4_logo.jpg`): a particle
 * mesh that runs from a mint crest down into deep navy-teal. Those are the two
 * poles the interface is built on, with the teal midtones kept as real
 * structural surfaces rather than collapsing everything to black plus an accent.
 *
 * Abort and fault sit on the warm side of the wheel on purpose. On a green
 * instrument panel a red-orange is the only thing that cannot be mistaken for
 * "running normally" at a glance.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Hull tones, deepest first. Panels rise off the field by tone alone.
        abyss: '#00131F',
        hull: {
          deep: '#001A2F',
          DEFAULT: '#04233A',
          deck: '#072D46',
          raise: '#0B3752',
        },
        strut: {
          soft: '#0D3349',
          DEFAULT: '#14455D',
          bright: '#1E5C75',
        },
        // Live, and only live. Sampled from the crest of the brand wave.
        mint: {
          DEFAULT: '#01D5A3',
          bright: '#5CF2CE',
          deep: '#00A87F',
        },
        // The brand's mid-teal. Structure and inactive brand states.
        kelp: {
          DEFAULT: '#12695E',
          deep: '#0B4A48',
        },
        // Cool white, matched to the wordmark.
        frost: {
          DEFAULT: '#E8F2F0',
          dim: '#BBD0CF',
        },
        haze: {
          DEFAULT: '#7C99A4',
          deep: '#4C6975',
        },
        // Abort and fault. Never decorative.
        ember: {
          DEFAULT: '#FF6A45',
          deep: '#C4462A',
        },
        amber: {
          DEFAULT: '#FFC24B',
          deep: '#C28E22',
        },
        // Electric cyan from the brand field. The bench's colour: a rehearsal
        // with the motors inert must never read as the mint of a live flight.
        cyan: {
          DEFAULT: '#00E5FF',
          bright: '#7AF3FF',
          deep: '#00A8C2',
        },
      },
      fontFamily: {
        sans: ['Barlow', 'Helvetica Neue', 'Arial', 'system-ui', 'sans-serif'],
        cond: ['Barlow Semi Condensed', 'Barlow', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['Azeret Mono', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        '3xs': ['0.625rem', { lineHeight: '0.875rem' }],
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        xs: ['0.75rem', { lineHeight: '1.1rem' }],
        sm: ['0.8125rem', { lineHeight: '1.2rem' }],
        base: ['0.9375rem', { lineHeight: '1.45rem' }],
        lg: ['1.0625rem', { lineHeight: '1.45rem' }],
        xl: ['1.375rem', { lineHeight: '1.6rem' }],
        '2xl': ['1.875rem', { lineHeight: '2rem' }],
        '3xl': ['2.75rem', { lineHeight: '2.8rem' }],
        '4xl': ['4rem', { lineHeight: '3.9rem' }],
        '6xl': ['7.5rem', { lineHeight: '7rem' }],
      },
      borderRadius: {
        bezel: '4px',
        panel: '10px',
      },
      transitionTimingFunction: {
        instrument: 'cubic-bezier(0.22, 0.61, 0.36, 1)',
        settle: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      boxShadow: {
        // Depth comes from tone; this is a contact glow for live elements only.
        live: '0 0 0 1px rgba(1, 213, 163, 0.35), 0 0 24px -6px rgba(1, 213, 163, 0.45)',
        abort: '0 0 0 1px rgba(255, 106, 69, 0.4), 0 0 28px -6px rgba(255, 106, 69, 0.5)',
        bench: '0 0 0 1px rgba(0, 229, 255, 0.4), 0 0 24px -6px rgba(0, 229, 255, 0.5)',
      },
    },
  },
  plugins: [],
};
