import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Gauge, Video, VideoOff } from 'lucide-react';
import type { TelemetryView } from '../../types/mission';
import { cn, duration } from '../../lib/format';

interface OpticalFeedProps {
  fps: number;
  /** The MJPEG bridge answered its status endpoint. */
  bridgeUp: boolean;
  /** A frame arrived recently, as measured by the bridge itself. */
  live: boolean;
  width: number;
  height: number;
  telemetry: TelemetryView;
  running: boolean;
  flash: boolean;
  gimbalTilt: number | null;
  /** Angle the gimbal bridge confirmed publishing, or null before the first command. */
  cameraTilt: number | null;
  /** False in a browser session: the slider says so rather than pretending to command. */
  cameraAvailable: boolean;
  onCameraTilt: (degrees: number) => void;
}

/** The airframe's usable tilt span, as the command bridge clamps it. */
const TILT_MIN = -80;
const TILT_MAX = 0;

const STREAM_URL = 'http://127.0.0.1:9090/stream';
const RECONNECT_DELAY_MS = 1500;

/** Standard broadcast heights, so the operator reads a format rather than a pair of numbers. */
function formatName(width: number, height: number): string {
  if (!width || !height) return '—';
  if (height >= 2160) return '4K';
  if (height >= 1080) return '1080p';
  if (height >= 720) return '720p';
  if (height >= 480) return '480p';
  return `${width}×${height}`;
}

/** One HUD cluster. Painted over video, so everything carries its own legibility. */
const HudChip: React.FC<{
  icon?: React.ReactNode;
  children: React.ReactNode;
  tone?: 'default' | 'mint' | 'amber' | 'ember';
}> = ({ icon, children, tone = 'default' }) => (
  <span
    className={cn(
      'flex items-center gap-1.5 rounded-full border border-frost/10 bg-abyss/55 px-2.5 py-1 backdrop-blur-sm',
      tone === 'mint' && 'border-mint/30',
      tone === 'amber' && 'border-amber/35',
      tone === 'ember' && 'border-ember/40'
    )}
  >
    {icon ? (
      <span
        className={cn(
          'shrink-0',
          tone === 'mint' && 'text-mint',
          tone === 'amber' && 'text-amber',
          tone === 'ember' && 'text-ember',
          tone === 'default' && 'text-haze'
        )}
      >
        {icon}
      </span>
    ) : null}
    <span
      className={cn(
        'hud-legible tnum font-mono text-2xs',
        tone === 'mint' && 'text-mint',
        tone === 'amber' && 'text-amber',
        tone === 'ember' && 'text-ember',
        tone === 'default' && 'text-frost'
      )}
    >
      {children}
    </span>
  </span>
);

/** What the feed panel shows: the stream, or why there is none. */
export type FeedState = 'image' | 'no-link' | 'no-bridge' | 'stalled';

/**
 * Decide the feed panel's content.
 *
 * `live` is required for the image. An `<img>` on an MJPEG stream keeps its
 * last decoded frame on screen for as long as the connection stays open and
 * raises no error, so a camera that stops sending while the bridge answers
 * used to leave a frozen frame that read as live video. The reason given
 * follows the chain from the aircraft inward: no link first, then a bridge
 * that does not answer, then a bridge that answers with no frames.
 */
export function feedState(signal: {
  bridgeUp: boolean;
  failed: boolean;
  live: boolean;
  connected: boolean;
}): FeedState {
  if (signal.bridgeUp && !signal.failed && signal.live) return 'image';
  if (!signal.connected) return 'no-link';
  if (!signal.bridgeUp || signal.failed) return 'no-bridge';
  return 'stalled';
}

/** Why there is no image, per state; shown only as the tooltip of the offline panel. */
const FEED_MESSAGE: Record<Exclude<FeedState, 'image'>, string> = {
  'no-link': 'Sem enlace com a aeronave',
  'no-bridge': 'A ponte MJPEG na porta 9090 não respondeu.',
  stalled:
    'A ponte de vídeo está no ar, mas a câmera parou de enviar quadros. Verifique o driver e o enlace com a aeronave.',
};

/**
 * The live feed, with the flight instruments painted onto it.
 *
 * The HUD sits over the video rather than beside it because in flight the
 * operator's eyes are on the image. The flight time sits alone at the top
 * centre, the format of the feed in the top-left corner and the altitude and
 * speed in the bottom-left, each in the same chip; link and charge live in the
 * global top bar. All of it hugs the edges, clear of the optical centre where
 * the target will appear.
 *
 * The bounding boxes are drawn upstream, by the mission or by the bridge over
 * the raw camera stream, and arrive in the image already on screen.
 */
