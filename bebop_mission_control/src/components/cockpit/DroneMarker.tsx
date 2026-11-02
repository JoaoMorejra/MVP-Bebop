import React, { useEffect, useRef } from 'react';
import type { AnimationItem } from 'lottie-web';
import droneAnimation from '../../assets/flyingDrone.json';

/** Side of the square the airframe is drawn in, px. */
export const DRONE_MARKER_PX = 64;

/** Whether the operator asked the system for less motion. */
const reducedMotion = (): boolean =>
  typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);

/**
 * The aircraft on the map: a quadcopter seen from above whose rotors turn only
 * while the aircraft reports them turning, and whose nose is the heading.
 *
 * The artwork is the LottieFiles "Flying drone" by Jafar Mujawar (Lottie
 * Simple License), bundled in `assets/flyingDrone.json` because the station
 * flies on the aircraft's Wi-Fi with no route to a CDN. It is drawn as one
 * solid silhouette in the station's mint, body, arms, rotors and hubs alike,
 * the white backdrop removed, with a dark shadow to lift it off any tile.
 * While the rotors turn, a faint disc under each one reads as its blur.
 *
 * Its front is the top of the drawing, so a rotation by the compass heading
 * (0 north, clockwise, which is how `telemetry_bridge.py:odom_yaw_to_compass`
 * publishes it and how the map draws north) points the nose where the aircraft
 * faces. A wedge ahead of the nose marks it, since the airframe itself is near
 * symmetric.
 *
 * Drawn into an SVG `foreignObject`, so it sits in the map's own frame with
 * the trail. The player is loaded lazily: a session that cannot run it still
 * gets the wedge.
 */
/** Rotor hubs in the marker's frame, px from its centre, as the artwork places them. */
const ROTOR_HUBS: ReadonlyArray<[number, number]> = [
  [-18.9, -14.7],
  [18.9, -14.7],
  [-18.9, 14.4],
  [18.9, 14.4],
];
const ROTOR_BLUR_R = 9;
export const DroneMarker: React.FC<{ heading: number; spinning: boolean }> = ({ heading, spinning }) => {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const animRef = useRef<AnimationItem | null>(null);
  const spinningRef = useRef(spinning);
  spinningRef.current = spinning;

  useEffect(() => {
    // The unit tests run on jsdom, which has no canvas for the player to probe.
    if (import.meta.env.MODE === 'test') return;
    let cancelled = false;
    void import('lottie-web/build/player/lottie_light')
      .then(({ default: lottie }) => {
        if (cancelled || !hostRef.current) return;
        const anim = lottie.loadAnimation({
          container: hostRef.current,
          renderer: 'svg',
          loop: true,
          autoplay: false,
          animationData: droneAnimation,
          rendererSettings: { preserveAspectRatio: 'xMidYMid meet' },
        });
        animRef.current = anim;
        if (spinningRef.current && !reducedMotion()) anim.play();
        else anim.goToAndStop(0, true);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      animRef.current?.destroy();
      animRef.current = null;
    };
  }, []);

  useEffect(() => {
    const anim = animRef.current;
    if (!anim) return;
    if (spinning && !reducedMotion()) anim.play();
    else anim.goToAndStop(0, true);
  }, [spinning]);

  const half = DRONE_MARKER_PX / 2;
  return (
    <g transform={`rotate(${heading})`} data-drone-marker data-spinning={spinning || undefined}>
      {/* Where the nose, and the camera, are pointing. */}
      <path d="M 0 -18 L -11 -46 A 46 46 0 0 1 11 -46 Z" fill="#12B382" fillOpacity="0.25" />
      <path d="M 0 -22 L 4.5 -15.5 L -4.5 -15.5 Z" fill="#12B382" />
      {spinning
        ? ROTOR_HUBS.map(([x, y]) => (
            <circle key={`${x}-${y}`} data-rotor-blur cx={x} cy={y} r={ROTOR_BLUR_R} fill="#12B382" fillOpacity="0.2" />
          ))
        : null}
      <foreignObject x={-half} y={-half} width={DRONE_MARKER_PX} height={DRONE_MARKER_PX}>
        <div
          ref={hostRef}
          style={{
            width: DRONE_MARKER_PX,
            height: DRONE_MARKER_PX,
            filter: 'drop-shadow(0 0 1px rgba(0,8,16,0.95)) drop-shadow(0 2px 4px rgba(0,8,16,0.7))',
          }}
        />
      </foreignObject>
    </g>
  );
};
