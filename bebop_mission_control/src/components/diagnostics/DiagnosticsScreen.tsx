import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { ArrowDownToLine, Pause, Play, Plus, Search, Trash2, X } from 'lucide-react';
import type { LogLine, TerminalIdentity } from '../../types/bmg';
import { cn } from '../../lib/format';
import { createLandInterceptor } from '../../lib/terminalInput';
import { parseOsc7, tabLabel, terminalTitle } from '../../lib/terminalTitle';
import { useBridge } from '../../hooks/useBridge';

interface DiagnosticsScreenProps {
  missionLog: LogLine[];
  driverLog: LogLine[];
  onClearLogs: () => void;
  /** `mission.py` is up (running or arming). */
  missionRunning: boolean;
  /** Emergency landing: the same path as the cockpit's abort. */
  onLand: () => Promise<void> | void;
  /** The last shell tab ended (`exit`, Ctrl+D): the terminal closes with it. */
  onClose?: () => void;
}

/** One shell tab. `identity` arrives with the PTY and follows the shell's `cd`. */
interface ShellTab {
  key: number;
  identity: TerminalIdentity | null;
}

type LogSource = 'mission' | 'driver' | 'none';

interface Row {
  key: string;
  text: string;
  tone: 'plain' | 'stderr' | 'exit' | 'warn' | 'error';
}

/** Rendering more than this makes the scroll the bottleneck, not the data. */
const ROW_LIMIT = 2500;

/** The station palette, for the shell's ANSI colours. */
const TERMINAL_THEME = {
  background: '#03090e',
  foreground: '#dbe8e5',
  cursor: '#01D5A3',
  cursorAccent: '#03090e',
  selectionBackground: '#1c3a47',
  black: '#03090e',
  red: '#FF6A45',
  green: '#01D5A3',
  yellow: '#FFC24B',
  blue: '#4FA3FF',
  magenta: '#C792EA',
  cyan: '#4DE3F0',
  white: '#dbe8e5',
  brightBlack: '#557482',
  brightRed: '#FF8A6B',
  brightGreen: '#5CF2CE',
  brightYellow: '#FFD580',
  brightBlue: '#7FBFFF',
  brightMagenta: '#DDB3FF',
  brightCyan: '#8AF2FA',
  brightWhite: '#FFFFFF',
};

const BANNER =
  '\x1b[1;32mBMG Ground Station\x1b[0m \u00b7 bash no ambiente da miss\u00e3o (nectar-activate)\r\n' +
  '\x1b[1;33mland\x1b[0m + Enter = POUSO DE EMERG\u00caNCIA \u00b7 \x1b[1mexit\x1b[0m fecha a aba \u00b7 Tab completa \u00b7 Ctrl+C interrompe\r\n\r\n';

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
  warn: 'text-amber',
  error: 'text-ember',
};

/**
 * The shell: xterm.js on a real PTY in the mission's environment.
 *
 * Keystrokes go to the PTY as raw bytes, so bash owns editing, history and
 * Tab completion. The one exception is the emergency shortcut: every chunk
 * passes through the `land` interceptor first, and `land` + Enter lands the
 * aircraft without the Enter ever reaching the shell -- instantly, and even
 * while a command holds the foreground.
 */
interface ShellPaneProps {
  /** The tab on screen: only it is visible and holds the keyboard. */
  active: boolean;
  onLand: DiagnosticsScreenProps['onLand'];
  onExit?: () => void;
  onIdentity?: (identity: TerminalIdentity) => void;
}