export const OpticalFeed: React.FC<OpticalFeedProps> = ({
  fps,
  bridgeUp,
  live,
  width,
  height,
  telemetry,
  running,
  flash,
  gimbalTilt,
  cameraTilt,
  cameraAvailable,
  onCameraTilt,
}) => {
  const [nonce, setNonce] = useState(0);
  const [failed, setFailed] = useState(false);
  const timerRef = useRef<number | null>(null);

  // The handle is local and the readout follows it, so a drag is not paced by
  // the round trip to the aircraft. `cameraTilt` is what the bridge confirmed
  // it published, and it takes over whenever the operator is not dragging.
  const [handle, setHandle] = useState<number>(() => cameraTilt ?? TILT_MAX);
  const draggingRef = useRef(false);
  const releaseDrag = useCallback(() => {
    draggingRef.current = false;
  }, []);

  useEffect(() => {
    if (draggingRef.current) return;
    const target =
      cameraTilt ??
      (running && gimbalTilt !== null ? gimbalTilt : null) ??
      (telemetry.camera_tilt_deg !== null && telemetry.camera_tilt_deg !== undefined
        ? telemetry.camera_tilt_deg
        : null);
    if (target !== null && Number.isFinite(target)) {
      setHandle(Math.max(TILT_MIN, Math.min(TILT_MAX, Math.round(target))));
    }
  }, [cameraTilt, gimbalTilt, running, telemetry.camera_tilt_deg]);

  const commandTilt = useCallback(
    (degrees: number) => {
      const clamped = Math.max(TILT_MIN, Math.min(TILT_MAX, Math.round(degrees)));
      setHandle(clamped);
      onCameraTilt(clamped);
    },
    [onCameraTilt]
  );

  // A dropped MJPEG connection never recovers on its own: re-request it.
  useEffect(() => {
    if (!failed || !bridgeUp) return;
    timerRef.current = window.setTimeout(() => {
      setFailed(false);
      setNonce((n) => n + 1);
    }, RECONNECT_DELAY_MS);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [failed, bridgeUp]);

  // The bridge coming back after a restart invalidates the current connection.
  useEffect(() => {
    if (bridgeUp) return;
    setFailed(false);
    setNonce((n) => n + 1);
  }, [bridgeUp]);

  // Frames resuming after a stall get a fresh connection: the old one was
  // unmounted with the frozen image, and a reused URL could hand it back.
  useEffect(() => {
    if (live) setNonce((n) => n + 1);
  }, [live]);

  const connected = Boolean(telemetry.connected);
  // Altitude and speed also follow a benchtop simulation, which has no link.
  const navFresh = Boolean(telemetry.nav_fresh ?? telemetry.data_fresh);
  const state = feedState({ bridgeUp, failed, live, connected });

  return (
    <div className="relative h-full w-full overflow-hidden rounded-panel bg-black">
      {state === 'image' ? (
        <img
          key={nonce}
          src={`${STREAM_URL}?t=${nonce}`}
          alt="Transmissão óptica ao vivo do Bebop 2"
          onError={() => setFailed(true)}
          className="absolute inset-0 h-full w-full object-cover"
        />
      ) : (
        <div className="mesh-fine absolute inset-0 flex flex-col items-center justify-center gap-3">
          <VideoOff size={26} strokeWidth={1.4} className="text-haze-deep" aria-hidden />
          <p className="title-pixel font-pixel text-base tracking-[0.06em] text-haze">Sem sinal óptico</p>
          <span
            data-feed-state={state}
            aria-label={FEED_MESSAGE[state]}
            className="absolute inset-0"
          />
        </div>
      )}

      {/* Optical axis. The IBVS law drives the target onto this cross. */}
      {live ? (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
          <svg width="72" height="72" viewBox="0 0 72 72" aria-hidden>
            <circle cx="36" cy="36" r="22" fill="none" stroke="#12B382" strokeOpacity="0.3" strokeWidth="1" />
            <line x1="36" y1="4" x2="36" y2="22" stroke="#12B382" strokeWidth="1.25" opacity="0.85" />
            <line x1="36" y1="50" x2="36" y2="68" stroke="#12B382" strokeWidth="1.25" opacity="0.85" />
            <line x1="4" y1="36" x2="22" y2="36" stroke="#12B382" strokeWidth="1.25" opacity="0.85" />
            <line x1="50" y1="36" x2="68" y2="36" stroke="#12B382" strokeWidth="1.25" opacity="0.85" />
            <circle cx="36" cy="36" r="1.5" fill="#34D9A2" />
          </svg>
        </div>
      ) : null}

      {/* Top centre: how long the aircraft has been flying, and nothing else. */}
      <div className="pointer-events-none absolute left-1/2 top-3 z-30 -translate-x-1/2" data-flight-timer>
        <HudChip tone={running ? 'mint' : 'default'}>
          <span className="text-xs">{(connected || telemetry.simulated) && Number.isFinite(telemetry.flight_time_sec) ? duration(telemetry.flight_time_sec) : '—'}</span>
        </HudChip>
      </div>

      {/* Top-left: the format of what is on screen. */}
      <div className="pointer-events-none absolute left-3 top-3 z-30 flex items-center gap-2">
        <HudChip icon={<Video size={11} strokeWidth={2} />} tone={live && fps < 8 ? 'amber' : 'default'}>
          {live ? `${formatName(width, height)} · ${fps} FPS` : 'sem vídeo'}
        </HudChip>
      </div>

      {/* Bottom-left: the airframe's own state, in a chip like the others. */}
      <div className="pointer-events-none absolute bottom-3 left-3 z-30" data-flight-figures>
        <HudChip icon={<Gauge size={11} strokeWidth={2} />}>
          <span className="flex items-baseline gap-3">
            <Figure label="alt" value={navFresh ? telemetry.altitude.toFixed(2) : '—'} unit="m" />
            <Figure label="vel" value={navFresh ? telemetry.speed.toFixed(2) : '—'} unit="m/s" />
          </span>
        </HudChip>
      </div>

      {/* Camera tilt, on the picture it changes.

          Vertical and on the right edge because the axis it commands is
          vertical: the handle travels the way the lens does. Putting it here
          rather than in a panel is the whole point — the operator judges an
          angle by looking at the frame it produces, and a control that forces
          them to look somewhere else to change it makes that judgement
          impossible. */}
      <div
        className={cn(
          'absolute right-3 top-1/2 z-30 flex -translate-y-1/2 flex-col items-center gap-2',
          'rounded-full border border-frost/10 bg-abyss/55 px-2 py-3 backdrop-blur-sm',
          !cameraAvailable && 'opacity-40'
        )}
      >
        <span className="hud-legible font-cond text-3xs tracking-wide text-frost/55">0°</span>

        <div className="group relative h-[132px] w-5">
          <input
            type="range"
            aria-label="Inclinação da câmera em graus"
            min={TILT_MIN}
            max={TILT_MAX}
            step={1}
            value={handle}
            disabled={!cameraAvailable}
            onPointerDown={() => {
              draggingRef.current = true;
            }}
            onPointerUp={releaseDrag}
            onPointerCancel={releaseDrag}
            onLostPointerCapture={releaseDrag}
            onBlur={releaseDrag}
            onChange={(e) => commandTilt(Number(e.target.value))}
            className="peer absolute left-1/2 top-1/2 h-5 w-[132px] -translate-x-1/2 -translate-y-1/2 cursor-pointer opacity-0 disabled:cursor-not-allowed"
            // Counter-clockwise, so the input's maximum (0°, horizon) lands at
            // the top of the track and its minimum (−80°, nadir) at the bottom
            // — the handle then travels the way the lens does, and the way the
            // two labels bracketing it say it does.
            style={{ transform: 'translate(-50%, -50%) rotate(-90deg)' }}
          />
          <div className="pointer-events-none absolute bottom-0 left-1/2 top-0 w-[2px] -translate-x-1/2 rounded-full bg-frost/20" />
          <div
            className="pointer-events-none absolute left-1/2 top-0 w-[2px] -translate-x-1/2 rounded-full bg-mint slider-fill-flight-v"
            style={{ height: `${((TILT_MAX - handle) / (TILT_MAX - TILT_MIN)) * 100}%` }}
          />
          <div
            className={cn(
              'pointer-events-none absolute left-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full',
              'border-2 border-mint-bright bg-hull-deep transition-shadow duration-150 shadow-[0_0_8px_rgba(18,179,130,0.5)]',
              'peer-hover:shadow-live peer-focus-visible:shadow-live'
            )}
            style={{ top: `${((TILT_MAX - handle) / (TILT_MAX - TILT_MIN)) * 100}%` }}
          />
        </div>

        <span className="hud-legible font-cond text-3xs tracking-wide text-frost/55">−80°</span>

        <span
          className={cn(
            'hud-legible tnum rounded-full px-1.5 font-mono text-2xs',
            cameraTilt === handle ? 'text-mint' : 'text-frost'
          )}
        >
          {handle}°
        </span>
      </div>

      {flash ? (
        <div className="anim-flash pointer-events-none absolute inset-0 z-40 bg-frost" />
      ) : null}
    </div>
  );
};

const Figure: React.FC<{ label: string; value: string; unit: string }> = ({ label, value, unit }) => (
  <span className="flex items-baseline gap-1">
    <span className="font-cond text-3xs tracking-wide text-frost/55">{label}</span>
    <span className="tnum font-mono text-2xs text-frost">{value}</span>
    <span className="font-mono text-3xs text-frost/55">{unit}</span>
  </span>
);
