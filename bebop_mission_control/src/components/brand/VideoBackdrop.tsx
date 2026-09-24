import React, { useEffect, useRef, useState } from 'react';
import { ParticleField } from './ParticleField';

interface VideoBackdropProps {
  /** Deepens the overlay while a panel owns the operator's attention. */
  subdued?: boolean;
  /**
   * Fired when the film cannot be decoded and the generated field takes over.
   * The caller uses it to put the wordmark back: the film carries the mark
   * itself, the fallback does not.
   */
  onFallback?: (usingFallback: boolean) => void;
}

const SOURCE = '/assets/Tech4aiMVPvideo.mp4';

/**
 * The pre-flight backdrop.
 *
 * The Tech for Humans film, full bleed and silent, under a tactical wash that
 * exists for one reason: every instrument and control on this screen has to
 * stay legible over whatever frame happens to be behind it. The video is
 * decoration; the dial is not, and the overlay is tuned so that stays true at
 * the brightest moment of the loop rather than the average one.
 *
 * `muted` is what makes `autoPlay` legal — the file carries an audio track, and
 * a ground station that makes noise on its own is a ground station an operator
 * turns off. It is set on the element and asserted again on the DOM node,
 * because a browser that ignores the attribute will still honour the property
 * and refuse playback either way.
 *
 * If the file cannot be decoded the generated particle field takes over. A
 * blank screen behind the launch control is not an acceptable failure.
 */
export const VideoBackdrop: React.FC<VideoBackdropProps> = ({ subdued, onFallback }) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    onFallback?.(failed);
  }, [failed, onFallback]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    // Belt and braces: the property, not just the attribute, is what autoplay
    // policies actually read.
    video.muted = true;
    video.defaultMuted = true;
    video.volume = 0;

    const play = () => {
      void video.play().catch(() => {
        // A refused autoplay is not a failure worth swapping the backdrop for:
        // the first frame still renders, and the next user gesture starts it.
        });
    };

    play();
    const onVisible = () => {
      if (document.visibilityState === 'visible') play();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);

  if (failed) return <ParticleField subdued={subdued} />;

  return (
    <div aria-hidden className="absolute inset-0 overflow-hidden bg-abyss">
      <video
        ref={videoRef}
        src={SOURCE}
        autoPlay
        loop
        muted
        playsInline
        preload="auto"
        disablePictureInPicture
        onError={() => setFailed(true)}
        className="h-full w-full object-cover"
      />

      {/* The tactical wash, in three parts: a flat floor that guarantees a
          contrast baseline, a vertical gradient that anchors the header and the
          tab rail, and a centre vignette that carves out the dial. */}
      <div
        className="absolute inset-0 transition-colors duration-500"
        style={{ background: subdued ? 'rgba(0,12,22,0.8)' : 'rgba(0,12,22,0.5)' }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(180deg, rgba(0,19,31,0.78) 0%, rgba(0,19,31,0.08) 26%, rgba(0,19,31,0.14) 62%, rgba(0,19,31,0.88) 100%)',
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(42% 48% at 50% 47%, rgba(0,12,22,0.42) 0%, rgba(0,12,22,0.08) 58%, transparent 82%)',
        }}
      />
      {/* A whisper of the brand mesh, so the film still reads as this product's
          and not as stock footage behind a UI. */}
      <div
        className="absolute inset-0 opacity-[0.35] mesh-fine"
        style={{ mixBlendMode: 'overlay' }}
      />
    </div>
  );
};
