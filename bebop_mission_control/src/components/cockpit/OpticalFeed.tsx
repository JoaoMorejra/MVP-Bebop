import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Battery, Timer, Video, VideoOff, ScanLine } from 'lucide-react';
import type { TelemetryView } from '../../types/mission';
import { cn, duration, rfBars } from '../../lib/format';

interface OpticalFeedProps {
  fps: number;
  /** The MJPEG bridge answered its status endpoint. */
  bridgeUp: boolean;
  /** A frame arrived recently, as measured by the bridge itself. */
  live: boolean;
  width: number;
  height: number;
  /** Topic the current frames come from. */
  source: string;
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
/** The topic the mission publishes YOLO-annotated frames on. */
const DETECTION_TOPIC = '/bebop/camera/detections';

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
  icon: React.ReactNode;
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

/**
 * The live feed, with the flight instruments painted onto it.
 *
 * The HUD sits over the video rather than beside it because in flight the
 * operator's eyes are on the image: elapsed time, format, link and charge have
 * to be readable without looking away from what the aircraft is seeing. Both
 * clusters hug the corners, clear of the optical centre where the target will
 * appear.
 *
 * The bounding boxes are drawn upstream. While the mission runs it publishes
 * annotated frames on `/bebop/camera/detections` and the bridge prefers them,
 * so the boxes arrive burned into the image already on screen — the badge
 * reports which of the two feeds is live, so it is never ambiguous whether
 * inference is actually running.
 */
export const OpticalFeed: React.FC<OpticalFeedProps> = ({
  fps,
  bridgeUp,
  live,
  width,
  height,
  source,
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

  const showImage = bridgeUp && !failed;
  const annotated = source === DETECTION_TOPIC;
  // Gated on the aircraft being reachable, for the same reason the status bar
  // is: neither figure expires on its own, so a powered-down drone would go on
  // reporting its last charge over a feed showing nothing.
  const connected = Boolean(telemetry.connected);
  const known = connected && Boolean(telemetry.battery_known);
  const charge = known ? telemetry.battery_pct : 0;
  const bars = connected ? rfBars(telemetry.wifi_signal_dbm) : 0;

  return (
    <div className="relative h-full w-full overflow-hidden rounded-panel border border-strut-soft bg-black">
      {showImage ? (
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
          <p className="text-sm text-haze">Sem sinal óptico</p>
          <p className="max-w-[38ch] text-center text-2xs leading-relaxed text-haze-deep">
            {connected
              ? 'A ponte MJPEG na porta 9090 não respondeu. Os logs do serviço mjpeg estão em Diagnóstico.'
              : 'Sem enlace com a aeronave. Conecte-se à rede publicada pelo Bebop para receber vídeo e telemetria.'}
          </p>
        </div>
      )}

      {/* Optical axis. The IBVS law drives the target onto this cross. */}
      {live ? (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
          <svg width="72" height="72" viewBox="0 0 72 72" aria-hidden>
            <circle cx="36" cy="36" r="22" fill="none" stroke="#01D5A3" strokeOpacity="0.22" strokeWidth="1" />
            <line x1="36" y1="4" x2="36" y2="22" stroke="#01D5A3" strokeWidth="1.25" opacity="0.8" />
            <line x1="36" y1="50" x2="36" y2="68" stroke="#01D5A3" strokeWidth="1.25" opacity="0.8" />
            <line x1="4" y1="36" x2="22" y2="36" stroke="#01D5A3" strokeWidth="1.25" opacity="0.8" />
            <line x1="50" y1="36" x2="68" y2="36" stroke="#01D5A3" strokeWidth="1.25" opacity="0.8" />
            <circle cx="36" cy="36" r="1.5" fill="#5CF2CE" />
          </svg>
        </div>
      ) : null}

      {/* Top-left: how long, and in what format. */}
      <div className="pointer-events-none absolute left-3 top-3 z-30 flex items-center gap-2">
        <HudChip icon={<Timer size={11} strokeWidth={2} />} tone={running ? 'mint' : 'default'}>
          {duration(telemetry.flight_time_sec)}
        </HudChip>
        <HudChip icon={<Video size={11} strokeWidth={2} />} tone={live && fps < 8 ? 'amber' : 'default'}>
          {live ? `${formatName(width, height)} · ${fps} FPS` : 'sem vídeo'}
        </HudChip>
        {/* Which of the two feeds is on screen.
            The MJPEG bridge prefers `/bebop/camera/detections` whenever the
            mission is publishing it, so the boxes arrive burned into the image.
            While a flight is running and they are absent, the chip says so in
            amber rather than going quiet: an operator must never be left to
            infer from a clean frame that the detector found nothing, when the
            truth is that the detector is not in the picture at all. */}
        {live && annotated ? (
          <HudChip icon={<ScanLine size={11} strokeWidth={2} />} tone="mint">
            YOLO
          </HudChip>
        ) : live && running ? (
          <HudChip icon={<ScanLine size={11} strokeWidth={2} />} tone="amber">
            sem YOLO
          </HudChip>
        ) : null}
      </div>

      {/* Top-right: what ends a flight early.

          The radio is bars and nothing else. A dBm figure over live video is a
          number the operator cannot act on — the decision it feeds is "is the
          link holding", which four bars answer at a glance and a negative
          integer does not. The charge keeps its figure, because there the
          decision is "how much flight is left" and that is the number. */}
      <div className="pointer-events-none absolute right-3 top-3 z-30 flex items-center gap-2">
        <span
          className={cn(
            'flex items-center gap-1.5 rounded-full border border-frost/10 bg-abyss/55 px-2.5 py-1.5 backdrop-blur-sm',
            bars <= 1 && telemetry.connected && 'border-amber/35'
          )}
          title={
            connected
              ? `Enlace com a aeronave: ${telemetry.wifi_signal_dbm} dBm`
              : 'Sem enlace com a aeronave'
          }
        >
          <span className="inline-flex items-end gap-[2px]" aria-hidden>
            {[0, 1, 2, 3].map((i) => (
              <span
                key={i}
                className={cn(
                  'w-[3px] rounded-[1px] transition-colors duration-300',
                  i < bars
                    ? bars <= 1
                      ? 'bg-amber'
                      : 'bg-frost'
                    : 'bg-frost/20'
                )}
                style={{ height: 4 + i * 3 }}
              />
            ))}
          </span>
          <span className="sr-only">
            {connected ? `Sinal: ${bars} de 4 barras` : 'Sem sinal'}
          </span>
        </span>
        <HudChip
          icon={<Battery size={11} strokeWidth={2} />}
          tone={!known ? 'default' : charge <= 15 ? 'ember' : charge <= 30 ? 'amber' : 'mint'}
        >
          {known ? `${charge}%` : '—'}
        </HudChip>
      </div>

      {/* Bottom-left: the airframe's own state. Quieter than the corners above,
          because these change every frame and must not pull the eye. */}
      <div className="pointer-events-none absolute bottom-3 left-3 z-30 flex items-end gap-4">
        <Figure label="alt" value={telemetry.altitude.toFixed(2)} unit="m" />
        <Figure label="vel" value={telemetry.speed.toFixed(2)} unit="m/s" />
        {gimbalTilt !== null ? (
          <Figure label="gimbal" value={gimbalTilt.toFixed(0)} unit="°" />
        ) : null}
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
            className="pointer-events-none absolute left-1/2 top-0 w-[2px] -translate-x-1/2 rounded-full bg-mint"
            style={{ height: `${((TILT_MAX - handle) / (TILT_MAX - TILT_MIN)) * 100}%` }}
          />
          <div
            className={cn(
              'pointer-events-none absolute left-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full',
              'border-2 border-mint bg-hull-deep transition-shadow duration-150',
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
          title={
            !cameraAvailable
              ? 'O controle de câmera só funciona no aplicativo desktop'
              : cameraTilt === null
              ? 'Ângulo ainda não comandado'
              : cameraTilt === handle
              ? 'Ângulo confirmado pela câmera'
              : 'Enviando'
          }
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

const Figure: React.FC<{ label: string; value: string; unit: string }> = ({
  label,
  value,
  unit,
}) => (
  <div>
    <div className="hud-legible font-cond text-3xs leading-none tracking-wide text-frost/45">
      {label}
    </div>
    <div className="flex items-baseline gap-1">
      <span className="hud-legible tnum font-mono text-sm text-frost">{value}</span>
      <span className="hud-legible font-mono text-3xs text-frost/55">{unit}</span>
    </div>
  </div>
);
