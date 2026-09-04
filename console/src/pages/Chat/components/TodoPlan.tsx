import { useEffect, useRef } from 'react';
import { Button, Progress, Spin, Tag, Tooltip, Typography } from 'antd';
import {
  BulbOutlined,
  CheckCircleFilled,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleFilled,
  LoadingOutlined,
  RobotOutlined,
  RocketOutlined,
  SendOutlined,
} from '@ant-design/icons';
import {
  formatTimestamp,
  type Delegation,
  type DelegationActivity,
  type DelegationRecord,
  type TodoItem,
} from '../../../stores/chatStore';
import { useI18n } from '../../../i18n';
import CollapsibleBadge from './CollapsibleBadge';

const { Text } = Typography;

/* ───────── Todo status helpers ───────── */

const STATUS_META: Record<string, { color: string; bg: string; labelKey: string }> = {
  completed: { color: '#52c41a', bg: '#f6ffed', labelKey: 'chat.todoCompleted' },
  in_progress: { color: '#1677ff', bg: '#e6f4ff', labelKey: 'chat.todoInProgress' },
  pending: { color: '#bfbfbf', bg: '#fafafa', labelKey: 'chat.todoPending' },
};

function statusMeta(status?: string) {
  return STATUS_META[status ?? ''] ?? STATUS_META.pending;
}

function StatusIcon({ status }: { status?: string }) {
  const meta = statusMeta(status);
  if (status === 'completed') {
    return <CheckCircleFilled style={{ color: meta.color, fontSize: 15, marginTop: 1 }} />;
  }
  if (status === 'in_progress') {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center' }}>
        <LoadingOutlined style={{ color: meta.color, fontSize: 14 }} />
      </span>
    );
  }
  return <ClockCircleOutlined style={{ color: meta.color, fontSize: 14, marginTop: 1 }} />;
}

const PRIORITY_COLORS: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'default',
};

/* ───────── Todo list (shared by panel & message) ───────── */

