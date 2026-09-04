import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Alert,
  Button,
  Input,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import { BugOutlined, CopyOutlined, ReloadOutlined } from '@ant-design/icons';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

/* ───────── Constants ───────── */

const LOG_LINES = 300;
const REFRESH_MS = 3000;

type LevelFilter = 'all' | 'debug' | 'info' | 'warning' | 'error';

interface BackendLogsResponse {
  path: string;
  exists: boolean;
  lines: number;
  updated_at: number | null;
  size: number;
  content: string;
}

function levelTagColor(level: LevelFilter): string {
  if (level === 'error') return 'red';
  if (level === 'warning') return 'gold';
  if (level === 'info') return 'blue';
  if (level === 'debug') return 'geekblue';
  return 'default';
}

/** Color a log line based on its level (for the plain viewer). */
function lineLevel(line: string): LevelFilter | null {
  const up = line.toUpperCase();
  if (up.includes(' ERROR ') || up.includes('| ERROR ')) return 'error';
  if (up.includes(' WARNING ') || up.includes('| WARNING ')) return 'warning';
  if (up.includes(' DEBUG ') || up.includes('| DEBUG ')) return 'debug';
  if (up.includes(' INFO ') || up.includes('| INFO ')) return 'info';
  return null;
}

const levelColorMap: Record<string, string> = {
  error: 'var(--google-danger, #ef4444)',
  warning: '#b7791f',
  debug: 'var(--google-muted-foreground)',
  info: 'inherit',
};

/* ───────── Highlight helper ───────── */

function escapeRegExp(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function highlightLine(line: string, needle: string): ReactNode {
  const q = needle.trim();
  if (!q) return line;
  const re = new RegExp(escapeRegExp(q), 'ig');
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(line))) {
    const start = match.index;
    const end = start + match[0].length;
    if (start > lastIndex) parts.push(line.slice(lastIndex, start));
    parts.push(
      <mark
        key={`${start}-${end}`}
        style={{ background: '#fde68a', color: '#0e1115', borderRadius: 2, padding: '0 1px' }}
      >
        {line.slice(start, end)}
      </mark>,
    );
    lastIndex = end;
  }
  if (lastIndex < line.length) parts.push(line.slice(lastIndex));
  return parts;
}

/* ───────── Page ───────── */

