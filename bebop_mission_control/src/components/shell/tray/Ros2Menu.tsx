import React from 'react';
import type { LinkReadiness } from '../../../types/bmg';
import { cn } from '../../../lib/format';
import { topicLabel, type Ros2Tone } from '../../../lib/trayState';
import { Ros2Glyph } from '../glyphs';
import { ScrambleText, SuccessCheck } from '../motion';
import { QsSubmenu } from './controls';

interface Ros2MenuProps {
  readiness: LinkReadiness | null;
  tone: Ros2Tone;
  driverRunning: boolean;
  /** Start is refused without an answering aircraft or while a bring-up runs. */
  canStart: boolean;
  /** The launch sequence is running (D6). */
  frozen: boolean;
  onStart: () => void;
  onStop: () => void;
}

/**
 * The eight essential topics (`REQUIRED_TOPICS` of `electron/main.cjs`, as
 * they arrive in `LinkReadiness.topics`), after the Bluetooth submenu of
 * Ubuntu. While the nodes come up each name churns, and it resolves with a tick
 * the moment its topic carries data; the title takes the tick once all eight
 * do. The foot starts or stops the driver.
 */
export const Ros2Menu: React.FC<Ros2MenuProps> = ({ readiness, tone, driverRunning, canStart, frozen, onStart, onStop }) => {
  const loading = tone === 'busy';
  const topics = readiness?.topics ?? [];

  return (
    <QsSubmenu
      id="ros2"
      title="ROS 2"
      active={tone === 'ready'}
      icon={<Ros2Glyph tone={tone} size={18} />}
      status={tone === 'ready' ? <SuccessCheck size={15} className="text-frost" /> : null}
      footer={
        driverRunning ? (
          <button type="button" onClick={onStop} disabled={frozen} className="qs-menu-row w-full text-[14px] text-frost disabled:opacity-50">
            Parar
          </button>
        ) : (
          <button
            type="button"
            onClick={onStart}
            disabled={frozen}
            className="qs-menu-row w-full text-[14px] text-frost disabled:opacity-50"
          >
            Iniciar
          </button>
        )
      }
    >
      {topics.length ? (
        <ul className="px-0.5" data-loading={loading || undefined}>
          {topics.map((topic) => {
            const scrambling = loading && !topic.receiving;
            return (
              <li
                key={topic.topic}
                data-topic={topic.topic}
                data-topic-state={topic.receiving ? 'receiving' : topic.present ? 'present' : 'absent'}
                className="flex h-8 items-center gap-3 px-3"
              >
                <ScrambleText
                  text={topicLabel(topic.topic)}
                  scrambling={scrambling}
                  className={cn(
                    'min-w-0 flex-1 truncate font-mono text-[12.5px] transition-colors duration-300',
                    topic.receiving ? 'text-frost' : scrambling ? 'text-frost/70' : 'text-frost/40'
                  )}
                />
                <span className="flex w-4 justify-end">{topic.receiving ? <SuccessCheck size={13} className="text-frost" /> : null}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="px-3 py-3 text-[13px] text-frost/60">{loading ? 'Iniciando os nós' : 'Driver parado'}</p>
      )}
    </QsSubmenu>
  );
};
