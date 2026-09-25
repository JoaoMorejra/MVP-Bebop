import { useCallback, useEffect, useRef, useState } from 'react';
import type { BmgTelemetry } from '../types/bmg';
import type { TelemetryView, TrackPoint } from '../types/mission';
import { toEnu } from '../lib/geo';
import { useBridge } from './useBridge';
import { useInterval } from './useInterval';

const BLANK: BmgTelemetry = {
  connected: false,
  driver_running: false,
  battery_pct: 0,
  wifi_ssid: '',
  wifi_signal_dbm: -100,
  speed: 0,
  altitude: 0,
  flight_time_sec: 0,
  heading: 0,
  latitude: 0,
  longitude: 0,
  battery_known: false,
  battery_source: 'none',
  signal_source: 'none',
  flying_state: null,
  flying_state_label: 'unknown',
  node_present: false,
  topics: {},
  topics_ready: false,
};

/** Bridge heartbeats older than this mean the bridge itself has stopped. */
const BRIDGE_SILENT_AFTER_SEC = 4;
/** Enough at 2 Hz for roughly twenty minutes of track. */
const TRACK_LIMIT = 2400;
/** Below this the airframe has not moved enough to be worth a new vertex. */
const TRACK_MIN_STEP_M = 0.05;

/**
 * Single source of truth for airframe state.
 *
 * When the Electron bridge is present, every value here came off
 * `/bebop/odom` by way of `streamer/telemetry_bridge.py`. Nothing is
 * interpolated or invented — a stale link reports itself as stale rather than
 * coasting on its last reading. Synthetic values appear only when there is no
 * bridge at all, which is the browser development case.
 */
