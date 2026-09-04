import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CloudServerOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  DeleteOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  PoweroffOutlined,
  CopyOutlined,
  LinkOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { extractErrorMessage, formatDateTime } from '../../utils/helpers';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

/* ───────── Types ───────── */

interface Overview {
  enabled: boolean;
  server_url: string;
  server_reachable: boolean;
  version: string | null;
  containers: { total: number; running: number; paused: number; other: number };
  cpu: { total_cores: number | null; used_percentage: number | null };
  memory: { total_mib: number | null; used_mib: number | null };
  error: string | null;
}

interface Container {
  sandbox_id: string;
  agent_id: string;
  image: string;
  status: string;
  platform: string;
  created_at: string | null;
  expires_at: string | null;
}

interface ContainerMetrics {
  cpu_count: number | null;
  cpu_used_percentage: number | null;
  memory_total_in_mib: number | null;
  memory_used_in_mib: number | null;
}

interface SandboxSettings {
  enabled: boolean;
  domain: string;
  protocol: string;
  default_cleanup: string;
  base_url: string;
  api_key_set: boolean;
}

interface ImageCount {
  image: string;
  count: number;
}

interface ServerGuide {
  prerequisites: { docker: boolean; uv: boolean };
  quickstart_commands: { windows: string[]; linux: string[] };
  aio_requirements?: {
    seccomp_profile: string;
    config_key: string;
    config_file: string;
    docker_equivalent: string;
    reason: string;
    applies_to: string;
  };
  script_paths: { windows: string; linux: string };
  default_url: string;
  docs_url: string;
}

/* ───────── Helpers ───────── */

const isRunning = (s: string) => ['running', 'ready', 'alive'].includes((s || '').toLowerCase());

const statusColor = (s: string) => {
  const k = (s || '').toLowerCase();
  if (isRunning(k)) return 'green';
  if (k === 'paused') return 'gold';
  if (k === 'error') return 'red';
  return 'default';
};

const formatMem = (mib: number | null | undefined) =>
  mib === null || mib === undefined ? '—' : mib >= 1024 ? `${(mib / 1024).toFixed(1)} GiB` : `${Math.round(mib)} MiB`;

/* ───────── Page ───────── */

