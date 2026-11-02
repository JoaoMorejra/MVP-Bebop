import React, { useCallback, useRef } from 'react';
import { cn } from '../../lib/format';

/**
 * Where on the edge of `box` the pointer at (`x`, `y`) crossed it, as the two
 * percentages the fill grows from: the nearest of the four edges, at the
 * pointer's position along it (kinetics "directional hover").
 */
export function nearestEdgePoint(box: { left: number; top: number; width: number; height: number }, x: number, y: number): [string, string] {
  const px = x - box.left;
  const py = y - box.top;
  if (!(box.width > 0) || !(box.height > 0)) return ['50%', '50%'];
  const distances = [px, box.width - px, py, box.height - py];
  const edge = distances.indexOf(Math.min(...distances));
  const along = (value: number, size: number) => `${((Math.min(size, Math.max(0, value)) / size) * 100).toFixed(1)}%`;
  if (edge === 0) return ['0%', along(py, box.height)];
  if (edge === 1) return ['100%', along(py, box.height)];
  if (edge === 2) return [along(px, box.width), '0%'];
  return [along(px, box.width), '100%'];
}

export type DirectionalTone = 'ghost' | 'primary' | 'icon';

type DirectionalButtonProps = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'type'> & {
  tone?: DirectionalTone;
};

/**
 * A button whose hover fill enters from the side the pointer came in by and
 * leaves toward the side it goes out by: a circle clipped from that edge point
 * grows over the face on enter and shrinks back to it on leave. Keyboard focus
 * fills it from the centre.
 */
export const DirectionalButton = React.forwardRef<HTMLButtonElement, DirectionalButtonProps>(function DirectionalButton(
  { tone = 'ghost', className, children, onPointerEnter, onPointerLeave, onFocus, onBlur, ...rest },
  forwarded
) {
  const local = useRef<HTMLButtonElement | null>(null);
  const setRef = useCallback(
    (node: HTMLButtonElement | null) => {
      local.current = node;
      if (typeof forwarded === 'function') forwarded(node);
      else if (forwarded) forwarded.current = node;
    },
    [forwarded]
  );

  const aim = (event: React.PointerEvent<HTMLButtonElement>) => {
    const node = local.current;
    if (!node) return;
    const [x, y] = nearestEdgePoint(node.getBoundingClientRect(), event.clientX, event.clientY);
    node.style.setProperty('--edge-x', x);
    node.style.setProperty('--edge-y', y);
  };

  return (
    <button
      ref={setRef}
      type="button"
      {...rest}
      data-tone={tone}
      className={cn('dir-btn', className)}
      onPointerEnter={(event) => {
        aim(event);
        if (!event.currentTarget.disabled) event.currentTarget.dataset.hover = 'true';
        onPointerEnter?.(event);
      }}
      onPointerLeave={(event) => {
        aim(event);
        delete event.currentTarget.dataset.hover;
        onPointerLeave?.(event);
      }}
      onFocus={(event) => {
        event.currentTarget.style.setProperty('--edge-x', '50%');
        event.currentTarget.style.setProperty('--edge-y', '50%');
        onFocus?.(event);
      }}
      onBlur={onBlur}
    >
      <span className="dir-btn-fill" aria-hidden />
      <span className="dir-btn-face">{children}</span>
    </button>
  );
});