export function useTelemetry() {
  const bridge = useBridge();
  const [raw, setRaw] = useState<BmgTelemetry>(BLANK);
  const [lastFrameAt, setLastFrameAt] = useState<number | null>(null);
  const [track, setTrack] = useState<TrackPoint[]>([]);
  const [now, setNow] = useState(() => Date.now());

  const homeRef = useRef<{ lat: number; lng: number } | null>(null);
  const odomHomeRef = useRef<{ x: number; y: number } | null>(null);
  const syntheticRef = useRef(0);

  const ingest = useCallback((data: BmgTelemetry) => {
    setRaw((prev) =>
      // A sample saying the aircraft is unreachable replaces the previous one
      // rather than merging into it. Merging is right for a field this frame
      // happened not to carry and wrong for every field when the aircraft is
      // gone: the last known charge, altitude and SSID would survive the drone
      // being switched off, which is exactly how the station came to report
      // 94 % for a battery sitting on a bench.
      data.connected === false ? { ...BLANK, ...data } : { ...prev, ...data }
    );
    setLastFrameAt(Date.now());

    // `driver_running` is the bridge's own odometry watchdog. Without it there
    // is no fresh position of any kind to draw.
    if (!data.driver_running) return;

    // The trail is drawn from `/bebop/odom` itself, relative to where it was
    // when this flight's track began. Latitude and longitude are only the
    // fallback for an older bridge that does not publish the raw odometry:
    // they switch between GPS and projection when a fix comes and goes, and a
    // trail built on them jumps by the difference.
    let x: number;
    let y: number;
    //
    // East and north, not the raw `odom_x_m`/`odom_y_m`: the driver's frame is
    // x north, y west, and drawing it as x east rotated the whole trail by 90
    // degrees (`telemetry_bridge.py:odom_to_enu`).
    if (typeof data.east_m === 'number' && typeof data.north_m === 'number') {
      if (!odomHomeRef.current) odomHomeRef.current = { x: data.east_m, y: data.north_m };
      x = data.east_m - odomHomeRef.current.x;
      y = data.north_m - odomHomeRef.current.y;
    } else {
      if (!data.latitude || !data.longitude) return;
      if (!homeRef.current) {
        homeRef.current = { lat: data.latitude, lng: data.longitude };
      }
      const home = homeRef.current;
      ({ x, y } = toEnu(data.latitude, data.longitude, home.lat, home.lng));
    }

    setTrack((prev) => {
      const last = prev[prev.length - 1];
      if (last && Math.hypot(last.x - x, last.y - y) < TRACK_MIN_STEP_M && Math.abs(last.alt - data.altitude) < 0.1) {
        return prev;
      }
      const next = [...prev, { x, y, alt: data.altitude, at: Date.now() }];
      return next.length > TRACK_LIMIT ? next.slice(next.length - TRACK_LIMIT) : next;
    });
  }, []);

  useEffect(() => {
    if (!bridge) return;
    void bridge.getTelemetry().then(ingest).catch(() => undefined);
    return bridge.onTelemetryUpdate(ingest);
  }, [bridge, ingest]);

  // No bridge means a browser session. Produce an obviously synthetic orbit so
  // the layout can be worked on, and label it as such everywhere it surfaces.
  useInterval(
    () => {
      if (bridge) return;
      syntheticRef.current += 0.5;
      const t = syntheticRef.current;
      const lat = -19.8703 + Math.sin(t / 14) * 0.00018;
      const lng = -43.9678 + Math.cos(t / 19) * 0.00022;
      ingest({
        connected: true,
        driver_running: true,
        battery_pct: Math.max(40, 92 - Math.floor(t / 30)),
        wifi_ssid: 'Bebop2-SIM',
        wifi_signal_dbm: -52 + Math.round(Math.sin(t / 5) * 6),
        speed: 0.18 + Math.sin(t / 3) * 0.06,
        altitude: 1.0 + Math.sin(t / 7) * 0.12,
        flight_time_sec: Math.floor(t),
        heading: (t * 6) % 360,
        latitude: lat,
        longitude: lng,
        battery_known: true,
        battery_source: 'aircraft',
        signal_source: 'aircraft',
        flying_state: 3,
        flying_state_label: 'flying',
        node_present: true,
        topics_ready: true,
      });
    },
    bridge ? null : 500
  );

  useInterval(() => setNow(Date.now()), 500);

  const ageSec = lastFrameAt === null ? null : (now - lastFrameAt) / 1000;

  // The bridge prints a frame every 500 ms whether or not /bebop/odom is
  // publishing, so its heartbeat alone proves nothing about the aircraft. It
  // runs its own odometry watchdog and reports the verdict as `driver_running`
  // (`TelemetryState.to_payload`, which requires a recent /bebop/odom sample
  // *and* the driver node in the ROS 2 graph); that flag makes a reading current.
  const bridgeSilent = ageSec === null || ageSec > BRIDGE_SILENT_AFTER_SEC;
  const connected = Boolean(raw.connected && !bridgeSilent);
  const batteryKnown = connected && Boolean(raw.battery_known);
  const stale = !connected || !raw.driver_running;
  // A bridge older than `data_fresh` is read through its own odometry watchdog.
  const dataFresh = connected && Boolean(raw.data_fresh ?? raw.driver_running);

  const view: TelemetryView = {
    ...raw,
    connected,
    battery_known: batteryKnown,
    battery_pct: batteryKnown ? raw.battery_pct : 0,
    wifi_ssid: connected ? raw.wifi_ssid : '',
    wifi_signal_dbm: dataFresh ? raw.wifi_signal_dbm : -100,
    driver_running: connected && Boolean(raw.driver_running),
    gps_fix: dataFresh && Boolean(raw.gps_fix),
    data_fresh: dataFresh,
    // Second line of defence behind the bridge's own gating: nothing measured
    // on the airframe is shown once the bridge or the aircraft goes quiet.
    altitude: dataFresh ? raw.altitude : 0,
    speed: dataFresh ? raw.speed : 0,
    heading: dataFresh ? raw.heading : 0,
    flight_time_sec: connected ? raw.flight_time_sec : 0,
    latitude: connected ? raw.latitude : 0,
    longitude: connected ? raw.longitude : 0,
    source: bridge ? 'hardware' : 'synthetic',
    ageSec,
  };

  const resetTrack = useCallback(() => {
    homeRef.current = null;
    odomHomeRef.current = null;
    setTrack([]);
  }, []);

  return { telemetry: view, track, stale, resetTrack };
}
