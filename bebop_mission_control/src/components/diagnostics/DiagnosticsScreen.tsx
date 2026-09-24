import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownToLine, Pause, Play, Search, Trash2 } from 'lucide-react';
import type { LogLine, TerminalInfo } from '../../types/bmg';
import { cn } from '../../lib/format';
import { useBridge } from '../../hooks/useBridge';

interface DiagnosticsScreenProps {
  missionLog: LogLine[];
  driverLog: LogLine[];
  onClearLogs: () => void;
  /** `mission.py` is up (running or arming). */
  missionRunning: boolean;
  /** Emergency landing: the same path as the cockpit's abort. */
  onLand: () => Promise<void> | void;
  /** `exit` closes the terminal. */
  onClose?: () => void;
}

type LogSource = 'mission' | 'driver' | 'none';

/** One thing the terminal printed, from the process logs or from the operator's shell. */
type Entry =
  | { kind: 'log'; source: 'mission' | 'driver'; type: string; text: string; at: number }
  | { kind: 'cmd'; command: string; cwd: string; at: number }
  | { kind: 'chunk'; job: string; stream: 'stdout' | 'stderr'; text: string; at: number }
  | { kind: 'info'; tone: 'info' | 'ok' | 'warn' | 'error'; text: string; at: number };

interface Row {
  key: string;
  text: string;
  tone: 'plain' | 'stderr' | 'exit' | 'cmd' | 'info' | 'ok' | 'warn' | 'error';
  /** For `cmd` rows: the prompt that preceded the command. */
  prompt?: string;
}

const PROMPT_USER = 'operator@bmg-ground-station';
const HISTORY_KEY = 'bmg.terminal-history.v1';
const HISTORY_LIMIT = 200;
/** Rendering more than this makes the scroll the bottleneck, not the data. */
const ROW_LIMIT = 2500;

const HELP_TEXT = [
  'Comandos embutidos:',
  '  help            esta ajuda',
  '  clear           limpa a tela (Ctrl+L)',
  '  land            POUSO DE EMERGÊNCIA: publica /bebop/land e encerra a missão',
  '  topics          lista os tópicos ROS 2 ativos (ros2 topic list -t)',
  '  nodes           lista os nós ROS 2 ativos',
  '  history         comandos anteriores',
  '  exit            fecha o terminal',
  '',
  'Qualquer outro comando roda em bash, no ambiente da missão (nectar-activate):',
  '  ros2 topic echo /bebop/odom --once',
  '  ros2 topic hz /bebop/camera/image_raw',
  "  ros2 topic pub --once /bebop/land std_msgs/msg/Empty '{}'",
  '  ping -c 3 192.168.42.1',
  '',
  'Ctrl+C interrompe o comando em execução. Setas ↑/↓ percorrem o histórico.',
];

function loadHistory(): string[] {
  try {
    const raw = window.localStorage.getItem(HISTORY_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function saveHistory(history: string[]) {
  try {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-HISTORY_LIMIT)));
  } catch {
    /* private mode: the history lives for the session only */
  }
}

const TIMESTAMP_RE =
  /(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?|\[\d+\.\d+\]|\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b)/;
const TOKEN_RE = new RegExp(
  [
    TIMESTAMP_RE.source,
    // Log levels.
    '(\\[(?:INFO|DEBUG)\\]|\\b(?:INFO|DEBUG)\\b)',
    '(\\[(?:WARN|WARNING)\\]|\\b(?:WARN|WARNING)\\b)',
    '(\\[(?:ERROR|FATAL|CRITICAL)\\]|\\b(?:ERROR|FATAL|CRITICAL|Traceback)\\b)',
    // The stage markers the station keys the cockpit on.
    '(\\[?STEP [1-5]:[^\\]]*\\]?)',
    // ROS topics and namespaces.
    '((?<![\\w.~])/(?:bebop|mission|bmg|rosout|parameter_events|tf|camera)[\\w/]*)',
  ].join('|'),
  'g'
);

