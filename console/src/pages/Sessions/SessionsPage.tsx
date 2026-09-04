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
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  DeleteOutlined,
  DownOutlined,
  ExclamationCircleOutlined,
  MessageOutlined,
  ReloadOutlined,
  UpOutlined,
} from '@ant-design/icons';
import { apiClient } from '../../api/client';
import { useAgentStore } from '../../stores/agentStore';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import TraceTimeline from './TraceTimeline';

const { Text } = Typography;

interface SessionInfo {
  session_id: string;
  agent_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  has_pending_approval?: boolean;
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
        padding: 'var(--google-space-4)',
        borderRadius: 'var(--google-radius-lg)',
        border: '1px solid var(--google-border)',
        background: isUser
          ? 'color-mix(in srgb, var(--google-primary) 6%, var(--google-muted))'
          : 'color-mix(in srgb, var(--google-chart-5) 6%, var(--google-muted))',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--google-space-3)', gap: 'var(--google-space-3)' }}>
        <Tag color={style.color} style={{ marginInlineEnd: 0 }}>{style.label}</Tag>
        <Text type="secondary" style={{ fontSize: 12, flexShrink: 0 }}>
          {formatTime(msg.timestamp)}
        </Text>
      </div>
      <Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13, lineHeight: 1.75 }}>
        {collapsible ? `${content.slice(0, 600)}…` : content}
      </Text>
      {collapsible && (
        <Button type="link" size="small" style={{ padding: 0, marginTop: 'var(--google-space-2)' }} onClick={() => setExpanded(true)}>
          <DownOutlined /> {t('sessions.expand')}
        </Button>
      )}
      {expanded && long && (
        <Button type="link" size="small" style={{ padding: 0, marginTop: 'var(--google-space-2)' }} onClick={() => setExpanded(false)}>
          <UpOutlined /> {t('sessions.collapse')}
        </Button>
      )}
      {msg.tool_calls && msg.tool_calls.length > 0 && (
        <div style={{ marginTop: 'var(--google-space-4)', display: 'flex', flexWrap: 'wrap', gap: 'var(--google-space-2)' }}>
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
  const agents = useAgentStore((s) => s.agents);
  const refreshAgents = useAgentStore((s) => s.refreshAgents);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [agentFilter, setAgentFilter] = useState<string | undefined>(selectedAgent);
  const [approvalFilter, setApprovalFilter] = useState<'all' | 'pending'>('all');
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);

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
    void refreshAgents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchSessions]);

  // 筛选条件变化后，之前勾选的会话可能已不在可见列表里，
  // 清空选择避免“删除所选”时误删当前视图中看不到的会话。
  useEffect(() => {
    setSelectedIds([]);
  }, [agentFilter, approvalFilter]);

  const agentOptions = useMemo(() => {
    // Only surface agents that still exist (per the agent list loaded by
    // the global store).  Sessions belonging to deleted agents are
    // filtered out at startup by the backend purge, but a stale browser
    // tab may still see them — this is the defensive last layer.
    const knownIds = new Set(agents.map((a) => a.agent_id));
    const ids = Array.from(
      new Set(
        sessions
          .map((s) => s.agent_id)
          .filter((id) => Boolean(id) && knownIds.has(id)),
      ),
    );
    if (selectedAgent && knownIds.has(selectedAgent) && !ids.includes(selectedAgent)) {
      ids.push(selectedAgent);
    }
    return ids.map((id) => ({ label: id, value: id }));
  }, [sessions, selectedAgent, agents]);

  const filteredSessions = useMemo(() => {
    let list = sessions;
    if (agentFilter) list = list.filter((s) => s.agent_id === agentFilter);
    if (approvalFilter === 'pending') list = list.filter((s) => s.has_pending_approval);
    return list;
  }, [sessions, agentFilter, approvalFilter]);

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

  const handleBatchDelete = async () => {
    if (selectedIds.length === 0) return;
    // 只删除当前筛选条件下可见的勾选项，防止残留的隐藏选择被误删
    const visibleIds = new Set(filteredSessions.map((s) => s.session_id));
    const toDelete = selectedIds.filter((id) => visibleIds.has(id));
    if (toDelete.length === 0) {
      setSelectedIds([]);
      return;
    }
    setBatchDeleting(true);
    try {
      const { data } = await apiClient.post<{ deleted?: string[]; not_found?: string[] }>(
        '/chat/sessions/batch-delete',
        { session_ids: toDelete },
      );
      const failed = data?.not_found ?? [];
      if (failed.length === 0) {
        message.success(t('sessions.batchDeleteSuccess', { count: toDelete.length }));
      } else {
        message.warning(
          t('sessions.batchDeletePartial', {
            count: toDelete.length,
            success: toDelete.length - failed.length,
            failed: failed.length,
            ids: failed.join('、'),
          }),
        );
      }
      if (activeSession && toDelete.includes(activeSession.session_id)) {
        setDrawerOpen(false);
        setActiveSession(null);
      }
      setSelectedIds([]);
      await fetchSessions();
    } catch (err) {
      console.error('Failed to batch delete sessions', err);
      message.error(t('sessions.deleteFailed'));
    } finally {
      setBatchDeleting(false);
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
      title: t('sessions.status'),
      key: 'status',
      width: 130,
      filters: [
        { text: t('sessions.filterPendingApproval'), value: 'pending' },
      ],
      onFilter: (value, record) =>
        value === 'pending' ? !!record.has_pending_approval : true,
      render: (_, record) =>
        record.has_pending_approval ? (
          <Tag color="purple" icon={<ExclamationCircleOutlined />}>
            {t('sessions.pendingApproval')}
          </Tag>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
        ),
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

  const headerExtra = (
    <Space>
      {selectedIds.length > 0 && (
        <Popconfirm
          title={t('sessions.batchDelete')}
          description={t('sessions.batchDeleteConfirm', { count: selectedIds.length })}
          okText={t('common.delete')}
          okButtonProps={{ danger: true }}
          cancelText={t('common.cancel')}
          onConfirm={handleBatchDelete}
        >
          <Button danger icon={<DeleteOutlined />} loading={batchDeleting}>
            {t('sessions.deleteSelected', { count: selectedIds.length })}
          </Button>
        </Popconfirm>
      )}
      <Select
        placeholder={t('sessions.filterByAgent')}
        allowClear
        style={{ width: 220 }}
        options={agentOptions}
        value={agentFilter}
        onChange={setAgentFilter}
        showSearch
      />
      <Select
        value={approvalFilter}
        onChange={setApprovalFilter}
        style={{ width: 160 }}
        options={[
          { label: t('sessions.filterAll'), value: 'all' },
          { label: t('sessions.filterPendingApproval'), value: 'pending' },
        ]}
      />
      <Button icon={<ReloadOutlined />} onClick={fetchSessions} loading={loading}>
        {t('common.refresh')}
      </Button>
    </Space>
  );

  return (
    <div>
      <GooglePageHeader
        icon={<MessageOutlined />}
        title={t('sessions.title')}
        extra={headerExtra}
      />

      <GoogleCard bodyStyle={{ padding: 0 }}>
        <Table<SessionInfo>
          columns={columns}
          dataSource={filteredSessions}
          rowKey="session_id"
          loading={loading}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys as string[]),
            preserveSelectedRowKeys: false,
          }}
          pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => t('sessions.pagination', { count: total }) }}
          locale={{ emptyText: <Empty description={t('sessions.noSessions')} /> }}
        />
      </GoogleCard>

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
          <GoogleCard style={{ marginBottom: 'var(--google-space-6)' }}>
            <div style={{ marginBottom: 'var(--google-space-2)' }}>
              <Text code>{activeSession.session_id}</Text>
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('sessions.createdInfo', { time: formatTime(activeSession.created_at), count: activeSession.message_count })}
            </Text>
          </GoogleCard>
        )}

        <Tabs
          items={[
            {
              key: 'messages',
              label: t('sessions.messagesTab'),
              children: historyLoading ? (
                <div style={{ textAlign: 'center', padding: 'var(--google-space-16)' }}>
                  <Spin />
                </div>
              ) : history.length === 0 ? (
                <Empty description={t('sessions.noMessages')} />
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--google-space-4)' }}>
                  {history.map((msg, idx) => (
                    <MessageBlock key={idx} msg={msg} />
                  ))}
                </div>
              ),
            },
            {
              key: 'trace',
              label: t('sessions.traceTab'),
              children: activeSession ? (
                <TraceTimeline sessionId={activeSession.session_id} />
              ) : null,
            },
          ]}
        />
      </Drawer>
    </div>
  );
}
