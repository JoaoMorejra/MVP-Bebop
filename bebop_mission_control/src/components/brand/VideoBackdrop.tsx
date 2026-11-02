import React, { useEffect, useRef, useState } from 'react';
import { ParticleField } from './ParticleField';

interface VideoBackdropProps {
  /** Deepens the overlay while a panel owns the operator's attention. */
  subdued?: boolean;
  /** Fired when the film cannot be decoded and the generated field takes over. */
  onFallback?: (usingFallback: boolean) => void;
}

/**
 * The INICIO loop: the Tech for Humans particle wave with the Parrot Bebop 2 in
 * white line art, its rotors stirring the surface. 20 s, 1920x1080, silent, and
 * frame 599 meets frame 0, so `loop` has no seam. Made with HyperFrames; the
 * source project, to edit and render again, is in
 * ~/hyperframes-projects/t4h-inicio-loop (tagged inicio-loop-r9).
 */
const SOURCE = '/assets/t4h-inicio-loop.mp4';
/** Its first frame, shown until the film decodes so the screen never opens black. */
const POSTER = '/assets/t4h-inicio-loop-poster.jpg';

/**
 * The pre-flight backdrop.
 *
 * The INICIO film, full bleed and silent. It carries its own legibility: its
 * top and bottom fall to the abyss and its centre stays calm behind the dial.
 * The wash here is only what the dial and the top bar still need over its
 * brightest frame, light enough to keep the brand's green; it deepens while a
 * panel owns the operator's attention.
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
        poster={POSTER}
        onError={() => setFailed(true)}
        className="h-full w-full object-cover"
      />

      {/* A light floor that deepens under a panel, the band behind the top
          bar, and a soft calm behind the dial. */}
      <div
        className="absolute inset-0 transition-colors duration-500"
        style={{ background: subdued ? 'rgba(0,12,22,0.78)' : 'rgba(0,12,22,0.12)' }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(180deg, rgba(0,19,31,0.55) 0%, rgba(0,19,31,0) 14%, rgba(0,19,31,0) 82%, rgba(0,19,31,0.4) 100%)',
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(30% 36% at 50% 47%, rgba(0,12,22,0.28) 0%, rgba(0,12,22,0.06) 62%, transparent 85%)',
        }}
      />
    </div>
  );
};