export default function DebugPage() {
  const { t } = useI18n();

  const [logs, setLogs] = useState<BackendLogsResponse | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const firstFetchDone = useRef(false);
  const [loadError, setLoadError] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [newestFirst, setNewestFirst] = useState(true);
  const [level, setLevel] = useState<LevelFilter>('all');
  const [query, setQuery] = useState('');

  /* ── Fetch logs ── */
  const loadLogs = useCallback(
    async (opts?: { successToast?: boolean }) => {
      const isFirstFetch = !firstFetchDone.current;
      try {
        const res = await apiClient.get(`/debug/backend-logs?lines=${LOG_LINES}`);
        setLogs(res.data as BackendLogsResponse);
        setLoadError('');
        if (opts?.successToast) message.success(t('debug.refreshSuccess'));
      } catch (error) {
        const msg = error instanceof Error ? error.message : t('debug.loadFailed');
        setLoadError(msg);
        if (opts?.successToast) message.error(msg);
      } finally {
        if (isFirstFetch) {
          firstFetchDone.current = true;
          setInitialLoading(false);
        }
      }
    },
    [t],
  );

  /* ── Initial load ── */
  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  /* ── Auto-refresh polling ── */
  useEffect(() => {
    if (!autoRefresh) return;
    let cancelled = false;
    let timeoutId: number | undefined;

    const tick = async () => {
      if (cancelled) return;
      await loadLogs();
      if (cancelled) return;
      timeoutId = window.setTimeout(() => void tick(), REFRESH_MS);
    };

    timeoutId = window.setTimeout(() => void tick(), REFRESH_MS);
    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [autoRefresh, loadLogs]);

  /* ── Filter + sort ── */
  const lines = useMemo(() => {
    const raw = logs?.content || '';
    if (!raw.trim()) return [] as string[];
    const split = raw.split('\n');
    return newestFirst ? [...split].reverse() : split;
  }, [logs?.content, newestFirst]);

  const filteredLines = useMemo(() => {
    const q = query.trim().toLowerCase();
    return lines.filter((line) => {
      if (level !== 'all') {
        const lvl = level.toUpperCase();
        const levelHit =
          line.includes(` ${lvl} `) || line.includes(`| ${lvl} `) || line.includes(`[${lvl}`);
        if (!levelHit) return false;
      }
      if (!q) return true;
      return line.toLowerCase().includes(q);
    });
  }, [lines, level, query]);

  /* ── Copy ── */
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(filteredLines.join('\n'));
      message.success(t('debug.copied'));
    } catch {
      message.error(t('debug.copyFailed'));
    }
  }, [filteredLines, t]);

  const updatedAtText = useMemo(() => {
    if (!logs?.updated_at) return '';
    return new Date(logs.updated_at * 1000).toLocaleString();
  }, [logs?.updated_at]);

  return (
    <div>
      <GooglePageHeader
        icon={<BugOutlined />}
        title={t('menu.debug')}
        subtitle={t('debug.desc')}
      />

      <GoogleCard
        title={t('debug.backendTitle')}
        extra={
          <Space size="middle">
            <Text type="secondary" style={{ fontSize: 13 }}>{t('debug.newestFirst')}</Text>
            <Switch size="small" checked={newestFirst} onChange={setNewestFirst} />
            <Text type="secondary" style={{ fontSize: 13 }}>{t('debug.autoRefresh')}</Text>
            <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
          </Space>
        }
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {/* Toolbar */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            <Select<LevelFilter>
              value={level}
              onChange={setLevel}
              style={{ width: 130 }}
              options={[
                { value: 'all', label: t('debug.levelAll') },
                { value: 'error', label: <Tag color={levelTagColor('error')}>ERROR</Tag> },
                { value: 'warning', label: <Tag color={levelTagColor('warning')}>WARNING</Tag> },
                { value: 'info', label: <Tag color={levelTagColor('info')}>INFO</Tag> },
                { value: 'debug', label: <Tag color={levelTagColor('debug')}>DEBUG</Tag> },
              ]}
            />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('debug.searchPlaceholder')}
              allowClear
              style={{ flex: 1, minWidth: 180 }}
            />
            {updatedAtText && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('debug.updatedAt')}: {updatedAtText}
              </Text>
            )}
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void loadLogs({ successToast: true })}
            >
              {t('common.refresh')}
            </Button>
            <Button icon={<CopyOutlined />} onClick={() => void handleCopy()}>
              {t('debug.copy')}
            </Button>
          </div>

          {/* Log file path */}
          {logs?.path && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>{t('debug.logPath')}</Text>
              <code
                style={{
                  fontSize: 12,
                  background: 'var(--google-muted)',
                  padding: '2px 8px',
                  borderRadius: 4,
                }}
              >
                {logs.path}
              </code>
            </div>
          )}

          {/* Errors / not-found */}
          {loadError ? (
            <Alert message={loadError} type="error" showIcon />
          ) : !logs?.exists && !initialLoading ? (
            <Alert message={t('debug.notFound')} type="warning" showIcon />
          ) : null}

          {/* Log viewer */}
          <Spin spinning={initialLoading} tip={t('common.loading')}>
            <div
              style={{
                maxHeight: 520,
                overflow: 'auto',
                background: 'var(--google-muted)',
                border: '1px solid var(--google-border)',
                borderRadius: 8,
                padding: '12px 16px',
                fontFamily: 'var(--google-font-mono, "JetBrains Mono", monospace)',
                fontSize: 12.5,
                lineHeight: 1.7,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              }}
            >
              {filteredLines.length ? (
                filteredLines.map((line, idx) => {
                  const lvl = lineLevel(line);
                  return (
                    <div key={idx} style={{ color: lvl ? levelColorMap[lvl] : undefined }}>
                      {highlightLine(line, query)}
                    </div>
                  );
                })
              ) : (
                <Text type="secondary">{t('debug.placeholder')}</Text>
              )}
            </div>
          </Spin>
        </Space>
      </GoogleCard>
    </div>
  );
}
