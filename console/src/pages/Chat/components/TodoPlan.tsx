import { Progress, Spin, Tag, Tooltip, Typography } from 'antd';
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  LoadingOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons';
import {
  formatTimestamp,
  type Delegation,
  type DelegationRecord,
  type TodoItem,
} from '../../../stores/chatStore';
import { useI18n } from '../../../i18n';

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

export function DelegationBubble({
  delegation,
  running,
  onClick,
}: {
  delegation: Delegation;
  running?: boolean;
  onClick?: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '7px 14px',
        background: '#fffbe6',
        borderRadius: 10,
        marginBottom: 8,
        border: '1px solid #ffe58f',
        cursor: onClick ? 'pointer' : 'default',
        transition: onClick ? 'box-shadow 0.2s' : undefined,
      }}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter') onClick(); } : undefined}
    >
      <span
        style={{
          width: 22,
          height: 22,
          borderRadius: 6,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#fff7e6',
          color: '#d48806',
          fontSize: 13,
          flexShrink: 0,
        }}
      >
        <RobotOutlined />
      </span>
      <div style={{ lineHeight: 1.5 }}>
        <Text style={{ fontSize: 13, color: '#874d00' }}>
          {t('chat.delegatedTo', { agent: delegation.subagent })}
        </Text>
        {delegation.description && (
          <div>
            <Text type="secondary" style={{ fontSize: 12, color: '#ad6800' }}>
              {delegation.description}
            </Text>
          </div>
        )}
      </div>
      {running && (
        <Tag
          icon={<Spin size="small" style={{ fontSize: 10 }} />}
          color="gold"
          style={{ marginInlineEnd: 0, fontSize: 11 }}
        >
          {t('chat.subagentRunning')}
        </Tag>
      )}
    </div>
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
                  background: '#faad14',
                  marginTop: 6,
                  boxShadow: '0 0 0 3px #fffbe6',
                }}
              />
              {i < arr.length - 1 && (
                <span style={{ width: 2, flex: 1, background: '#f0f0f0', margin: '2px 0' }} />
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
