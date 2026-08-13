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
import { DeleteOutlined, MessageOutlined, ReloadOutlined } from '@ant-design/icons';
import { apiClient } from '../../api/client';
import { useAgentStore } from '../../stores/agentStore';

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

const roleStyle: Record<string, { color: string; label: string }> = {
  user: { color: 'blue', label: 'User' },
  assistant: { color: 'green', label: 'Assistant' },
  system: { color: 'orange', label: 'System' },
  tool: { color: 'purple', label: 'Tool' },
};

export default function SessionsPage() {
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
      message.error('加载会话列表失败');
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
      message.error('加载消息历史失败');
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleDelete = async (sessionId: string) => {
    setDeletingId(sessionId);
    try {
      await apiClient.delete(`/chat/sessions/${sessionId}`);
      message.success('会话已删除');
      if (activeSession?.session_id === sessionId) {
        setDrawerOpen(false);
        setActiveSession(null);
      }
      await fetchSessions();
    } catch (err) {
      console.error('Failed to delete session', err);
      message.error('删除会话失败');
    } finally {
      setDeletingId(null);
    }
  };

  const columns: ColumnsType<SessionInfo> = [
    {
      title: '会话 ID',
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
      title: '消息数',
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
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 190,
      sorter: (a, b) => a.created_at.localeCompare(b.created_at),
      render: formatTime,
    },
    {
      title: '最近更新',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 190,
      sorter: (a, b) => a.updated_at.localeCompare(b.updated_at),
      render: formatTime,
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => openDetail(record)}>
            查看详情
          </Button>
          <Popconfirm
            title="删除会话"
            description="将同时删除该会话的全部消息记录，确定删除？"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => handleDelete(record.session_id)}
          >
            <Tooltip title="删除会话">
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
          会话管理
        </Title>
        <Space>
          <Select
            placeholder="按 Agent 筛选"
            allowClear
            style={{ width: 220 }}
            options={agentOptions}
            value={agentFilter}
            onChange={setAgentFilter}
            showSearch
          />
          <Button icon={<ReloadOutlined />} onClick={fetchSessions} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      <Table<SessionInfo>
        columns={columns}
        dataSource={filteredSessions}
        rowKey="session_id"
        loading={loading}
        pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (t) => `共 ${t} 个会话` }}
        locale={{ emptyText: <Empty description="暂无会话" /> }}
      />

      <Drawer
        title="会话详情"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={640}
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
              创建于 {formatTime(activeSession.created_at)} · 共 {activeSession.message_count} 条消息
            </Text>
          </div>
        )}

        {historyLoading ? (
          <div style={{ textAlign: 'center', padding: 48 }}>
            <Spin />
          </div>
        ) : history.length === 0 ? (
          <Empty description="暂无消息" />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {history.map((msg, idx) => {
              const style = roleStyle[msg.role] ?? { color: 'default', label: msg.role };
              const isUser = msg.role === 'user';
              return (
                <div
                  key={idx}
                  style={{
                    padding: 12,
                    borderRadius: 8,
                    border: '1px solid rgba(5, 5, 5, 0.06)',
                    background: isUser ? 'rgba(22, 119, 255, 0.04)' : 'rgba(82, 196, 26, 0.04)',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Tag color={style.color}>{style.label}</Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {formatTime(msg.timestamp)}
                    </Text>
                  </div>
                  <Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {msg.content || '(空消息)'}
                  </Text>
                  {msg.tool_calls && msg.tool_calls.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      {msg.tool_calls.map((tc, i) => (
                        <Tag key={tc.id || i} color="purple" style={{ marginBottom: 4 }}>
                          工具调用: {tc.name}
                        </Tag>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Drawer>
    </div>
  );
}
