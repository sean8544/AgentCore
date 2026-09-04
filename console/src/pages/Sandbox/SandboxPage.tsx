import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Popconfirm,
  Skeleton,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CloudServerOutlined,
  DeleteOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SyncOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  FolderOpenOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useParams, useNavigate } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { extractErrorMessage, formatDateTime } from '../../utils/helpers';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

/* ───────── Types ───────── */

interface SandboxSessionInfo {
  agent_id: string;
  sandbox_id: string;
  status: string;
  strategy: string;
  image?: string;
  created_at?: string;
  last_active_at?: string;
  timeout_remaining?: number;
  resume_count?: number;
  cpu_usage?: number;
  memory_usage?: number;
  memory_limit?: number;
}

interface SyncStatusInfo {
  enabled: boolean;
  container_alive?: boolean;
  container_root?: string;
  sandbox_id?: string;
  last_sync_at?: string;
  states?: Record<string, string>;
  summary?: {
    synced: number;
    local_modified: number;
    remote_modified: number;
    conflict: number;
    local_only: number;
    remote_only: number;
  };
}

/* ───────── Page ───────── */

export default function SandboxPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { agentId } = useParams<{ agentId: string }>();

  const [loading, setLoading] = useState(false);
  const [session, setSession] = useState<SandboxSessionInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Logs
  const [logs, setLogs] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const logContainerRef = useRef<HTMLDivElement>(null);

  // File sync
  const [syncLoading, setSyncLoading] = useState(false);
  const [syncStatus, setSyncStatus] = useState<SyncStatusInfo | null>(null);

  // ─── Fetch session ───
  const fetchSession = useCallback(async (silent = false) => {
    if (!agentId) return;
    if (!silent) setLoading(true);
    try {
      const resp = await apiClient.get<SandboxSessionInfo>(`/sandbox/sessions/${agentId}`);
      setSession(resp.data);
      setError(null);
    } catch (err: any) {
      if (err?.response?.status === 404) {
        // No active session — show page with "no sandbox" state
        setSession(null);
        setError(null);
      } else if (!silent) {
        setError(extractErrorMessage(err));
      }
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  // ─── Fetch logs ───
  const fetchLogs = useCallback(async () => {
    if (!agentId || !session || session.status !== 'running') return;
    setLogsLoading(true);
    try {
      const resp = await apiClient.get<{ logs: string[]; truncated?: boolean }>(
        `/sandbox/sessions/${agentId}/logs`,
      );
      setLogs(resp.data.logs || []);
    } catch (err) {
      // Silently ignore log fetch errors
    } finally {
      setLogsLoading(false);
    }
  }, [agentId, session]);

  // ── Fetch sync status ───
  const fetchSyncStatus = useCallback(async () => {
    if (!agentId) return;
    try {
      const resp = await apiClient.get<SyncStatusInfo>(
        `/api/agents/${agentId}/files/sync/status`,
      );
      setSyncStatus(resp.data);
    } catch {
      // Silently ignore
    }
  }, [agentId]);

  useEffect(() => {
    void fetchSession();
    void fetchSyncStatus();
    // Poll every 10 seconds
    const timer = window.setInterval(() => {
      void fetchSession(true);
      void fetchSyncStatus();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [fetchSession, fetchSyncStatus]);

  // Fetch logs when session is running
  useEffect(() => {
    if (session?.status === 'running') {
      void fetchLogs();
    }
  }, [session?.status, fetchLogs]);

  // ─── Actions ───
  const runAction = async (action: string, request: () => Promise<unknown>, successMsg: string) => {
    setActionLoading(action);
    try {
      await request();
      message.success(successMsg);
      await fetchSession(true);
      await fetchSyncStatus();
    } catch (err) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(err)}`);
    } finally {
      setActionLoading(null);
    }
  };

  const handleRenew = () =>
    runAction('renew', () => apiClient.post(`/sandbox/sessions/${agentId}/renew`), t('sandbox.renewSuccess'));

  const handlePause = () =>
    runAction('pause', () => apiClient.post(`/sandbox/sessions/${agentId}/pause`), t('sandbox.pauseSuccess'));

  const handleResume = () =>
    runAction('resume', () => apiClient.post(`/sandbox/sessions/${agentId}/resume`), t('sandbox.resumeSuccess'));

  const handleRestart = () =>
    runAction('restart', () => apiClient.post(`/sandbox/sessions/${agentId}/restart`), t('sandbox.restartSuccess'));

  const handleDestroy = () =>
    runAction('destroy', () => apiClient.delete(`/sandbox/sessions/${agentId}`), t('sandbox.destroySuccess'));

  const handleSync = (force = false) => {
    setSyncLoading(true);
    apiClient
      .post(`/api/agents/${agentId}/files/sync`, { direction: 'both', force })
      .then((resp) => {
        const pushed = resp.data?.pushed?.length ?? 0;
        const forceMsg = force ? ' (强制全量)' : '';
        message.success(`${t('sandbox.syncSuccess')}${forceMsg} (${pushed} files)`);
        void fetchLogs();
        void fetchSyncStatus();
      })
      .catch((err) => message.error(`${t('common.operationFailed')}：${extractErrorMessage(err)}`))
      .finally(() => setSyncLoading(false));
  };

  // ─── Derived ───
  const statusColor = (status: string) => {
    switch (status) {
      case 'running': return 'green';
      case 'paused': return 'gold';
      case 'destroyed': return 'default';
      case 'error': return 'red';
      default: return 'default';
    }
  };

  const strategyLabel = (strategy: string) => {
    switch (strategy) {
      case 'ephemeral': return t('sandbox.ephemeral');
      case 'pause-on-idle': return t('sandbox.pauseOnIdle');
      case 'persistent': return t('sandbox.persistent');
      default: return strategy;
    }
  };

  const formatTimeout = (seconds?: number) => {
    if (!seconds && seconds !== 0) return '—';
    const min = Math.floor(seconds / 60);
    const sec = seconds % 60;
    return `${min}分 ${sec}秒`;
  };

  const formatMemory = (used?: number, limit?: number) => {
    if (used === undefined || used === null) return '—';
    const usedMb = Math.round(used / (1024 * 1024));
    if (limit) {
      const limitMb = Math.round(limit / (1024 * 1024));
      return `${usedMb} / ${limitMb} MB`;
    }
    return `${usedMb} MB`;
  };

  const formatCpu = (cpu?: number) => {
    if (cpu === undefined || cpu === null) return '—';
    return `${cpu.toFixed(1)}%`;
  };

  const syncSummaryText = () => {
    if (!syncStatus?.summary) return '';
    const s = syncStatus.summary;
    const parts: string[] = [];
    if (s.synced > 0) parts.push(`已同步 ${s.synced}`);
    if (s.local_modified > 0) parts.push(`待推送 ${s.local_modified}`);
    if (s.remote_modified > 0) parts.push(`待拉回 ${s.remote_modified}`);
    if (s.conflict > 0) parts.push(`冲突 ${s.conflict}`);
    return parts.join(' · ') || '—';
  };

  // ─── Render ───
  return (
    <div>
      <GooglePageHeader
        icon={<CloudServerOutlined />}
        title={t('sandbox.title')}
        subtitle={`${t('sandbox.subtitle')} · ${agentId}`}
        extra={
          session && (
            <Space>
              <Tag color={statusColor(session.status)}>{t(`sandbox.${session.status}`)}</Tag>
              <Tag>{strategyLabel(session.strategy)}</Tag>
            </Space>
          )
        }
      />

      {loading ? (
        <GoogleCard>
          <Skeleton active paragraph={{ rows: 8 }} />
        </GoogleCard>
      ) : error ? (
        <Alert
          type="error"
          showIcon
          message={error}
          description={
            <Space direction="vertical">
              <Text>{t('sandbox.noSandboxHint')}</Text>
              <Button size="small" onClick={() => navigate(`/agents/${agentId}/config`)}>
                {t('sandbox.manageSandbox')}
              </Button>
            </Space>
          }
        />
      ) : !session ? (
        // No active sandbox — show info card instead of blocking the page
        <GoogleCard>
          <Empty
            image={<CloudServerOutlined style={{ fontSize: 48, color: 'var(--google-muted-foreground)' }} />}
            description={
              <Space direction="vertical" align="center">
                <Text type="secondary">{t('sandbox.noSandbox')}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>{t('sandbox.noSandboxHint')}</Text>
                <Button size="small" onClick={() => navigate(`/agents/${agentId}/config`)}>
                  {t('sandbox.manageSandbox')}
                </Button>
              </Space>
            }
          />
        </GoogleCard>
      ) : (
        <>
          {/* Container Status */}
          <GoogleCard
            title={<Space><CloudServerOutlined style={{ color: 'var(--google-primary)' }} /><span>{t('sandbox.containerStatus')}</span></Space>}
            style={{ marginBottom: 'var(--google-space-8)' }}
          >
            <Descriptions column={2} size="small" bordered styles={{ label: { width: 140 } }}>
              <Descriptions.Item label="Sandbox ID">
                <Text code style={{ fontSize: 12 }}>{session.sandbox_id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label={t('common.status')}>
                <Tag color={statusColor(session.status)} icon={
                  session.status === 'running' ? <PlayCircleOutlined /> :
                  session.status === 'paused' ? <PauseCircleOutlined /> : undefined
                }>
                  {t(`sandbox.${session.status}`)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label={t('sandbox.strategy')}>
                {strategyLabel(session.strategy)}
              </Descriptions.Item>
              <Descriptions.Item label={t('sandbox.image')}>
                {session.image ?? '—'}
              </Descriptions.Item>
              <Descriptions.Item label={t('sandbox.createdAt')}>
                {formatDateTime(session.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label={t('sandbox.remaining')}>
                <ClockCircleOutlined style={{ marginRight: 4 }} />
                {formatTimeout(session.timeout_remaining)}
              </Descriptions.Item>
              <Descriptions.Item label={t('sandbox.cpu')}>
                {formatCpu(session.cpu_usage)}
              </Descriptions.Item>
              <Descriptions.Item label={t('sandbox.memoryUsage')}>
                {formatMemory(session.memory_usage, session.memory_limit)}
              </Descriptions.Item>
            </Descriptions>

            <div style={{ marginTop: 'var(--google-space-6)' }}>
              <Space wrap>
                <Button
                  icon={<ClockCircleOutlined />}
                  onClick={handleRenew}
                  loading={actionLoading === 'renew'}
                  disabled={session.status !== 'running'}
                >
                  {t('sandbox.renew')}
                </Button>
                <Button
                  icon={<PauseCircleOutlined />}
                  onClick={handlePause}
                  loading={actionLoading === 'pause'}
                  disabled={session.status !== 'running'}
                >
                  {t('sandbox.pause')}
                </Button>
                <Button
                  icon={<PlayCircleOutlined />}
                  onClick={handleResume}
                  loading={actionLoading === 'resume'}
                  disabled={session.status !== 'paused'}
                >
                  {t('sandbox.resume')}
                </Button>
                <Popconfirm
                  title={t('sandbox.confirmRestart')}
                  onConfirm={handleRestart}
                  okButtonProps={{ danger: true }}
                >
                  <Button
                    icon={<ReloadOutlined />}
                    loading={actionLoading === 'restart'}
                  >
                    {t('sandbox.restart')}
                  </Button>
                </Popconfirm>
                <Popconfirm
                  title={t('sandbox.confirmDestroy')}
                  onConfirm={handleDestroy}
                  okButtonProps={{ danger: true }}
                >
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    loading={actionLoading === 'destroy'}
                  >
                    {t('sandbox.destroy')}
                  </Button>
                </Popconfirm>
              </Space>
            </div>
          </GoogleCard>

          {/* Real-time Logs */}
          <GoogleCard
            title={<Space><CodeOutlined style={{ color: 'var(--google-chart-2)' }} /><span>{t('sandbox.realtimeLogs')}</span></Space>}
            extra={
              session?.status === 'running' ? (
                <Button size="small" icon={<ReloadOutlined />} loading={logsLoading} onClick={() => void fetchLogs()}>
                  {t('sandbox.clearLogs')}
                </Button>
              ) : undefined
            }
            style={{ marginBottom: 'var(--google-space-8)' }}
          >
            <div
              ref={logContainerRef}
              style={{
                background: 'var(--google-muted)',
                border: '1px solid var(--google-border)',
                borderRadius: 'var(--google-radius-md)',
                padding: '12px 16px',
                fontFamily: 'var(--google-font-mono)',
                fontSize: 12,
                maxHeight: 300,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                lineHeight: 1.8,
              }}
            >
              {session?.status !== 'running' ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {session?.status === 'destroyed' ? 'Sandbox destroyed — no logs available' :
                   session?.status === 'paused' ? 'Sandbox paused — resume to view logs' :
                   'Sandbox not running'}
                </Text>
              ) : logsLoading ? (
                <Text type="secondary" style={{ fontSize: 12 }}>Loading logs...</Text>
              ) : logs.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>No logs yet...</Text>
              ) : (
                logs.map((line, i) => <div key={i}>{line}</div>)
              )}
            </div>
          </GoogleCard>

          {/* File Sync */}
          <GoogleCard
            title={<Space><FolderOpenOutlined style={{ color: 'var(--google-chart-3)' }} /><span>{t('sandbox.fileSync')}</span></Space>}
          >
            <Alert
              type="info"
              showIcon
              message={t('sandbox.fsBanner')}
              description={
                <div>
                  <div>{t('sandbox.sandboxConfigWarning')}</div>
                  {syncStatus && (
                    <div style={{ marginTop: 8, fontSize: 12 }}>
                      {syncStatus.container_alive !== undefined && (
                        <span>容器: {syncStatus.container_alive ? '运行中' : '未运行'} · </span>
                      )}
                      {syncStatus.container_root && (
                        <span>容器路径: {syncStatus.container_root} · </span>
                      )}
                      {syncStatus.last_sync_at && (
                        <span>最近同步: {formatDateTime(syncStatus.last_sync_at)}</span>
                      )}
                      {syncSummaryText() && <div>{syncSummaryText()}</div>}
                    </div>
                  )}
                </div>
              }
              style={{ marginBottom: 'var(--google-space-6)' }}
            />

            <Space>
              <Button
                type="primary"
                icon={<SyncOutlined />}
                loading={syncLoading}
                onClick={() => handleSync(false)}
                disabled={session.status !== 'running'}
              >
                {t('sandbox.syncAll')}
              </Button>
              <Popconfirm
                title={t('sandbox.forceSyncConfirm')}
                onConfirm={() => handleSync(true)}
                okText={t('common.confirm')}
                cancelText={t('common.cancel')}
              >
                <Button
                  icon={<ThunderboltOutlined />}
                  loading={syncLoading}
                  disabled={session.status !== 'running'}
                >
                  {t('sandbox.forceSync')}
                </Button>
              </Popconfirm>
              <Button
                icon={<ReloadOutlined />}
                loading={logsLoading}
                onClick={() => { void fetchSyncStatus(); }}
              >
                刷新状态
              </Button>
            </Space>
          </GoogleCard>
        </>
      )}
    </div>
  );
}