export default function SandboxControlPage() {
  const { t } = useI18n();

  const [overview, setOverview] = useState<Overview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const [containers, setContainers] = useState<Container[]>([]);
  const [containersLoading, setContainersLoading] = useState(false);
  const [containersReachable, setContainersReachable] = useState(true);

  const [guide, setGuide] = useState<ServerGuide | null>(null);

  // Logs modal
  const [logsOpen, setLogsOpen] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsId, setLogsId] = useState<string>('');

  // Resources tab
  const [metrics, setMetrics] = useState<Record<string, ContainerMetrics>>({});
  const [metricsLoading, setMetricsLoading] = useState(false);

  // Images tab
  const [images, setImages] = useState<ImageCount[]>([]);
  const [imagesLoading, setImagesLoading] = useState(false);

  // Settings form
  const [settings, setSettings] = useState<SandboxSettings | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ reachable: boolean; latency_ms: number | null; version: string | null; error: string | null; base_url: string } | null>(null);
  const [form] = Form.useForm();

  /* ─── Fetchers ─── */
  const fetchOverview = useCallback(async () => {
    setOverviewLoading(true);
    try {
      const resp = await apiClient.get<Overview>('/sandbox/overview');
      setOverview(resp.data);
    } catch (err) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(err)}`);
    } finally {
      setOverviewLoading(false);
    }
  }, [t]);

  const fetchContainers = useCallback(async () => {
    setContainersLoading(true);
    try {
      const resp = await apiClient.get<{ containers: Container[]; reachable: boolean }>('/sandbox/containers');
      setContainers(resp.data.containers || []);
      setContainersReachable(resp.data.reachable !== false);
    } catch (err) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(err)}`);
    } finally {
      setContainersLoading(false);
    }
  }, [t]);

  const fetchGuide = useCallback(async () => {
    try {
      const resp = await apiClient.get<ServerGuide>('/sandbox/server-guide');
      setGuide(resp.data);
    } catch {
      /* non-fatal */
    }
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const resp = await apiClient.get<SandboxSettings>('/sandbox/settings');
      setSettings(resp.data);
      form.setFieldsValue({
        domain: resp.data.domain,
        protocol: resp.data.protocol,
        default_cleanup: resp.data.default_cleanup,
        api_key: '',
      });
    } catch (err) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(err)}`);
    }
  }, [form, t]);

  const fetchImages = useCallback(async () => {
    setImagesLoading(true);
    try {
      const resp = await apiClient.get<{ images: ImageCount[] }>('/sandbox/images');
      setImages(resp.data.images || []);
    } catch {
      setImages([]);
    } finally {
      setImagesLoading(false);
    }
  }, []);

  const fetchMetrics = useCallback(async (list: Container[]) => {
    const running = list.filter((c) => isRunning(c.status));
    if (running.length === 0) {
      setMetrics({});
      return;
    }
    setMetricsLoading(true);
    const result: Record<string, ContainerMetrics> = {};
    await Promise.all(
      running.map(async (c) => {
        try {
          const resp = await apiClient.get<ContainerMetrics>(`/sandbox/containers/${c.sandbox_id}/metrics`);
          result[c.sandbox_id] = resp.data;
        } catch {
          /* skip */
        }
      }),
    );
    setMetrics(result);
    setMetricsLoading(false);
  }, []);

  /* ─── Lifecycle ─── */
  useEffect(() => {
    void fetchOverview();
    void fetchContainers();
    void fetchGuide();
    void fetchSettings();
  }, [fetchOverview, fetchContainers, fetchGuide, fetchSettings]);

  // Poll while enabled & reachable
  useEffect(() => {
    if (!overview?.enabled || !overview?.server_reachable) return;
    const timer = window.setInterval(() => {
      void fetchOverview();
      void fetchContainers();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [overview?.enabled, overview?.server_reachable, fetchOverview, fetchContainers]);

  /* ─── Actions ─── */
  const runOp = async (key: string, fn: () => Promise<unknown>, okMsg: string) => {
    setActionLoading(key);
    try {
      await fn();
      message.success(okMsg);
      await fetchOverview();
      await fetchContainers();
    } catch (err) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(err)}`);
    } finally {
      setActionLoading(null);
    }
  };

  const handleStart = () => runOp('start', () => apiClient.post('/sandbox/start'), t('sandboxControl.enableSuccess'));
  const handleStop = () => runOp('stop', () => apiClient.post('/sandbox/stop'), t('sandboxControl.disableSuccess'));

  const openLogs = async (id: string) => {
    setLogsId(id);
    setLogsOpen(true);
    setLogsLoading(true);
    setLogs([]);
    try {
      const resp = await apiClient.get<{ logs: string[] }>(`/sandbox/containers/${id}/logs`);
      setLogs(resp.data.logs || []);
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setLogsLoading(false);
    }
  };

  const handleTest = async () => {
    const values = form.getFieldsValue();
    setTesting(true);
    setTestResult(null);
    try {
      const body: Record<string, unknown> = { domain: values.domain, protocol: values.protocol };
      if (values.api_key) body.api_key = values.api_key;
      const resp = await apiClient.post('/sandbox/settings/test', body);
      setTestResult(resp.data);
    } catch (err) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(err)}`);
    } finally {
      setTesting(false);
    }
  };

  const handleSaveSettings = async () => {
    let values: { domain: string; protocol: string; default_cleanup: string; api_key?: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSavingSettings(true);
    try {
      const body: Record<string, unknown> = {
        domain: values.domain,
        protocol: values.protocol,
        default_cleanup: values.default_cleanup,
      };
      if (values.api_key) body.api_key = values.api_key;
      const resp = await apiClient.put<SandboxSettings>('/sandbox/settings', body);
      setSettings(resp.data);
      form.setFieldValue('api_key', '');
      message.success(t('sandboxControl.saveSuccess'));
      void fetchOverview();
    } catch (err) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(err)}`);
    } finally {
      setSavingSettings(false);
    }
  };

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success(t('sandboxControl.copied'));
    } catch {
      message.warning(text);
    }
  };

  /* ─── Column defs ─── */
  const containerColumns: ColumnsType<Container> = [
    { title: 'Sandbox ID', dataIndex: 'sandbox_id', key: 'sandbox_id', render: (v: string) => <Text code style={{ fontSize: 12 }}>{v}</Text> },
    { title: t('sandboxControl.agent'), dataIndex: 'agent_id', key: 'agent_id', render: (v: string) => v || '—' },
    { title: t('sandboxControl.image'), dataIndex: 'image', key: 'image', render: (v: string) => v || '—', ellipsis: true },
    { title: t('common.status'), dataIndex: 'status', key: 'status', width: 110, ellipsis: true, render: (v: string) => <Tag color={statusColor(v)} title={v || 'unknown'} style={{ maxWidth: '100%', marginInlineEnd: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'middle' }}>{v || 'unknown'}</Tag> },
    { title: t('sandboxControl.createdAt'), dataIndex: 'created_at', key: 'created_at', width: 180, render: (v: string | null) => formatDateTime(v) },
    { title: t('sandboxControl.expiresAt'), dataIndex: 'expires_at', key: 'expires_at', width: 180, render: (v: string | null) => formatDateTime(v) },
    {
      title: t('common.actions'),
      key: 'actions',
      width: 280,
      render: (_, r) => (
        <Space size={4} wrap>
          <Button size="small" icon={<PauseCircleOutlined />} disabled={!isRunning(r.status)} loading={actionLoading === `pause-${r.sandbox_id}`}
            onClick={() => runOp(`pause-${r.sandbox_id}`, () => apiClient.post(`/sandbox/containers/${r.sandbox_id}/pause`), t('sandboxControl.pauseSuccess'))}>
            {t('sandbox.pause')}
          </Button>
          <Button size="small" icon={<PlayCircleOutlined />} disabled={r.status?.toLowerCase() !== 'paused'} loading={actionLoading === `resume-${r.sandbox_id}`}
            onClick={() => runOp(`resume-${r.sandbox_id}`, () => apiClient.post(`/sandbox/containers/${r.sandbox_id}/resume`), t('sandboxControl.resumeSuccess'))}>
            {t('sandbox.resume')}
          </Button>
          <Button size="small" icon={<ClockCircleOutlined />} disabled={!isRunning(r.status)} loading={actionLoading === `renew-${r.sandbox_id}`}
            onClick={() => runOp(`renew-${r.sandbox_id}`, () => apiClient.post(`/sandbox/containers/${r.sandbox_id}/renew`), t('sandbox.renewSuccess'))}>
            {t('sandbox.renew')}
          </Button>
          <Button size="small" icon={<CodeOutlined />} onClick={() => void openLogs(r.sandbox_id)}>{t('sandboxControl.logs')}</Button>
          <Popconfirm title={t('sandboxControl.confirmDestroy')} okButtonProps={{ danger: true }} okText={t('common.delete')} cancelText={t('common.cancel')}
            onConfirm={() => runOp(`destroy-${r.sandbox_id}`, () => apiClient.post(`/sandbox/containers/${r.sandbox_id}/destroy`), t('sandbox.destroySuccess'))}>
            <Button size="small" danger icon={<DeleteOutlined />} loading={actionLoading === `destroy-${r.sandbox_id}`} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const resourceColumns: ColumnsType<Container> = [
    { title: 'Sandbox ID', dataIndex: 'sandbox_id', key: 'sandbox_id', render: (v: string) => <Text code style={{ fontSize: 12 }}>{v}</Text> },
    { title: t('sandboxControl.agent'), dataIndex: 'agent_id', key: 'agent_id', render: (v: string) => v || '—' },
    {
      title: t('sandboxControl.cpuCores'),
      key: 'cpu_count',
      render: (_, r) => metrics[r.sandbox_id]?.cpu_count ?? '—',
    },
    {
      title: t('sandboxControl.cpuUsage'),
      key: 'cpu_used',
      render: (_, r) => {
        const m = metrics[r.sandbox_id];
        return m?.cpu_used_percentage !== undefined && m?.cpu_used_percentage !== null
          ? <Progress percent={Number(m.cpu_used_percentage.toFixed(1))} size="small" />
          : '—';
      },
    },
    {
      title: t('sandboxControl.memory'),
      key: 'mem',
      render: (_, r) => {
        const m = metrics[r.sandbox_id];
        return m ? `${formatMem(m.memory_used_in_mib)} / ${formatMem(m.memory_total_in_mib)}` : '—';
      },
    },
  ];

  const imageColumns: ColumnsType<ImageCount> = [
    { title: t('sandboxControl.image'), dataIndex: 'image', key: 'image', render: (v: string) => <Text code>{v}</Text> },
    { title: t('sandboxControl.containerCount'), dataIndex: 'count', key: 'count', width: 160 },
  ];

  /* ─── Startup guide card ─── */
  const renderGuide = () => {
    if (!guide) return null;
    const isWin = typeof navigator !== 'undefined' && /win/i.test(navigator.platform || '');
    const cmds = isWin ? guide.quickstart_commands.windows : guide.quickstart_commands.linux;
    return (
      <GoogleCard
        title={<Space><CloudServerOutlined style={{ color: 'var(--google-primary)' }} /><span>{t('sandboxControl.guideTitle')}</span></Space>}
        style={{ marginBottom: 'var(--google-space-8)' }}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 'var(--google-space-6)' }}
          message={t('sandboxControl.guideIntro')}
          description={
            <Space size={8} wrap>
              <Tag color={guide.prerequisites.docker ? 'green' : 'red'}>{t('sandboxControl.docker')} {guide.prerequisites.docker ? t('sandboxControl.ready') : t('sandboxControl.missing')}</Tag>
              <Tag color={guide.prerequisites.uv ? 'green' : 'orange'}>{t('sandboxControl.uv')} {guide.prerequisites.uv ? t('sandboxControl.ready') : t('sandboxControl.missing')}</Tag>
            </Space>
          }
        />
        {guide.aio_requirements && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 'var(--google-space-6)' }}
            message={t('sandboxControl.aioReqTitle')}
            description={
              <Space direction="vertical" size={4}>
                <Text style={{ fontSize: 12 }}>
                  {t('sandboxControl.aioReqKey')}: <Text code>{guide.aio_requirements.config_key}</Text> = <Text code>"{guide.aio_requirements.seccomp_profile}"</Text>
                  {t('sandboxControl.aioReqInFile')} <Text code>{guide.aio_requirements.config_file}</Text>
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>{guide.aio_requirements.reason}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>{t('sandboxControl.aioReqAppliesTo')}: {guide.aio_requirements.applies_to}</Text>
              </Space>
            }
          />
        )}
        <Space direction="vertical" style={{ width: '100%' }}>
          {cmds.map((cmd, i) => (
            <Space.Compact key={i} style={{ width: '100%' }}>
              <Input readOnly value={cmd} style={{ fontFamily: 'var(--google-font-mono)', fontSize: 12 }} />
              <Button icon={<CopyOutlined />} onClick={() => void copyText(cmd)} />
            </Space.Compact>
          ))}
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('sandboxControl.scriptPath')}: <Text code>{isWin ? guide.script_paths.windows : guide.script_paths.linux}</Text>
          </Text>
          <Space>
            <Button icon={<LinkOutlined />} href={guide.docs_url} target="_blank" rel="noreferrer">{t('sandboxControl.viewDocs')}</Button>
            <Button icon={<ReloadOutlined />} loading={overviewLoading} onClick={() => { void fetchOverview(); void fetchContainers(); }}>{t('sandboxControl.recheck')}</Button>
          </Space>
        </Space>
      </GoogleCard>
    );
  };

  /* ─── Tabs ─── */
  const overviewTab = (
    <div>
      {!overview?.server_reachable && renderGuide()}

      <GoogleCard loading={overviewLoading} style={{ marginBottom: 'var(--google-space-8)' }}>
        <Descriptions column={2} size="small" bordered styles={{ label: { width: 140 } }}>
          <Descriptions.Item label={t('sandboxControl.enabled')}>
            <Tag color={overview?.enabled ? 'green' : 'default'}>{overview?.enabled ? t('sandboxControl.on') : t('sandboxControl.off')}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label={t('sandboxControl.reachability')}>
            <Tag color={overview?.server_reachable ? 'green' : 'red'}>{overview?.server_reachable ? t('sandboxControl.reachable') : t('sandboxControl.unreachable')}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label={t('sandboxControl.serverUrl')}>{overview?.server_url || '—'}</Descriptions.Item>
          <Descriptions.Item label={t('sandboxControl.version')}>{overview?.version || '—'}</Descriptions.Item>
        </Descriptions>

        <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap', marginTop: 'var(--google-space-6)' }}>
          <Statistic title={t('sandboxControl.containersTotal')} value={overview?.containers.total ?? 0} />
          <Statistic title={t('sandboxControl.running')} value={overview?.containers.running ?? 0} valueStyle={{ color: '#34a853' }} />
          <Statistic title={t('sandboxControl.paused')} value={overview?.containers.paused ?? 0} valueStyle={{ color: '#fbbc05' }} />
          <Statistic title={t('sandboxControl.cpuCores')} value={overview?.cpu.total_cores ?? '—'} />
          <Statistic title={t('sandboxControl.memoryTotal')} value={formatMem(overview?.memory.total_mib)} />
          <Statistic title={t('sandboxControl.memoryUsed')} value={formatMem(overview?.memory.used_mib)} />
        </div>
      </GoogleCard>
    </div>
  );

  const containersTab = (
    <GoogleCard
      title={<Space><CloudServerOutlined style={{ color: 'var(--google-primary)' }} /><span>{t('sandboxControl.containers')}</span></Space>}
      extra={<Button size="small" icon={<ReloadOutlined />} loading={containersLoading} onClick={() => void fetchContainers()}>{t('sandboxControl.refresh')}</Button>}
    >
      {!containersReachable && (
        <Alert type="error" showIcon style={{ marginBottom: 'var(--google-space-6)' }}
          message={t('sandboxControl.unreachable')} description={t('sandboxControl.unreachableHint')} />
      )}
      <Table rowKey="sandbox_id" size="small" loading={containersLoading} columns={containerColumns} dataSource={containers} pagination={false} scroll={{ x: 960 }} />
    </GoogleCard>
  );

  const resourcesTab = (
    <GoogleCard
      title={<Space><ApiOutlined style={{ color: 'var(--google-chart-2)' }} /><span>{t('sandboxControl.resources')}</span></Space>}
      extra={<Button size="small" icon={<ReloadOutlined />} loading={metricsLoading} onClick={() => void fetchMetrics(containers)}>{t('sandboxControl.refresh')}</Button>}
    >
      <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap', marginBottom: 'var(--google-space-6)' }}>
        <Statistic title={t('sandboxControl.cpuCores')} value={overview?.cpu.total_cores ?? '—'} />
        <Statistic title={t('sandboxControl.cpuUsage')} value={overview?.cpu.used_percentage ?? '—'} suffix={overview?.cpu.used_percentage != null ? '%' : ''} />
        <Statistic title={t('sandboxControl.memoryUsed')} value={formatMem(overview?.memory.used_mib)} />
        <Statistic title={t('sandboxControl.memoryTotal')} value={formatMem(overview?.memory.total_mib)} />
      </div>
      <Table rowKey="sandbox_id" size="small" loading={metricsLoading} columns={resourceColumns}
        dataSource={containers.filter((c) => isRunning(c.status))} pagination={false} scroll={{ x: 720 }} />
    </GoogleCard>
  );

  const imagesTab = (
    <GoogleCard
      title={<Space><CloudServerOutlined style={{ color: 'var(--google-chart-3)' }} /><span>{t('sandboxControl.images')}</span></Space>}
      extra={<Button size="small" icon={<ReloadOutlined />} loading={imagesLoading} onClick={() => void fetchImages()}>{t('sandboxControl.refresh')}</Button>}
    >
      <Table rowKey="image" size="small" loading={imagesLoading} columns={imageColumns} dataSource={images} pagination={false} />
    </GoogleCard>
  );

  const configTab = (
    <GoogleCard loading={!settings}>
      <Form form={form} layout="vertical" style={{ maxWidth: 520 }}>
        <Form.Item label={t('sandboxControl.domain')} name="domain" rules={[{ required: true, message: t('sandboxControl.domainRequired') }]}
          extra={t('sandboxControl.domainHint')}>
          <Input placeholder="localhost:8080" />
        </Form.Item>
        <Form.Item label={t('sandboxControl.protocol')} name="protocol">
          <Select options={[{ value: 'http', label: 'http' }, { value: 'https', label: 'https' }]} />
        </Form.Item>
        <Form.Item label={t('sandboxControl.apiKey')} name="api_key"
          extra={settings?.api_key_set ? t('sandboxControl.apiKeySetHint') : t('sandboxControl.apiKeyHint')}>
          <Input.Password placeholder={settings?.api_key_set ? '********' : t('sandboxControl.apiKeyPlaceholder')} autoComplete="new-password" />
        </Form.Item>
        <Form.Item label={t('sandboxControl.cleanup')} name="default_cleanup">
          <Select options={[{ value: 'on_exit', label: t('sandboxControl.cleanupOnExit') }, { value: 'never', label: t('sandboxControl.cleanupNever') }]} />
        </Form.Item>
        <Space wrap>
          <Button icon={<ApiOutlined />} loading={testing} onClick={() => void handleTest()}>{t('sandboxControl.testConnection')}</Button>
          <Button type="primary" loading={savingSettings} onClick={() => void handleSaveSettings()}>{t('common.save')}</Button>
        </Space>
      </Form>
      {testResult && (
        <Alert style={{ marginTop: 'var(--google-space-6)', maxWidth: 520 }} showIcon type={testResult.reachable ? 'success' : 'error'}
          message={testResult.reachable ? t('sandboxControl.testOk') : t('sandboxControl.testFail')}
          description={
            <Space direction="vertical" size={2}>
              <Text>{testResult.base_url}</Text>
              {testResult.reachable ? (
                <Text type="secondary">{t('sandboxControl.latency')}: {testResult.latency_ms} ms · {t('sandboxControl.version')}: {testResult.version}</Text>
              ) : (
                <Text type="secondary">{testResult.error}</Text>
              )}
              {!overview?.server_reachable && <Text type="secondary" style={{ fontSize: 12 }}>{t('sandboxControl.versionHint')}</Text>}
            </Space>
          }
        />
      )}
    </GoogleCard>
  );

  return (
    <div>
      <GooglePageHeader
        icon={<CloudServerOutlined />}
        title={t('sandboxControl.title')}
        subtitle={t('sandboxControl.subtitle')}
        extra={
          <Space>
            {overview?.enabled ? (
              <Popconfirm title={t('sandboxControl.confirmDisable')} okText={t('common.confirm')} cancelText={t('common.cancel')} onConfirm={handleStop}>
                <Button danger icon={<PoweroffOutlined />} loading={actionLoading === 'stop'}>{t('sandboxControl.disable')}</Button>
              </Popconfirm>
            ) : (
              <Button type="primary" icon={<PlayCircleOutlined />} loading={actionLoading === 'start'} onClick={handleStart}>{t('sandboxControl.enable')}</Button>
            )}
          </Space>
        }
      />

      {overview?.enabled && !overview?.server_reachable && (
        <Alert type="warning" showIcon style={{ marginBottom: 'var(--google-space-6)' }}
          message={t('sandboxControl.enabledButUnreachable')} description={t('sandboxControl.unreachableHint')} />
      )}

      <Tabs
        defaultActiveKey="overview"
        onChange={(key) => {
          if (key === 'images' && images.length === 0) void fetchImages();
          if (key === 'resources') void fetchMetrics(containers);
        }}
        items={[
          { key: 'overview', label: t('sandboxControl.tabOverview'), children: overviewTab },
          { key: 'containers', label: t('sandboxControl.tabContainers'), children: containersTab },
          { key: 'resources', label: t('sandboxControl.tabResources'), children: resourcesTab },
          { key: 'images', label: t('sandboxControl.tabImages'), children: imagesTab },
          { key: 'config', label: t('sandboxControl.tabConfig'), children: configTab },
        ]}
      />

      <Modal
        open={logsOpen}
        onCancel={() => setLogsOpen(false)}
        footer={null}
        width={800}
        title={<Space><CodeOutlined /><span>{`${t('sandboxControl.logs')} · ${logsId}`}</span></Space>}
      >
        <div style={{
          background: 'var(--google-muted)', border: '1px solid var(--google-border)', borderRadius: 'var(--google-radius-md)',
          padding: '12px 16px', fontFamily: 'var(--google-font-mono)', fontSize: 12, maxHeight: 420, overflow: 'auto', whiteSpace: 'pre-wrap', lineHeight: 1.7,
        }}>
          {logsLoading ? <Text type="secondary">Loading…</Text>
            : logs.length === 0 ? <Text type="secondary">{t('sandboxControl.noLogs')}</Text>
            : logs.map((line, i) => <div key={i}>{line}</div>)}
        </div>
      </Modal>
    </div>
  );
}