/** A line, coloured the way a Linux terminal with a log highlighter would colour it. */
const Highlighted: React.FC<{ text: string }> = ({ text }) => {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  TOKEN_RE.lastIndex = 0;
  let n = 0;
  while ((match = TOKEN_RE.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const [token, ts, info, warn, error, step, topic] = match;
    const className = ts
      ? 'text-mint/80'
      : info
      ? 'text-haze'
      : warn
      ? 'text-amber font-semibold'
      : error
      ? 'text-ember font-semibold'
      : step
      ? 'text-mint-bright font-semibold'
      : topic
      ? 'text-cyan'
      : '';
    parts.push(
      <span key={n++} className={className}>
        {token}
      </span>
    );
    last = match.index + token.length;
    if (token.length === 0) TOKEN_RE.lastIndex += 1;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
};

function toneOfLog(type: string, text: string): Row['tone'] {
  if (type === 'exit') return 'exit';
  if (/\b(ERROR|FATAL|CRITICAL|Traceback|Exception)\b/.test(text)) return 'error';
  if (/\bWARN(ING)?\b/.test(text)) return 'warn';
  return type === 'stderr' ? 'stderr' : 'plain';
}

const TONE_CLASS: Record<Row['tone'], string> = {
  plain: 'text-frost/85',
  stderr: 'text-frost/70',
  exit: 'text-mint',
  cmd: 'text-frost',
  info: 'text-haze',
  ok: 'text-mint',
  warn: 'text-amber',
  error: 'text-ember',
};

/**
 * Diagnostics, as a terminal.
 *
 * The live stdout and stderr of `mission.py` (or of the driver and bridges)
 * scroll past exactly as a shell would print them, coloured the way a log
 * highlighter would — timestamps mint, topics cyan, warnings amber, errors red —
 * and a real prompt at the foot runs commands in the mission's own environment
 * through `bmg:terminal-exec`, so `ros2 topic echo` sees the aircraft's graph.
 *
 * `clear` hides what is on screen without discarding the process history the
 * host keeps; the logs are evidence, and a tidy screen is not a reason to lose
 * them.
 */
export const DiagnosticsScreen: React.FC<DiagnosticsScreenProps> = ({
  missionLog,
  driverLog,
  onClearLogs,
  missionRunning,
  onLand,
  onClose,
}) => {
  const bridge = useBridge();
  const [source, setSource] = useState<LogSource>('mission');
  const [filter, setFilter] = useState('');
  const [showFilter, setShowFilter] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [clearedAt, setClearedAt] = useState(0);
  const [session, setSession] = useState<Entry[]>([]);
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<string[]>(loadHistory);
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  const [runningJob, setRunningJob] = useState<string | null>(null);
  const [info, setInfo] = useState<TerminalInfo | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const jobSeq = useRef(0);
  const draftRef = useRef('');

  const push = useCallback((entry: Entry) => setSession((previous) => [...previous, entry].slice(-4000)), []);
  const note = useCallback(
    (text: string, tone: 'info' | 'ok' | 'warn' | 'error' = 'info') =>
      push({ kind: 'info', tone, text, at: Date.now() }),
    [push]
  );

  // Where the shell is, and a banner saying what this is.
  useEffect(() => {
    if (!bridge) {
      note('Sem ponte Electron: o terminal só executa comandos no aplicativo desktop.', 'warn');
      return;
    }
    void bridge
      .getTerminalInfo()
      .then(setInfo)
      .catch(() => undefined);
    void bridge
      .getEnvInfo()
      .then((env) =>
        note(
          `BMG Ground Station · ROS 2 ${env.rosDistro} · ROS_DOMAIN_ID=${env.rosDomainId} · ${env.venvPath}. Digite help.`
        )
      )
      .catch(() => note('BMG Ground Station. Digite help.'));
  }, [bridge, note]);

  // Streamed output of the command that is running.
  useEffect(() => {
    if (!bridge) return;
    return bridge.onTerminalOutput((event) => {
      push({ kind: 'chunk', job: event.id, stream: event.stream, text: event.text, at: Date.now() });
    });
  }, [bridge, push]);

  const cwd = info?.cwd ?? '/home/joaomoreira/ros2_ws';
  const home = info?.home ?? '/home/joaomoreira';
  const displayCwd = cwd === home ? '~' : cwd.startsWith(`${home}/`) ? `~${cwd.slice(home.length)}` : cwd;
  const prompt = `${PROMPT_USER}:${displayCwd}$`;

  const rows = useMemo((): Row[] => {
    const logs: Entry[] = [];
    const lines = source === 'mission' ? missionLog : source === 'driver' ? driverLog : [];
    for (const line of lines) {
      const at = line.at ?? 0;
      if (at < clearedAt) continue;
      logs.push({ kind: 'log', source: source === 'driver' ? 'driver' : 'mission', type: line.type, text: line.text, at });
    }
    const entries = [...logs, ...session.filter((e) => e.at >= clearedAt)].sort((a, b) => a.at - b.at);

    const out: Row[] = [];
    let i = 0;
    while (i < entries.length) {
      const entry = entries[i];
      if (entry.kind === 'chunk') {
        // Consecutive chunks of one stream of one job are one text: a line the
        // pipe delivered in two reads is still one line.
        let text = entry.text;
        let j = i + 1;
        while (j < entries.length) {
          const next = entries[j];
          if (next.kind !== 'chunk' || next.job !== entry.job || next.stream !== entry.stream) break;
          text += next.text;
          j += 1;
        }
        text.replace(/\n$/, '').split('\n').forEach((part, k) => {
          out.push({
            key: `c${i}-${k}`,
            text: part,
            tone: entry.stream === 'stderr' ? toneOfLog('stderr', part) : 'plain',
          });
        });
        i = j;
        continue;
      }
      if (entry.kind === 'log') {
        entry.text
          .replace(/\n$/, '')
          .split('\n')
          .forEach((part, k) => {
            if (part.length === 0) return;
            out.push({ key: `l${i}-${k}`, text: part, tone: toneOfLog(entry.type, part) });
          });
      } else if (entry.kind === 'cmd') {
        out.push({ key: `p${i}`, text: entry.command, tone: 'cmd', prompt: entry.cwd });
      } else {
        entry.text.split('\n').forEach((part, k) => out.push({ key: `i${i}-${k}`, text: part, tone: entry.tone }));
      }
      i += 1;
    }

    const needle = filter.trim().toLowerCase();
    const filtered = needle ? out.filter((r) => r.text.toLowerCase().includes(needle)) : out;
    return filtered.length > ROW_LIMIT ? filtered.slice(filtered.length - ROW_LIMIT) : filtered;
  }, [source, missionLog, driverLog, session, clearedAt, filter]);

  useLayoutEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [rows, autoScroll]);

  // Scrolling up pauses the follow, as `less +F` does; reaching the end resumes it.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      setAutoScroll((previous) => (previous === atEnd ? previous : atEnd));
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const clearScreen = useCallback(() => {
    setClearedAt(Date.now());
    setSession([]);
    onClearLogs();
  }, [onClearLogs]);

  const runShell = useCallback(
    async (command: string) => {
      if (!bridge) {
        note('Sem ponte Electron: comando não executado.', 'error');
        return;
      }
      jobSeq.current += 1;
      const id = `job-${Date.now()}-${jobSeq.current}`;
      setRunningJob(id);
      try {
        const result = await bridge.terminalExec(command, id);
        // `cd` answers without streaming; its error is only in the result.
        if (result.stderr && !result.durationMs) note(result.stderr.replace(/\n$/, ''), 'error');
        setInfo((previous) => (previous ? { ...previous, cwd: result.cwd } : previous));
        if (result.signal) {
          note(`[interrompido: ${result.signal}]`, 'warn');
        } else if (result.exitCode !== 0 && result.exitCode !== null) {
          note(`[código de saída ${result.exitCode}]`, 'error');
        }
      } catch (err) {
        note(err instanceof Error ? err.message : 'falha ao executar', 'error');
      } finally {
        setRunningJob(null);
        window.requestAnimationFrame(() => inputRef.current?.focus());
      }
    },
    [bridge, note]
  );

  const execute = useCallback(
    async (raw: string) => {
      const command = raw.trim();
      push({ kind: 'cmd', command: raw, cwd: prompt, at: Date.now() });
      if (!command) return;

      setHistory((previous) => {
        const next = previous[previous.length - 1] === command ? previous : [...previous, command];
        saveHistory(next);
        return next.slice(-HISTORY_LIMIT);
      });

      switch (command) {
        case 'clear':
          clearScreen();
          return;
        case 'help':
          note(HELP_TEXT.join('\n'));
          return;
        case 'history':
          note(history.map((h, index) => `${String(index + 1).padStart(4, ' ')}  ${h}`).join('\n') || '(vazio)');
          return;
        case 'exit':
          onClose?.();
          return;
        case 'land':
          note('POUSO DE EMERGÊNCIA: publicando /bebop/land e encerrando a missão.', 'warn');
          try {
            await onLand();
            note('Pouso comandado. Acompanhe o estado de voo na Cabine.', 'ok');
          } catch (err) {
            note(err instanceof Error ? err.message : 'falha ao comandar o pouso', 'error');
          }
          return;
        case 'topics':
          await runShell('ros2 topic list -t');
          return;
        case 'nodes':
          await runShell('ros2 node list');
          return;
        default:
          await runShell(command);
      }
    },
    [push, prompt, clearScreen, note, history, onClose, onLand, runShell]
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.ctrlKey && (event.key === 'c' || event.key === 'C')) {
      // Copy stays copy when there is a selection.
      if (window.getSelection()?.toString()) return;
      event.preventDefault();
      if (runningJob && bridge) {
        void bridge.terminalKill(runningJob);
        note('^C', 'warn');
      } else {
        push({ kind: 'cmd', command: `${input}^C`, cwd: prompt, at: Date.now() });
        setInput('');
      }
      return;
    }
    if (event.ctrlKey && (event.key === 'l' || event.key === 'L')) {
      event.preventDefault();
      clearScreen();
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      if (runningJob) return;
      const command = input;
      setInput('');
      setHistoryIndex(null);
      setAutoScroll(true);
      void execute(command);
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (history.length === 0) return;
      if (historyIndex === null) draftRef.current = input;
      const next = historyIndex === null ? history.length - 1 : Math.max(0, historyIndex - 1);
      setHistoryIndex(next);
      setInput(history[next]);
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (historyIndex === null) return;
      const next = historyIndex + 1;
      if (next >= history.length) {
        setHistoryIndex(null);
        setInput(draftRef.current);
      } else {
        setHistoryIndex(next);
        setInput(history[next]);
      }
    }
  };

  const followNow = () => {
    setAutoScroll(true);
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  };

  return (
    <div className="flex h-full min-h-0 flex-col p-3">
      <div
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-panel border border-[#0f2a36] shadow-2xl"
        style={{ background: '#03090e' }}
      >
        {/* Title bar. */}
        <div className="flex h-10 shrink-0 items-center gap-3 border-b border-[#0f2a36] bg-[#061219] px-3">
          <span className="flex items-center gap-1.5" aria-hidden>
            <span className="h-3 w-3 rounded-full bg-[#ff5f57]" />
            <span className="h-3 w-3 rounded-full bg-[#febc2e]" />
            <span className="h-3 w-3 rounded-full bg-[#28c840]" />
          </span>
          <span className="min-w-0 flex-1 truncate text-center font-mono text-2xs text-frost/70">
            {PROMPT_USER}: {displayCwd} — bash
          </span>
          <span
            className={cn(
              'flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-3xs',
              missionRunning ? 'border-mint/45 text-mint' : 'border-[#1c3a47] text-haze'
            )}
          >
            <span className={cn('h-1.5 w-1.5 rounded-full', missionRunning ? 'bg-mint anim-breathe' : 'bg-haze-deep')} />
            mission.py: {missionRunning ? 'online' : 'offline'}
          </span>
        </div>

        {/* Toolbar. */}
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-[#0f2a36] px-3 font-mono text-3xs">
          <span className="text-haze-deep">logs:</span>
          {(
            [
              { id: 'mission', label: 'mission.py' },
              { id: 'driver', label: 'driver/pontes' },
              { id: 'none', label: 'ocultar' },
            ] as { id: LogSource; label: string }[]
          ).map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => setSource(option.id)}
              className={cn(
                'rounded px-2 py-0.5 transition-colors',
                source === option.id ? 'bg-mint/15 text-mint' : 'text-haze hover:text-frost'
              )}
            >
              {option.label}
            </button>
          ))}

          <span className="ml-auto flex items-center gap-1">
            {showFilter ? (
              <input
                autoFocus
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') {
                    setFilter('');
                    setShowFilter(false);
                    inputRef.current?.focus();
                  }
                }}
                placeholder="filtrar linhas"
                aria-label="Filtrar linhas"
                className="w-44 rounded border border-[#1c3a47] bg-black/40 px-2 py-0.5 text-frost outline-none focus:border-mint/60"
              />
            ) : null}
            <button
              type="button"
              onClick={() => setShowFilter((previous) => !previous)}
              aria-label="Buscar"
              title="Buscar / filtrar"
              className={cn(
                'rounded p-1 transition-colors hover:text-frost',
                showFilter || filter ? 'text-mint' : 'text-haze'
              )}
            >
              <Search size={12} />
            </button>
            <button
              type="button"
              onClick={() => (autoScroll ? setAutoScroll(false) : followNow())}
              aria-label={autoScroll ? 'Pausar rolagem automática' : 'Retomar rolagem automática'}
              title={autoScroll ? 'Pausar auto-scroll' : 'Retomar auto-scroll'}
              className={cn(
                'flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors hover:text-frost',
                autoScroll ? 'text-mint' : 'text-amber'
              )}
            >
              {autoScroll ? <Pause size={11} /> : <Play size={11} />}
              auto-scroll
            </button>
            <button
              type="button"
              onClick={clearScreen}
              aria-label="Limpar a tela"
              title="clear (Ctrl+L)"
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-haze transition-colors hover:text-frost"
            >
              <Trash2 size={11} />
              clear
            </button>
          </span>
        </div>

        {/* Body. A click anywhere puts the cursor back on the prompt. */}
        <div className="relative min-h-0 flex-1">
          <div
            ref={scrollRef}
            onMouseUp={() => {
              if (!window.getSelection()?.toString()) inputRef.current?.focus();
            }}
            className="scroll-thin h-full overflow-y-auto px-4 py-3 font-mono text-[12px] leading-[1.5]"
          >
            {rows.map((row) =>
              row.tone === 'cmd' ? (
                <div key={row.key} className="whitespace-pre-wrap break-words">
                  <span className="font-semibold text-mint">{row.prompt}</span>{' '}
                  <span className="text-frost">{row.text}</span>
                </div>
              ) : (
                <div key={row.key} className={cn('whitespace-pre-wrap break-words', TONE_CLASS[row.tone])}>
                  {row.tone === 'plain' || row.tone === 'stderr' || row.tone === 'warn' || row.tone === 'error' ? (
                    <Highlighted text={row.text} />
                  ) : (
                    row.text || ' '
                  )}
                </div>
              )
            )}

            {/* The prompt, as the last line of the output. */}
            <div className="flex items-center whitespace-pre">
              <span className="shrink-0 font-semibold text-mint">{prompt}</span>
              <span className="w-2 shrink-0" />
              <div className="relative flex min-w-0 flex-1 items-center">
                <input
                  ref={inputRef}
                  autoFocus
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    setHistoryIndex(null);
                  }}
                  onKeyDown={onKeyDown}
                  spellCheck={false}
                  autoComplete="off"
                  aria-label="Linha de comando"
                  className="w-full bg-transparent font-mono text-[12px] text-frost outline-none"
                  style={{ caretColor: '#01D5A3' }}
                />
                {input.length === 0 && !runningJob ? (
                  <span
                    aria-hidden
                    className="anim-cursor pointer-events-none absolute left-0 top-1/2 h-[15px] w-[8px] -translate-y-1/2 bg-mint"
                  />
                ) : null}
              </div>
              {runningJob ? (
                <span className="ml-3 shrink-0 text-3xs text-amber anim-breathe">
                  executando · Ctrl+C interrompe
                </span>
              ) : null}
            </div>
          </div>

          {!autoScroll ? (
            <button
              type="button"
              onClick={followNow}
              className="absolute bottom-3 right-5 flex items-center gap-1.5 rounded border border-[#1c3a47] bg-[#061219] px-2.5 py-1.5 font-mono text-3xs text-frost transition-colors hover:border-mint/60"
            >
              <ArrowDownToLine size={12} />
              acompanhar
            </button>
          ) : null}
        </div>

        {/* Status line. */}
        <div className="flex h-6 shrink-0 items-center justify-between border-t border-[#0f2a36] bg-[#061219] px-3 font-mono text-[10px] text-haze-deep">
          <span>
            {rows.length} linhas{filter ? ` · filtro "${filter}"` : ''}
          </span>
          <span>help · clear · land · topics · nodes · ↑↓ histórico · Ctrl+C</span>
        </div>
      </div>
    </div>
  );
};