export function TodoList({ todos }: { todos: TodoItem[] }) {
  const { t } = useI18n();
  const completed = todos.filter((td) => td.status === 'completed').length;
  const percent = todos.length ? Math.round((completed / todos.length) * 100) : 0;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <Text strong style={{ fontSize: 13, color: '#1a1a1a' }}>
          {'\u{1F4CB}'} {t('chat.todoPlan')}
        </Text>
        <Progress
          percent={percent}
          size="small"
          strokeColor="#52c41a"
          style={{ flex: 1, minWidth: 0, marginBottom: 0 }}
        />
        <Tag color="blue" style={{ marginInlineEnd: 0, fontSize: 11 }}>
          {completed}/{todos.length}
        </Tag>
      </div>
      <div>
        {todos.map((todo, i) => {
          const meta = statusMeta(todo.status);
          const title = todo.title ?? todo.content ?? '';
          return (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 10,
                padding: '7px 10px',
                borderRadius: 8,
                background: i % 2 === 0 ? '#fafafa' : '#fff',
                marginBottom: 2,
              }}
            >
              <Tooltip title={t(meta.labelKey)}>
                <span style={{ marginTop: 2, flexShrink: 0 }}>
                  <StatusIcon status={todo.status} />
                </span>
              </Tooltip>
              <div style={{ flex: 1, minWidth: 0 }}>
                <Text
                  style={{
                    fontSize: 13,
                    color: todo.status === 'completed' ? '#999' : '#333',
                    textDecoration: todo.status === 'completed' ? 'line-through' : 'none',
                    display: 'block',
                  }}
                >
                  {title}
                </Text>
                {todo.description && (
                  <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
                    {todo.description}
                  </Text>
                )}
              </div>
              {todo.priority && (
                <Tag
                  color={PRIORITY_COLORS[todo.priority] ?? 'default'}
                  style={{ marginInlineEnd: 0, fontSize: 11, flexShrink: 0 }}
                >
                  {todo.priority}
                </Tag>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ───────── Top-of-page todo panel ───────── */

export function TodoPanel({ todos }: { todos: TodoItem[] }) {
  return (
    <div
      style={{
        border: '1px solid #f0f0f0',
        borderRadius: 12,
        background: '#fff',
        padding: '12px 16px',
        marginBottom: 16,
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}
    >
      <TodoList todos={todos} />
    </div>
  );
}

/* ───────── Delegation bubble (inside a message) ───────── */

/** One row of the subagent live activity feed. */
function ActivityRow({ act }: { act: DelegationActivity }) {
  const { t } = useI18n();
  let icon = <LoadingOutlined style={{ color: '#1677ff' }} />;
  let text = act.name || '';
  switch (act.kind) {
    case 'started':
      icon = <RocketOutlined style={{ color: '#d48806' }} />;
      text = t('chat.subagentStarted');
      break;
    case 'thinking':
      icon = <BulbOutlined style={{ color: '#8c8c8c' }} />;
      text = t('chat.subagentThinking');
      break;
    case 'tool_done':
      icon = <CheckCircleOutlined style={{ color: '#52c41a' }} />;
      break;
    case 'completed':
      icon = <CheckCircleFilled style={{ color: '#52c41a' }} />;
      text = t('chat.subagentCompleted');
      break;
    case 'error':
      icon = <CloseCircleFilled style={{ color: '#ff4d4f' }} />;
      text = act.name ? `${act.name} — ${t('chat.subagentError')}` : t('chat.subagentError');
      break;
    case 'collapsed':
      icon = <span style={{ color: '#bfbfbf' }}>{'\u22EF'}</span>;
      text = t('chat.subagentCollapsed', { count: String(act.name ?? '').replace('+', '') });
      break;
  }
  const muted = act.kind === 'collapsed';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '1.5px 0', fontSize: 12 }}>
      <span style={{ width: 14, textAlign: 'center', flexShrink: 0, fontSize: 12 }}>{icon}</span>
      <span
        style={{
          fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
          color: muted ? '#bfbfbf' : '#555',
          wordBreak: 'break-all',
        }}
      >
        {text}
      </span>
    </div>
  );
}

/** Collapse consecutive duplicate ``thinking`` rows. */
function dedupeActivity(acts: DelegationActivity[]): DelegationActivity[] {
  const out: DelegationActivity[] = [];
  for (const a of acts) {
    const prev = out[out.length - 1];
    if (
      a.kind === 'thinking' && prev && prev.kind === 'thinking'
    ) {
      continue;
    }
    out.push(a);
  }
  return out;
}

/**
 * Delegation badge with a *live* subagent activity feed.
 *
 * The running state comes from real ``task``-tool lifecycle events
 * (``subagent_activity`` SSE channel), not from the parent stream flag.
 * While running the body auto-expands and auto-scrolls; the final feed
 * stays available afterwards via the persisted activity timeline.
 *
 * The whole pill toggles expand/collapse; a dedicated "open subagent
 * session" button lives in the expanded body so navigation is no
 * longer the default click target (previously a single misclick
 * would leave the current conversation).
 */
export function DelegationBubble({
  delegation,
  running,
  onOpenSession,
}: {
  delegation: Delegation;
  running?: boolean;
  onOpenSession?: () => void;
}) {
  const { t } = useI18n();
  const isRunning = delegation.running ?? !!running;
  const acts = dedupeActivity(delegation.activity ?? []);
  const feedRef = useRef<HTMLDivElement | null>(null);

  // Keep the live feed pinned to the latest step while running.
  useEffect(() => {
    if (isRunning && feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [acts.length, isRunning]);

  const lastTool = [...acts].reverse().find((a) => a.kind === 'tool' || a.kind === 'tool_done');
  const toolCount = acts.filter((a) => a.kind === 'tool').length;

  const body = (
    <div style={{ minWidth: 240 }}>
      {acts.length > 0 && (
        <div style={{ marginBottom: delegation.description ? 10 : 8 }}>
          <div
            style={{
              fontSize: 11,
              color: '#8c8c8c',
              marginBottom: 2,
              textTransform: 'uppercase',
              letterSpacing: 0.4,
            }}
          >
            {t('chat.subagentActivity')}
            {toolCount > 0 && ` · ${t('chat.subagentToolCount', { count: toolCount })}`}
          </div>
          <div
            ref={feedRef}
            style={{
              maxHeight: 168,
              overflowY: 'auto',
              scrollbarWidth: 'thin',
              background: '#fafafa',
              border: '1px solid #f0f0f0',
              borderRadius: 6,
              padding: '4px 8px',
            }}
          >
            {acts.map((a, i) => (
              <ActivityRow key={i} act={a} />
            ))}
          </div>
        </div>
      )}
      {delegation.description && (
        <div style={{ marginBottom: onOpenSession ? 10 : 0 }}>
          <div
            style={{
              fontSize: 11,
              color: '#8c8c8c',
              marginBottom: 2,
              textTransform: 'uppercase',
              letterSpacing: 0.4,
            }}
          >
            {t('chat.taskDescription')}
          </div>
          <div
            style={{
              fontSize: 12.5,
              color: '#333',
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {delegation.description}
          </div>
        </div>
      )}
      {onOpenSession && (
        <div style={{ textAlign: 'right' }}>
          <Tooltip title={t('chat.openSubagentSessionTip')}>
            <Button
              type="link"
              size="small"
              icon={<SendOutlined />}
              iconPosition="end"
              onClick={(e) => {
                e.stopPropagation();
                onOpenSession();
              }}
              style={{
                padding: '0 4px',
                fontSize: 12,
                height: 'auto',
                color: '#8c8c8c',
              }}
            >
              {t('chat.openSubagentSession', { agent: delegation.subagent })}
            </Button>
          </Tooltip>
        </div>
      )}
    </div>
  );

  return (
    <CollapsibleBadge
      icon={<RobotOutlined />}
      label={t('chat.delegatedTo', { agent: delegation.subagent })}
      color="#d48806"
      bg="#fffbe6"
      autoOpen={isRunning}
      trailing={
        isRunning ? (
          <Tag
            icon={<Spin size="small" style={{ fontSize: 10 }} />}
            color="gold"
            style={{ marginInlineEnd: 0, fontSize: 11, marginLeft: 4 }}
          >
            {lastTool?.name
              ? `${t('chat.subagentRunning')} · ${lastTool.name}`
              : t('chat.subagentRunning')}
          </Tag>
        ) : undefined
      }
    >
      {body}
    </CollapsibleBadge>
  );
}

/* ───────── Delegation record panel (timeline) ───────── */

export function DelegationRecordPanel({
  records,
}: {
  records: DelegationRecord[];
}) {
  const { t } = useI18n();
  return (
    <div
      style={{
        border: '1px solid #f0f0f0',
        borderRadius: 12,
        background: '#fff',
        padding: '12px 16px',
        marginBottom: 16,
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <SendOutlined style={{ color: '#d48806', fontSize: 14 }} />
        <Text strong style={{ fontSize: 13, color: '#1a1a1a' }}>
          {t('chat.delegationRecords')}
        </Text>
        <Tag color="gold" style={{ marginInlineEnd: 0, fontSize: 11 }}>
          {records.length}
        </Tag>
      </div>
      <div style={{ paddingLeft: 4 }}>
        {[...records].reverse().map((r, i, arr) => (
          <div
            key={i}
            style={{ display: 'flex', gap: 10, position: 'relative' }}
          >
            {/* timeline rail */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
              <span
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: '50%',
                  background: 'var(--google-chart-3)',
                  marginTop: 6,
                  boxShadow: '0 0 0 3px var(--google-secondary)',
                }}
              />
              {i < arr.length - 1 && (
                <span style={{ width: 2, flex: 1, background: 'var(--google-border)', margin: '2px 0' }} />
              )}
            </div>
            <div
              style={{
                flex: 1,
                minWidth: 0,
                paddingBottom: i < arr.length - 1 ? 14 : 2,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <Tag color="orange" style={{ marginInlineEnd: 0, fontSize: 11 }}>
                  {t('chat.delegatedFrom')}
                </Tag>
                <Text code style={{ fontSize: 12.5 }}>
                  {r.parent_agent_id ?? '—'}
                </Text>
                {r.timestamp && (
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {formatTimestamp(r.timestamp)}
                  </Text>
                )}
              </div>
              {r.task_description && (
                <Text style={{ fontSize: 13, color: '#555', display: 'block', marginTop: 2 }}>
                  {r.task_description}
                </Text>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
