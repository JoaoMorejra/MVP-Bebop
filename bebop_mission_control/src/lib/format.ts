/** `m:ss` for short flights, `h:mm:ss` beyond an hour. */
export function duration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return hh > 0 ? `${hh}:${pad(mm)}:${pad(ss)}` : `${mm}:${pad(ss)}`;
}

export function clockTime(d: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** `YYYYMMDD_HHMMSS` as written by the mission, back to a Date. */
export function parseStamp(stamp: string): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(stamp);
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  return new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s));
}

export function stampLabel(stamp: string): string {
  const d = parseStamp(stamp);
  if (!d) return stamp;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}:${pad(d.getSeconds())}`;
}

/** dBm to a 0–4 bar count, using the usual Wi-Fi link thresholds. */
export function rfBars(dbm: number): number {
  if (dbm >= -55) return 4;
  if (dbm >= -67) return 3;
  if (dbm >= -75) return 2;
  if (dbm >= -85) return 1;
  return 0;
}

export function degreesToCardinal(deg: number): string {
  const names = ['N', 'NE', 'L', 'SE', 'S', 'SO', 'O', 'NO'];
  const normalized = ((deg % 360) + 360) % 360;
  return names[Math.round(normalized / 45) % 8];
}

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}
