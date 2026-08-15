import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Drawer,
  Empty,
  message,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  DeleteOutlined,
  DownOutlined,
  MessageOutlined,
  ReloadOutlined,
  UpOutlined,
} from '@ant-design/icons';
import { apiClient } from '../../api/client';
import { useAgentStore } from '../../stores/agentStore';
import { useI18n } from '../../i18n';

const { Title, Text } = Typography;

interface SessionInfo {
  session_id: string;
  agent_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

interface ChatMessage {
  role: string;
  content: string;
  timestamp: string;
  tool_calls?: { name: string; args: Record<string, unknown>; id: string }[];
}

function formatTime(iso: string): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/**
 * Clean token-boundary newlines left in legacy streamed replies (each
 * token chunk ended with ``\n``, so words ended up on separate lines).
 * Collapse a single newline between two non-newline characters into a
 * space; real paragraphs (blank-line separated) are preserved.
 */
function normalizeText(text: string): string {
  // \u000a is a newline — written this way to survive JSON escaping.
  // Lookahead keeps the next character unconsumed so adjacent token
  // boundaries ("a\nb\nc") are all collapsed in one pass.
  const reSingle = /([^\u000a])\u000a(?=[^\u000a])/g;
  const reMany = /\u000a{3,}/g;
  return text.replace(reSingle, '$1 ').replace(reMany, '\u000a\u000a');
}

const roleStyle: Record<string, { color: string; label: string }> = {
  user: { color: 'blue', label: 'User' },
  assistant: { color: 'green', label: 'Assistant' },
  system: { color: 'orange', label: 'System' },
  tool: { color: 'purple', label: 'Tool' },
};

/** Message bubble with collapsible body for very long replies. */
function MessageBlock({ msg }: { msg: ChatMessage }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const style = roleStyle[msg.role] ?? { color: 'default', label: msg.role };
  const isUser = msg.role === 'user';
  const content = normalizeText(msg.content || '');
  const long = content.length > 600;
  const collapsible = long && !expanded;

  return (
    <div
      style={{
        padding: '12px 14px',
        borderRadius: 10,
        border: '1px solid rgba(5, 5, 5, 0.06)',
        background: isUser ? 'rgba(22, 119, 255, 0.05)' : 'rgba(82, 196, 26, 0.05)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 8 }}>
        <Tag color={style.color} style={{ marginInlineEnd: 0 }}>{style.label}</Tag>
        <Text type="secondary" style={{ fontSize: 12, flexShrink: 0 }}>
          {formatTime(msg.timestamp)}
        </Text>
      </div>
      <Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13, lineHeight: 1.75 }}>
        {collapsible ? `${content.slice(0, 600)}…` : content}
      </Text>
      {collapsible && (
        <Button type="link" size="small" style={{ padding: 0, marginTop: 4 }} onClick={() => setExpanded(true)}>
          <DownOutlined /> {t('sessions.expand')}
        </Button>
      )}
      {expanded && long && (
        <Button type="link" size="small" style={{ padding: 0, marginTop: 4 }} onClick={() => setExpanded(false)}>
          <UpOutlined /> {t('sessions.collapse')}
        </Button>
      )}
      {msg.tool_calls && msg.tool_calls.length > 0 && (
        <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {msg.tool_calls.map((tc, i) => (
            <Tag key={tc.id || i} color="purple" style={{ marginInlineEnd: 0, marginBottom: 0 }}>
              {t('sessions.toolCall', { name: tc.name })}
            </Tag>
          ))}
        </div>
      )}
    </div>
  );
}

export default function SessionsPage() {
  const { t } = useI18n();
  const selectedAgent = useAgentStore((s) => s.selectedAgent);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [agentFilter, setAgentFilter] = useState<string | undefined>(selectedAgent);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeSession, setActiveSession] = useState<SessionInfo | null>(null);
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await apiClient.get<SessionInfo[]>('/chat/sessions');
      setSessions(data ?? []);
    } catch (err) {
      console.error('Failed to load sessions', err);
      message.error(t('sessions.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const agentOptions = useMemo(() => {
    const ids = Array.from(new Set(sessions.map((s) => s.agent_id).filter(Boolean)));
    if (selectedAgent && !ids.includes(selectedAgent)) ids.push(selectedAgent);
    return ids.map((id) => ({ label: id, value: id }));
  }, [sessions, selectedAgent]);

  const filteredSessions = useMemo(
    () => (agentFilter ? sessions.filter((s) => s.agent_id === agentFilter) : sessions),
    [sessions, agentFilter],
  );

  const openDetail = async (session: SessionInfo) => {
    setActiveSession(session);
    setDrawerOpen(true);
    setHistory([]);
    setHistoryLoading(true);
    try {
      const { data } = await apiClient.get<ChatMessage[]>('/chat/history', {
        params: { session_id: session.session_id, limit: 200 },
      });
      setHistory(data ?? []);
    } catch (err) {
      console.error('Failed to load history', err);
      message.error(t('sessions.historyLoadFailed'));
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleDelete = async (sessionId: string) => {
    setDeletingId(sessionId);
    try {
      await apiClient.delete(`/chat/sessions/${sessionId}`);
      message.success(t('sessions.deletedSuccess'));
      if (activeSession?.session_id === sessionId) {
        setDrawerOpen(false);
        setActiveSession(null);
      }
      await fetchSessions();
    } catch (err) {
      console.error('Failed to delete session', err);
      message.error(t('sessions.deleteFailed'));
    } finally {
      setDeletingId(null);
    }
  };

  const columns: ColumnsType<SessionInfo> = [
    {
      title: t('sessions.sessionId'),
      dataIndex: 'session_id',
      key: 'session_id',
      ellipsis: true,
      render: (id: string) => (
        <Text code copyable={{ text: id }}>
          {id}
        </Text>
      ),
    },
    {
      title: 'Agent',
      dataIndex: 'agent_id',
      key: 'agent_id',
      width: 200,
      render: (id: string) => <Tag color="geekblue">{id || '-'}</Tag>,
    },
    {
      title: t('sessions.messageCount'),
      dataIndex: 'message_count',
      key: 'message_count',
      width: 110,
      align: 'center',
      sorter: (a, b) => a.message_count - b.message_count,
      render: (count: number) => (
        <Space size={4}>
          <MessageOutlined />
          {count}
        </Space>
      ),
    },
    {
      title: t('sessions.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 190,
      sorter: (a, b) => a.created_at.localeCompare(b.created_at),
      render: formatTime,
    },
    {
      title: t('sessions.updatedAt'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 190,
      sorter: (a, b) => a.updated_at.localeCompare(b.updated_at),
      render: formatTime,
    },
    {
      title: t('common.actions'),
      key: 'actions',
      width: 160,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => openDetail(record)}>
            {t('sessions.viewDetail')}
          </Button>
          <Popconfirm
            title={t('sessions.deleteSession')}
            description={t('sessions.deleteConfirm')}
            okText={t('common.delete')}
            okButtonProps={{ danger: true }}
            cancelText={t('common.cancel')}
            onConfirm={() => handleDelete(record.session_id)}
          >
            <Tooltip title={t('sessions.deleteSession')}>
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={deletingId === record.session_id}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          {t('sessions.title')}
        </Title>
        <Space>
          <Select
            placeholder={t('sessions.filterByAgent')}
            allowClear
            style={{ width: 220 }}
            options={agentOptions}
            value={agentFilter}
            onChange={setAgentFilter}
            showSearch
          />
          <Button icon={<ReloadOutlined />} onClick={fetchSessions} loading={loading}>
            {t('common.refresh')}
          </Button>
        </Space>
      </div>

      <Table<SessionInfo>
        columns={columns}
        dataSource={filteredSessions}
        rowKey="session_id"
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => t('sessions.pagination', { count: total }) }}
        locale={{ emptyText: <Empty description={t('sessions.noSessions')} /> }}
      />

      <Drawer
        title={t('sessions.sessionDetail')}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={720}
        destroyOnHidden
        extra={
          activeSession ? (
            <Tag color="geekblue">{activeSession.agent_id}</Tag>
          ) : undefined
        }
      >
        {activeSession && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ marginBottom: 4 }}>
              <Text code>{activeSession.session_id}</Text>
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('sessions.createdInfo', { time: formatTime(activeSession.created_at), count: activeSession.message_count })}
            </Text>
          </div>
        )}

        {historyLoading ? (
          <div style={{ textAlign: 'center', padding: 48 }}>
            <Spin />
          </div>
        ) : history.length === 0 ? (
          <Empty description={t('sessions.noMessages')} />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {history.map((msg, idx) => (
              <MessageBlock key={idx} msg={msg} />
            ))}
          </div>
        )}
      </Drawer>
    </div>
  );
}
