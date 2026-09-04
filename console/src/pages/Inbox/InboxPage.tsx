import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Checkbox,
  Empty,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  DeleteOutlined,
  InboxOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useAgentStore } from '../../stores/agentStore';
import { useInboxStore } from '../../stores/inboxStore';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

interface PendingSession {
  session_id: string;
  agent_id: string;
  updated_at: string;
  message_count: number;
  has_pending_approval?: boolean;
  has_expired_approval?: boolean;
}

function formatTime(iso: string): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export default function InboxPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const selectedAgent = useAgentStore((s) => s.selectedAgent);
  const [sessions, setSessions] = useState<PendingSession[]>([]);
  const [allSessions, setAllSessions] = useState<PendingSession[]>([]);
  const [loading, setLoading] = useState(false);
  // Default to ALL agents — pending approvals from background runs
  // (heartbeat / cron / other agents) must not be hidden by the global
  // agent selector; the filter stays opt-in.
  const [agentFilter, setAgentFilter] = useState<string | undefined>(undefined);

  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);

  const fetchPending = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await apiClient.get<PendingSession[]>('/chat/sessions');
      // Keep expired background approvals visible (marked as such) so the
      // user sees what the cleanup pass removed from the pending queue.
      const pending = (data ?? []).filter(
        (s) => s.has_pending_approval || s.has_expired_approval,
      );
      setAllSessions(pending);
      // Keep the sidebar badge in sync — same payload, no extra request.
      // Expired entries no longer count as unread.
      useInboxStore.getState().setPendingApprovalCount(
        pending.filter((s) => s.has_pending_approval).length,
      );
    } catch {
      setAllSessions([]);
      message.error(t('inbox.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchPending();
  }, [fetchPending]);

  useEffect(() => {
    if (agentFilter) {
      setSessions(allSessions.filter((s) => s.agent_id === agentFilter));
    } else {
      setSessions(allSessions);
    }
  }, [allSessions, agentFilter]);

  const handleBatchDelete = async () => {
    if (selectedIds.length === 0) return;
    setBatchDeleting(true);
    try {
      const { data } = await apiClient.post<{
        deleted?: string[];
        not_found?: string[];
      }>('/chat/sessions/batch-delete', { session_ids: selectedIds });
      const failed = data?.not_found ?? [];
      if (failed.length === 0) {
        message.success(t('inbox.batchDeleteSuccess', { count: selectedIds.length }));
      } else {
        message.warning(
          t('inbox.batchDeletePartial', {
            count: selectedIds.length,
            success: selectedIds.length - failed.length,
            failed: failed.length,
            ids: failed.join('、'),
          }),
        );
      }
      setSelectedIds([]);
      // Reload the list — also keeps the sidebar badge in sync.
      await fetchPending();
    } catch {
      message.error(t('inbox.deleteFailed'));
    } finally {
      setBatchDeleting(false);
    }
  };

  const handleGoToApproval = (session: PendingSession) => {
    // Navigate to Chat page for the agent, then select the session.
    navigate(`/agents/${session.agent_id}/chat`);
    // The chatStore will pick up the session after navigation.
    // We use a short delay to let the ChatPage mount and load sessions.
    setTimeout(() => {
      import('../../stores/chatStore').then(({ useChatStore }) => {
        useChatStore.getState().selectSession(session.session_id);
      });
    }, 500);
  };

  const agentOptions = (() => {
    const ids = Array.from(new Set(allSessions.map((s) => s.agent_id).filter(Boolean)));
    if (selectedAgent && !ids.includes(selectedAgent)) ids.push(selectedAgent);
    return ids.map((id) => ({ label: id, value: id }));
  })();

  const columns: ColumnsType<PendingSession> = [
    {
      title: 'Agent',
      dataIndex: 'agent_id',
      key: 'agent_id',
      width: 160,
      render: (id: string) => <Tag color="geekblue">{id || '-'}</Tag>,
    },
    {
      title: t('inbox.sessionId'),
      dataIndex: 'session_id',
      key: 'session_id',
      ellipsis: true,
      render: (id: string) => (
        <Text code copyable={{ text: id }} style={{ fontSize: 12 }}>
          {id}
        </Text>
      ),
    },
    {
      title: t('inbox.updatedAt'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 190,
      sorter: (a, b) => a.updated_at.localeCompare(b.updated_at),
      render: formatTime,
    },
    {
      title: t('inbox.source'),
      key: 'source',
      width: 140,
      render: (_, record) => {
        const sid = record.session_id;
        if (sid.startsWith('heartbeat-')) return <Tag color="blue">{t('inbox.sourceHeartbeat')}</Tag>;
        if (sid.startsWith('memory-consolidation-')) return <Tag color="green">{t('inbox.sourceMemory')}</Tag>;
        if (sid.startsWith('cron:')) return <Tag color="orange">{t('inbox.sourceCron')}</Tag>;
        return <Tag>{t('inbox.sourceChat')}</Tag>;
      },
    },
    {
      title: t('inbox.status'),
      key: 'status',
      width: 110,
      render: (_, record) =>
        record.has_pending_approval ? (
          <Tag color="green">{t('inbox.statusPending')}</Tag>
        ) : (
          <Tag>{t('inbox.statusExpired')}</Tag>
        ),
    },
    {
      title: t('common.actions'),
      key: 'actions',
      width: 140,
      render: (_, record) =>
        record.has_expired_approval ? (
          <Tooltip title={t('inbox.expiredHint')}>
            <Button type="primary" size="small" disabled>
              {t('inbox.statusExpired')}
            </Button>
          </Tooltip>
        ) : (
          <Button
            type="primary"
            size="small"
            icon={<ThunderboltOutlined />}
            onClick={() => handleGoToApproval(record)}
          >
            {t('inbox.goToApproval')}
          </Button>
        ),
    },
  ];

  const headerExtra = (
    <Space>
      {selectedIds.length > 0 && (
        <Popconfirm
          title={t('inbox.batchDelete')}
          description={t('inbox.batchDeleteConfirm', { count: selectedIds.length })}
          okText={t('common.delete')}
          okButtonProps={{ danger: true }}
          cancelText={t('common.cancel')}
          onConfirm={() => void handleBatchDelete()}
        >
          <Button danger icon={<DeleteOutlined />} loading={batchDeleting}>
            {t('inbox.deleteSelected', { count: selectedIds.length })}
          </Button>
        </Popconfirm>
      )}
      <Select
        placeholder={t('inbox.filterByAgent')}
        allowClear
        style={{ width: 200 }}
        options={agentOptions}
        value={agentFilter}
        onChange={setAgentFilter}
        showSearch
      />
      <Button icon={<ReloadOutlined />} onClick={() => void fetchPending()} loading={loading}>
        {t('common.refresh')}
      </Button>
    </Space>
  );

  return (
    <div>
      <GooglePageHeader
        icon={<InboxOutlined />}
        title={t('menu.inbox')}
        subtitle={t('inbox.subtitle')}
        extra={headerExtra}
      />

      <GoogleCard bodyStyle={{ padding: 0 }}>
        <Table<PendingSession>
          columns={columns}
          dataSource={sessions}
          rowKey="session_id"
          loading={loading}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys as string[]),
            columnTitle: (
              <Checkbox
                checked={sessions.length > 0 && selectedIds.length === sessions.length}
                indeterminate={
                  selectedIds.length > 0 && selectedIds.length < sessions.length
                }
                onChange={(e) =>
                  setSelectedIds(
                    e.target.checked ? sessions.map((s) => s.session_id) : [],
                  )
                }
              />
            ),
          }}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          locale={{ emptyText: <Empty description={t('inbox.empty')} /> }}
        />
      </GoogleCard>
    </div>
  );
}
