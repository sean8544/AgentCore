import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { DownOutlined } from '@ant-design/icons';

/* ────────────────────────────────────────────────────────────────
 * CollapsibleBadge
 *
 * A compact, click-to-expand pill used for tool-call cards and
 * subagent delegation bubbles.  Renders a header (icon + label +
 * chevron) and, when opened, a body that grows in with a smooth
 * max-height / opacity transition.
 *
 * Interaction contract:
 *   - whole header is a button (mouse click + Enter/Space)
 *   - `defaultOpen` may be flipped when a transient state (e.g.
 *     streaming) starts; the auto-collapse on `streaming=false`
 *     is the caller's responsibility (see ToolCallCard)
 *   - body mounts lazily on first open so a long message with
 *     dozens of tool calls does not pay the render cost for
 *     content nobody expanded.
 * ──────────────────────────────────────────────────────────────── */

export interface CollapsibleBadgeProps {
  /** Small square icon on the left — usually an Ant Design icon element. */
  icon: ReactNode;
  /** Main label text next to the icon (tool name / "delegated to X"). */
  label: ReactNode;
  /** Foreground color (icon, border, chevron). */
  color: string;
  /** Header background (very light tint of `color`). */
  bg: string;
  /** Optional trailing element inside the header (spinner tag, badge…). */
  trailing?: ReactNode;
  /** Expanded body — pass `null` to disable expansion entirely. */
  children?: ReactNode;
  /** Initial open state (default false). */
  defaultOpen?: boolean;
  /**
   * Transient auto-expand signal: opens the body when it flips to true
   * and collapses again on false (e.g. a live feed worth watching while
   * running).  Manual toggling still works afterwards — only changes of
   * this prop drive the state.
   */
  autoOpen?: boolean;
}

export default function CollapsibleBadge({
  icon,
  label,
  color,
  bg,
  trailing,
  children,
  defaultOpen = false,
  autoOpen,
}: CollapsibleBadgeProps) {
  const bodyId = useId();
  const hasBody = children != null && children !== false;
  const [open, setOpen] = useState(defaultOpen && hasBody);
  const [everOpened, setEverOpened] = useState(defaultOpen && hasBody);
  const [hover, setHover] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [bodyHeight, setBodyHeight] = useState<number>(0);

  // Live-state auto expand/collapse (see `autoOpen` docs).
  useEffect(() => {
    if (autoOpen === undefined || !hasBody) return;
    setOpen(autoOpen);
    if (autoOpen) setEverOpened(true);
  }, [autoOpen, hasBody]);

  // If the body content changes size while open (e.g. streaming args),
  // re-measure so max-height stays correct.
  useEffect(() => {
    if (open && bodyRef.current) {
      setBodyHeight(bodyRef.current.scrollHeight);
    }
  }, [children, open]);

  const toggle = () => {
    if (!hasBody) return;
    setOpen((v) => {
      const next = !v;
      if (next) setEverOpened(true);
      return next;
    });
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      toggle();
    }
  };

  return (
    <div
      style={{
        display: 'inline-block',
        maxWidth: '100%',
        marginBottom: 6,
        marginRight: 6,
        verticalAlign: 'top',
      }}
    >
      <div
        role={hasBody ? 'button' : undefined}
        tabIndex={hasBody ? 0 : undefined}
        aria-expanded={hasBody ? open : undefined}
        aria-controls={hasBody ? bodyId : undefined}
        onClick={toggle}
        onKeyDown={hasBody ? onKeyDown : undefined}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '4px 10px 4px 8px',
          border: `1px solid ${color}${hover ? '55' : '33'}`,
          borderLeft: `3px solid ${color}`,
          borderTopRightRadius: 8,
          borderBottomRightRadius: open && everOpened ? 0 : 8,
          borderTopLeftRadius: 8,
          borderBottomLeftRadius: open && everOpened ? 0 : 8,
          background: bg,
          fontSize: 12,
          lineHeight: 1.4,
          cursor: hasBody ? 'pointer' : 'default',
          transition: 'box-shadow 0.15s ease, border-color 0.15s ease',
          boxShadow: hover && hasBody ? '0 1px 3px rgba(0,0,0,0.06)' : 'none',
          userSelect: 'none',
          whiteSpace: 'nowrap',
        }}
      >
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            color,
            fontSize: 14,
            flexShrink: 0,
            lineHeight: 1,
          }}
        >
          {icon}
        </span>
        <span
          style={{
            fontFamily:
              "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
            fontSize: 12,
            color,
            whiteSpace: 'nowrap',
          }}
        >
          {label}
        </span>
        {trailing}
        {hasBody && (
          <DownOutlined
            style={{
              fontSize: 9,
              color: hover ? color : `${color}99`,
              marginLeft: 2,
              transform: open ? 'rotate(180deg)' : 'none',
              transition: 'transform 0.18s ease, color 0.15s ease',
            }}
          />
        )}
      </div>

      {everOpened && (
        <div
          id={bodyId}
          ref={bodyRef}
          style={{
            maxHeight: open ? bodyHeight || 480 : 0,
            opacity: open ? 1 : 0,
            overflow: open ? 'auto' : 'hidden',
            transition: 'max-height 0.18s ease, opacity 0.15s ease',
            border: open ? `1px solid ${color}33` : 'none',
            borderTop: 'none',
            borderBottomLeftRadius: 8,
            borderBottomRightRadius: 8,
            background: '#fff',
            minWidth: '100%',
            maxWidth: 'min(520px, 78vw)',
            scrollbarWidth: 'thin',
          }}
        >
          <div style={{ padding: '8px 10px' }}>{children}</div>
        </div>
      )}
    </div>
  );
}
