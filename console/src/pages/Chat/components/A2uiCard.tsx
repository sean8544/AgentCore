import { useEffect, useMemo, useRef, useState } from 'react';
import { AppstoreOutlined } from '@ant-design/icons';
import { MessageProcessor, NodeResolver, getValue } from '@a2ui/web_core/v0_9';
import type { A2uiClientAction, SurfaceModel } from '@a2ui/web_core/v0_9';
import { A2uiSurface, basicCatalog, MarkdownContext } from '@a2ui/react/v0_9';
import { renderMarkdown } from '@a2ui/markdown-it';
import type { A2uiSurfacePayload } from '../../../stores/chatStore';
import { useI18n } from '../../../i18n';
import '../../../styles/a2ui.css';

/**
 * Custom surface renderer that avoids the useSyncExternalStore + Preact effect()
 * timing issue. Creates a NodeResolver directly and uses React state to track
 * the root node, calling onChange synchronously when the root is built.
 */
function A2uiSurfaceSync({ surface }: { surface: SurfaceModel<any> }) {
  const resolverRef = useRef<NodeResolver<any> | null>(null);
  const [, forceUpdate] = useState(0);

  useEffect(() => {
    try {
      const resolver = new NodeResolver(surface, surface.catalog);
      resolverRef.current = resolver;

      const stop = (() => {
        let prev = getValue(resolver.rootNode);
        const interval = setInterval(() => {
          const curr = getValue(resolver.rootNode);
          if (curr !== prev) {
            prev = curr;
            forceUpdate((n) => n + 1);
          }
        }, 16);
        if (getValue(resolver.rootNode)) {
          forceUpdate((n) => n + 1);
        }
        return () => clearInterval(interval);
      })();

      return () => {
        stop();
        resolver.dispose();
        resolverRef.current = null;
      };
    } catch (e) {
      console.error('[A2uiSurfaceSync] Error creating resolver:', e);
      return () => { resolverRef.current = null; };
    }
  }, [surface]);

  const root = resolverRef.current ? getValue(resolverRef.current.rootNode) : null;
  if (!root) {
    return <div style={{ fontSize: 12.5, color: 'var(--google-muted-foreground)' }}>Loading...</div>;
  }

  // Use the standard A2uiSurface for actual rendering once root is resolved
  return (
    <MarkdownContext.Provider value={renderMarkdown}>
      <A2uiSurface surface={surface} />
    </MarkdownContext.Provider>
  );
}

/**
 * Renders one A2UI interactive surface projected by the agent's
 * ``send_a2ui`` tool call.  The envelope sequence is fed to the official
 * ``@a2ui/react`` MessageProcessor (basic catalog); user actions are
 * forwarded to the chat as a new message via ``onAction``.
 */
export default function A2uiCard({
  payload,
  disabled,
  onAction,
}: {
  payload: A2uiSurfacePayload;
  /** disable further interaction (e.g. while the agent is streaming) */
  disabled?: boolean;
  onAction?: (action: A2uiClientAction) => void;
}) {
  const { t } = useI18n();
  const [surfaces, setSurfaces] = useState<SurfaceModel<any>[]>([]);
  const [error, setError] = useState<string | null>(null);

  // The MessageProcessor is memoized per payload, so its action handler
  // would capture a stale ``disabled``/``onAction`` closure from the first
  // render (while streaming).  Route through refs so clicks always see the
  // latest values.
  const disabledRef = useRef(disabled);
  disabledRef.current = disabled;
  const onActionRef = useRef(onAction);
  onActionRef.current = onAction;

  // One processor per surface payload; rebuild only when the payload
  // (or its action callback) changes.
  const processor = useMemo(() => {
    try {
      const p = new MessageProcessor([basicCatalog], (action) => {
        if (!disabledRef.current) onActionRef.current?.(action);
      });
      p.processMessages(payload.messages as never[]);
      return p;
    } catch (e) {
      console.error('[A2uiCard] Processor error:', e);
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload.surface_id, payload.messages]);

  useEffect(() => {
    if (!processor) return;
    const sync = () =>
      setSurfaces(Array.from(processor.model.surfacesMap.values()));
    sync();
    const created = processor.onSurfaceCreated(sync);
    const deleted = processor.onSurfaceDeleted(sync);
    return () => {
      created.unsubscribe();
      deleted.unsubscribe();
    };
  }, [processor]);

  // Force a microtask-delayed re-sync so A2uiSurface's useSyncExternalStore
  // picks up the root node even when the Preact effect() fires asynchronously.
  useEffect(() => {
    if (!processor) return;
    const t = setTimeout(() => {
      setSurfaces(Array.from(processor.model.surfacesMap.values()));
    }, 0);
    return () => clearTimeout(t);
  }, [processor]);

  return (
    <div
      data-testid="a2ui-card"
      style={{
        margin: 'var(--google-space-2) 0 var(--google-space-4)',
        border: '1px solid var(--google-border)',
        borderRadius: 'var(--google-radius-md)',
        background: 'var(--google-card, #fff)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          borderBottom: '1px solid var(--google-border)',
          background: 'color-mix(in srgb, var(--google-muted) 40%, transparent)',
          fontSize: 12,
          color: 'var(--google-muted-foreground)',
        }}
      >
        <AppstoreOutlined style={{ fontSize: 12 }} />
        {t('chat.a2uiCard')}
        {payload.title ? ` · ${payload.title}` : ''}
      </div>
      <div
        style={{
          padding: 'var(--google-space-3) var(--google-space-4)',
          opacity: disabled ? 0.65 : 1,
          pointerEvents: disabled ? 'none' : 'auto',
        }}
      >
        {error ? (
          <div style={{ fontSize: 12.5, color: '#ff4d4f' }}>
            {t('chat.a2uiRenderError')}: {error}
          </div>
        ) : surfaces.length === 0 ? (
          <div style={{ fontSize: 12.5, color: 'var(--google-muted-foreground)' }}>
            {t('chat.a2uiWaiting')}
          </div>
        ) : (
          surfaces.map((surface) => (
            <A2uiSurfaceSync key={surface.id} surface={surface} />
          ))
        )}
      </div>
    </div>
  );
}