const ShellPane: React.FC<ShellPaneProps> = ({ active, onLand, onExit, onIdentity }) => {
  const bridge = useBridge();
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const onLandRef = useRef(onLand);
  onLandRef.current = onLand;
  const onExitRef = useRef(onExit);
  onExitRef.current = onExit;
  const onIdentityRef = useRef(onIdentity);
  onIdentityRef.current = onIdentity;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const term = new Terminal({
      fontFamily: '"Azeret Mono", SFMono-Regular, Menlo, Consolas, monospace',
      fontSize: 12,
      lineHeight: 1.2,
      cursorBlink: true,
      scrollback: 5000,
      theme: TERMINAL_THEME,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    termRef.current = term;
    fitRef.current = fit;
    try {
      fit.fit();
    } catch {
      // A host with no size yet; the observer below fits it once it has one.
    }
    term.write(BANNER);
    term.focus();

    if (!bridge) {
      term.write('\x1b[33mSem ponte Electron: o terminal s\u00f3 abre no aplicativo desktop.\x1b[0m\r\n');
      return () => {
        termRef.current = null;
        fitRef.current = null;
        term.dispose();
      };
    }

    let id: string | null = null;
    let disposed = false;
    let identity: TerminalIdentity | null = null;
    let reportedCwd: string | null = null;
    const interceptor = createLandInterceptor();

    // The rcfile reports the working directory as OSC 7 after every command.
    // The first report can beat the spawn reply, so it is held until then.
    const osc7 = term.parser.registerOscHandler(7, (payload) => {
      const cwd = parseOsc7(payload);
      if (cwd === null) return false;
      reportedCwd = cwd;
      if (identity && identity.cwd !== cwd) {
        identity = { ...identity, cwd };
        onIdentityRef.current?.(identity);
      }
      return true;
    });

    const offData = bridge.onTerminalData((event) => {
      if (event.id === id) term.write(event.data);
    });
    const offExit = bridge.onTerminalExit((event) => {
      if (event.id !== id) return;
      id = null;
      onExitRef.current?.();
    });

    const input = term.onData((data) => {
      const { forward, land } = interceptor.feed(data);
      if (forward && id) void bridge.terminalWrite(id, forward);
      if (!land) return;
      term.write('\r\n\x1b[1;33m[BMG] POUSO DE EMERG\u00caNCIA: publicando /bebop/land e encerrando a miss\u00e3o.\x1b[0m\r\n');
      Promise.resolve()
        .then(() => onLandRef.current())
        .then(
          () => term.write('\x1b[32m[BMG] Pouso comandado. Acompanhe o estado de voo na Cabine.\x1b[0m\r\n'),
          (error: unknown) =>
            term.write(
              `\x1b[31m[BMG] ${error instanceof Error ? error.message : 'falha ao comandar o pouso'}\x1b[0m\r\n`
            )
        );
    });

    void bridge.terminalSpawn({ cols: term.cols, rows: term.rows }).then((result) => {
      if (disposed) {
        if (result.success) void bridge.terminalKill(result.id);
        return;
      }
      if (!result.success) {
        term.write(`\x1b[31mTerminal indispon\u00edvel: ${result.error}\x1b[0m\r\n`);
        return;
      }
      id = result.id;
      identity = {
        cwd: reportedCwd ?? result.cwd,
        shell: result.shell,
        user: result.user,
        host: result.host,
        home: result.home,
      };
      onIdentityRef.current?.(identity);
      if (result.cols !== term.cols || result.rows !== term.rows) {
        void bridge.terminalResize(result.id, term.cols, term.rows);
      }
    });

    const observer = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        return;
      }
      if (id) void bridge.terminalResize(id, term.cols, term.rows);
    });
    observer.observe(host);

    return () => {
      disposed = true;
      observer.disconnect();
      osc7.dispose();
      input.dispose();
      offData();
      offExit();
      if (id) void bridge.terminalKill(id);
      termRef.current = null;
      fitRef.current = null;
      term.dispose();
    };
  }, [bridge]);

  // Inactive panes stay mounted, hidden but full-size, so their PTYs keep
  // running and their scrollback survives a switch. The newly shown one is
  // refitted in case the window changed while it was hidden.
  useEffect(() => {
    if (!active) return;
    const frame = window.requestAnimationFrame(() => {
      try {
        fitRef.current?.fit();
      } catch {
        // No size yet; the observer fits it once it has one.
      }
      termRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active]);

  return (
    <div
      ref={hostRef}
      className={cn('absolute inset-0 px-3 py-2', !active && 'pointer-events-none invisible')}
      style={{ background: TERMINAL_THEME.background }}
      aria-label="Terminal bash"
      aria-hidden={!active}
    />
  );
};

/**
 * Diagnostics: the process logs, and a real shell.
 *
 * The live stdout and stderr of `mission.py` (or of the driver and bridges)
 * scroll past in their own pane, coloured the way a log highlighter would --
 * timestamps mint, topics cyan, warnings amber, errors red -- and below them
 * an interactive bash on a PTY runs in the mission's own environment, so
 * `ros2 topic echo` sees the aircraft's graph and Tab completes natively.
 *
 * `clear` in the toolbar hides the logs on screen without discarding the
 * process history the host keeps, and clears the shell's screen; the logs are
 * evidence, and a tidy screen is not a reason to lose them.
 */
export const DiagnosticsScreen: React.FC<DiagnosticsScreenProps> = ({
  missionLog,
  driverLog,
  onClearLogs,
  missionRunning,
  onLand,
  onClose,
}) => {
  const [source, setSource] = useState<LogSource>('mission');
  const [filter, setFilter] = useState('');
  const [showFilter, setShowFilter] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [clearedAt, setClearedAt] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const [tabs, setTabs] = useState<ShellTab[]>([{ key: 1, identity: null }]);
  const [activeKey, setActiveKey] = useState(1);
  const nextKey = useRef(2);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const addTab = useCallback(() => {
    const key = nextKey.current++;
    setTabs((previous) => [...previous, { key, identity: null }]);
    setActiveKey(key);
  }, []);

  /** Drop one tab; its pane unmounts and kills its PTY. The last tab is never dropped here. */
  const removeTab = useCallback((key: number) => {
    const current = tabsRef.current;
    const index = current.findIndex((tab) => tab.key === key);
    if (index < 0 || current.length <= 1) return;
    const remaining = current.filter((tab) => tab.key !== key);
    setTabs(remaining);
    setActiveKey((active) => (active === key ? remaining[Math.max(0, index - 1)].key : active));
  }, []);

  /** A shell ended by itself. The last one takes the terminal with it, as before tabs existed. */
  const shellExited = useCallback(
    (key: number) => {
      if (tabsRef.current.length <= 1) onCloseRef.current?.();
      else removeTab(key);
    },
    [removeTab]
  );

  const setIdentity = useCallback((key: number, identity: TerminalIdentity) => {
    setTabs((previous) => previous.map((tab) => (tab.key === key ? { ...tab, identity } : tab)));
  }, []);

  const activeTab = tabs.find((tab) => tab.key === activeKey) ?? tabs[0];

  const rows = useMemo((): Row[] => {
    const lines = source === 'mission' ? missionLog : source === 'driver' ? driverLog : [];
    const out: Row[] = [];
    lines.forEach((line, i) => {
      if ((line.at ?? 0) < clearedAt) return;
      line.text
        .replace(/\n$/, '')
        .split('\n')
        .forEach((part, k) => {
          if (part.length === 0) return;
          out.push({ key: `l${i}-${k}`, text: part, tone: toneOfLog(line.type, part) });
        });
    });
    const needle = filter.trim().toLowerCase();
    const filtered = needle ? out.filter((r) => r.text.toLowerCase().includes(needle)) : out;
    return filtered.length > ROW_LIMIT ? filtered.slice(filtered.length - ROW_LIMIT) : filtered;
  }, [source, missionLog, driverLog, clearedAt, filter]);

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
  }, [source]);

  const clearScreen = useCallback(() => {
    setClearedAt(Date.now());
    onClearLogs();
  }, [onClearLogs]);

  const followNow = () => {
    setAutoScroll(true);
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  };

  return (
    <div className="flex h-full min-h-0 flex-col p-3">
      <div
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-panel border border-[#0f2a36] shadow-2xl"
        style={{ background: TERMINAL_THEME.background }}
      >
        {/* Title bar. */}
        <div className="flex h-10 shrink-0 items-center gap-3 border-b border-[#0f2a36] bg-[#061219] px-3">
          <span
            data-terminal-title
            className="min-w-0 flex-1 truncate font-mono text-2xs text-frost/70"
          >
            {activeTab.identity ? terminalTitle(activeTab.identity) : 'bash'}
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

        {/* Toolbar: the log view only; the shell below is untouched by it. */}
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
              aria-label="Limpar os logs"
              title="Limpar os logs da tela"
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-haze transition-colors hover:text-frost"
            >
              <Trash2 size={11} />
              clear
            </button>
          </span>
        </div>

        {/* Logs, when shown: a pane of their own above the shell. */}
        {source !== 'none' ? (
          <div className="relative min-h-0 basis-[42%] border-b border-[#0f2a36]">
            <div
              ref={scrollRef}
              className="scroll-thin h-full overflow-y-auto px-4 py-3 font-mono text-[12px] leading-[1.5]"
            >
              {rows.length === 0 ? (
                <div className="text-haze-deep">(sem linhas de log)</div>
              ) : (
                rows.map((row) => (
                  <div key={row.key} className={cn('whitespace-pre-wrap break-words', TONE_CLASS[row.tone])}>
                    <Highlighted text={row.text} />
                  </div>
                ))
              )}
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
        ) : null}

        {/* Shell tabs: one PTY each, all kept alive while the overlay is open. */}
        <div
          role="tablist"
          aria-label="Sessões do terminal"
          className="flex h-8 shrink-0 items-stretch gap-px overflow-x-auto border-b border-[#0f2a36] bg-[#061219] font-mono text-3xs"
        >
          {tabs.map((tab, index) => {
            const selected = tab.key === activeTab.key;
            const label = tab.identity ? tabLabel(tab.identity) : 'bash';
            return (
              <div
                key={tab.key}
                className={cn(
                  'group flex min-w-[120px] max-w-[220px] items-center border-r border-[#0f2a36]',
                  selected ? 'bg-[#03090e] text-frost' : 'text-haze hover:bg-[#0a1b24] hover:text-frost'
                )}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  onClick={() => setActiveKey(tab.key)}
                  title={tab.identity ? terminalTitle(tab.identity) : undefined}
                  className="flex h-full min-w-0 flex-1 items-center gap-1.5 truncate px-3 text-left outline-none focus-visible:text-mint"
                >
                  <span className="text-haze-deep">{index + 1}</span>
                  <span className="truncate">{label}</span>
                </button>
                {tabs.length > 1 ? (
                  <button
                    type="button"
                    onClick={() => removeTab(tab.key)}
                    aria-label={`Fechar aba ${index + 1}`}
                    title="Fechar aba"
                    className={cn(
                      'mr-1.5 grid h-5 w-5 shrink-0 place-items-center rounded text-haze transition-colors hover:bg-frost/10 hover:text-frost',
                      !selected && 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'
                    )}
                  >
                    <X size={11} />
                  </button>
                ) : null}
              </div>
            );
          })}
          <button
            type="button"
            onClick={addTab}
            aria-label="Nova aba"
            title="Nova aba"
            className="grid w-8 shrink-0 place-items-center text-haze transition-colors hover:bg-[#0a1b24] hover:text-frost"
          >
            <Plus size={13} />
          </button>
        </div>

        {/* The shells. */}
        <div className="relative min-h-0 flex-1">
          {tabs.map((tab) => (
            <ShellPane
              key={tab.key}
              active={tab.key === activeTab.key}
              onLand={onLand}
              onExit={() => shellExited(tab.key)}
              onIdentity={(identity) => setIdentity(tab.key, identity)}
            />
          ))}
        </div>

        {/* Status line. */}
        <div className="flex h-6 shrink-0 items-center justify-between border-t border-[#0f2a36] bg-[#061219] px-3 font-mono text-[10px] text-haze-deep">
          <span>
            {source === 'none' ? 'logs ocultos' : `${rows.length} linhas de log`}
            {filter ? ` · filtro "${filter}"` : ''}
          </span>
          <span>land = pouso de emergência · exit fecha a aba · Tab completa · Ctrl+C interrompe</span>
        </div>
      </div>
    </div>
  );
};
