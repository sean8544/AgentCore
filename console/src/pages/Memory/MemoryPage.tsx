import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button,
  Collapse,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  DatabaseOutlined,
  EyeOutlined,
  PlayCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { useNavigate, useParams } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { extractErrorMessage } from '../../utils/helpers';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import MarkdownView from '../../components/MarkdownView';

const { Text } = Typography;

/* ───────── Types ───────── */

interface MemoryConfig {
  enabled: boolean;
  consolidate_cron: string;
  retain_days: number;
  max_memory_kb: number;
  timeout_seconds: number;
}

interface MemoryFile {
  name: string;
  content: string;
  size_bytes: number;
}

interface ArchiveEntry {
  name: string;
  size_bytes: number;
  modified_at: string;
}

interface MemoryLastRun {
  status: string;
  at: string;
  summary?: string;
  reason?: string;
  session_id?: string;
  consolidated_files?: string[];
  archived_files?: string[];
}

interface MemoryPayload {
  agent_id: string;
  config: MemoryConfig;
  files: Record<string, MemoryFile | null>;
  archives: ArchiveEntry[];
  archived: ArchiveEntry[];
  consolidation: {
    state: { last_consolidated_at?: string; last_consolidated_files?: string[] };
    scheduled: boolean;
    next_run_at: string | null;
    last_status: string | null;
  };
  last_run: MemoryLastRun | null;
}

interface AgentInfo {
  agent_id: string;
  description?: string;
}

interface FormValues {
  enabled: boolean;
  consolidate_cron: string;
  retain_days: number;
  max_memory_kb: number;
  timeout_seconds: number;
}

const STATUS_COLOR: Record<string, string> = {
  success: 'green',
  skipped: 'default',
  timeout: 'orange',
  error: 'red',
  pending_approval: 'purple',
};

const STATUS_KEY: Record<string, string> = {
  success: 'memory.statusSuccess',
  skipped: 'memory.statusSkipped',
  timeout: 'memory.statusTimeout',
  error: 'memory.statusError',
  pending_approval: 'memory.statusPendingApproval',
};

const MEMORY_FILE_NAMES = ['MEMORY.md', 'USER.md'];

function formatTime(value?: string | null): string {
  if (!value) return '—';
  const d = dayjs(value);
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : value;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

export default function MemoryPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { agentId: urlAgentId } = useParams<{ agentId?: string }>();
  const [form] = Form.useForm<FormValues>();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentId, setAgentId] = useState<string>('');
  const [payload, setPayload] = useState<MemoryPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [fileDrafts, setFileDrafts] = useState<Record<string, string>>({});
  const [savingFile, setSavingFile] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ name: string; content: string } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadPayload = useCallback(
    async (id: string, silent = false) => {
      if (!id) return;
      if (!silent) setLoading(true);
      try {
        const res = await apiClient.get(`/agents/${id}/memory`);
        const data = res.data as MemoryPayload;
        setPayload(data);
        form.setFieldsValue({
          enabled: data.config?.enabled ?? true,
          consolidate_cron: data.config?.consolidate_cron ?? '0 3 * * *',
          retain_days: data.config?.retain_days ?? 30,
          max_memory_kb: data.config?.max_memory_kb ?? 16,
          timeout_seconds: data.config?.timeout_seconds ?? 600,
        });
        setFileDrafts((prev) => {
          const next = { ...prev };
          for (const name of MEMORY_FILE_NAMES) {
            // Only (re)populate drafts that the user has not edited yet.
            if (next[name] === undefined) {
              next[name] = data.files?.[name]?.content ?? '';
            }
          }
          return next;
        });
      } catch (error) {
        setPayload(null);
        if (!silent) {
          message.error(`${t('memory.loadFailed')}：${extractErrorMessage(error)}`);
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [form, t],
  );

  useEffect(() => {
    apiClient
      .get('/agents')
      .then((res) => {
        const list = (res.data ?? []) as AgentInfo[];
        setAgents(list);
        if (list.length > 0) {
          // Prefer the agent from the URL (/agents/:agentId/memory), then
          // the seeded ``default`` agent.
          const preferred =
            (urlAgentId && list.find((a) => a.agent_id === urlAgentId)) ||
            list.find((a) => a.agent_id === 'default') ||
            list[0];
          setAgentId(preferred.agent_id);
          void loadPayload(preferred.agent_id);
        }
      })
      .catch(() => setAgents([]));
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Follow external agent switches (e.g. the sidebar AgentSelector) that only
  // change the URL path.
  useEffect(() => {
    if (urlAgentId && urlAgentId !== agentId && agents.some((a) => a.agent_id === urlAgentId)) {
      setAgentId(urlAgentId);
      setPayload(null);
      setFileDrafts({});
      void loadPayload(urlAgentId);
    }
  }, [urlAgentId, agentId, agents, loadPayload]);

  const handleAgentChange = (id: string) => {
    setAgentId(id);
    setPayload(null);
    setFileDrafts({});
    void loadPayload(id);
    if (id !== urlAgentId) navigate(`/agents/${id}/memory`);
  };

  const handleSave = async () => {
    let values: FormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    try {
      await apiClient.put(`/agents/${agentId}/memory/config`, {
        enabled: values.enabled,
        consolidate_cron: values.consolidate_cron,
        retain_days: values.retain_days,
        max_memory_kb: values.max_memory_kb,
        timeout_seconds: values.timeout_seconds,
      });
      message.success(t('memory.saveSuccess'));
      void loadPayload(agentId);
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const pollUntilFinished = useCallback(
    (baselineAt: string | null | undefined) => {
      const poll = async () => {
        try {
          const res = await apiClient.get(`/agents/${agentId}/memory`);
          const data = res.data as MemoryPayload;
          const finished =
            data.last_run && data.last_run.at !== (baselineAt ?? null);
          if (finished) {
            setRunning(false);
            setPayload(data);
            setFileDrafts({});
            await loadPayload(agentId, true);
            return;
          }
        } catch {
          /* transient poll errors are ignored */
        }
        pollTimer.current = setTimeout(poll, 5000);
      };
      pollTimer.current = setTimeout(poll, 5000);
    },
    [agentId, loadPayload],
  );

  const handleConsolidateNow = async () => {
    setRunning(true);
    try {
      await apiClient.post(`/agents/${agentId}/memory/consolidate`);
      message.info(t('memory.consolidationStarted'));
      pollUntilFinished(payload?.last_run?.at);
    } catch (error) {
      setRunning(false);
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    }
  };

  const handleSaveFile = async (name: string) => {
    setSavingFile(name);
    try {
      await apiClient.put(`/agents/${agentId}/memory/files/${name}`, {
        content: fileDrafts[name] ?? '',
      });
      message.success(t('memory.fileSaved', { name }));
      void loadPayload(agentId, true);
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setSavingFile(null);
    }
  };

  const handleViewArchive = async (name: string, archived: boolean) => {
    setPreviewLoading(true);
    setPreview({ name, content: '' });
    try {
      const res = await apiClient.get(
        `/agents/${agentId}/memory/archives/${name}`,
        { params: archived ? { archived: true } : {} },
      );
      setPreview({ name, content: res.data?.content ?? '' });
    } catch (error) {
      setPreview(null);
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setPreviewLoading(false);
    }
  };

  const renderArchiveList = (entries: ArchiveEntry[], archived: boolean) =>
    entries.length === 0 ? (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t('memory.archivesEmpty')}
      />
    ) : (
      <List
        size="small"
        dataSource={entries}
        renderItem={(entry) => (
          <List.Item
            actions={[
              <Button
                key="view"
                size="small"
                icon={<EyeOutlined />}
                onClick={() => handleViewArchive(entry.name, archived)}
              >
                {t('memory.view')}
              </Button>,
            ]}
          >
            <List.Item.Meta
              title={<Text>{entry.name}</Text>}
              description={
                <Text style={{ color: 'var(--google-muted-foreground)' }}>
                  {formatSize(entry.size_bytes)} · {formatTime(entry.modified_at)}
                </Text>
              }
            />
          </List.Item>
        )}
      />
    );

  return (
    <div>
      <GooglePageHeader
        icon={<DatabaseOutlined />}
        title={t('memory.title')}
        subtitle={t('memory.subtitle')}
      />

      {/* ── Configuration & consolidation status ── */}
      <GoogleCard loading={loading} style={{ marginBottom: 'var(--google-space-6)' }}>
        <div style={{ marginBottom: 'var(--google-space-4)' }}>
          <Space wrap align="center">
            <Text strong>{t('memory.agent')}</Text>
            <Select
              value={agentId || undefined}
              onChange={handleAgentChange}
              style={{ minWidth: 200 }}
              options={agents.map((a) => ({
                value: a.agent_id,
                label: a.agent_id,
              }))}
            />
            {payload && (
              <Tag color={payload.consolidation.scheduled ? 'green' : 'default'}>
                {payload.consolidation.scheduled
                  ? t('memory.scheduled')
                  : t('memory.notScheduled')}
              </Tag>
            )}
          </Space>
        </div>

        <Form form={form} layout="vertical" style={{ maxWidth: 560 }}>
          <Form.Item label={t('memory.enable')}>
            <Form.Item name="enabled" valuePropName="checked" noStyle>
              <Switch checkedChildren="ON" unCheckedChildren="OFF" />
            </Form.Item>
            <div style={{ marginTop: 'var(--google-space-2)' }}>
              <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
                {t('memory.enableHelp')}
              </Text>
            </div>
          </Form.Item>

          <Form.Item
            name="consolidate_cron"
            label={t('memory.cron')}
            rules={[{ required: true }]}
          >
            <Input placeholder="0 3 * * *" style={{ maxWidth: 200 }} />
          </Form.Item>
          <Form.Item style={{ marginTop: 'calc(-1 * var(--google-space-4))' }}>
            <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
              {t('memory.cronHelp')}
            </Text>
          </Form.Item>

          <Space size="large" wrap>
            <div>
              <Form.Item
                name="retain_days"
                label={t('memory.retainDays')}
                rules={[{ required: true }]}
              >
                <InputNumber min={1} max={3650} style={{ width: 140 }} />
              </Form.Item>
              <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
                {t('memory.retainDaysHelp')}
              </Text>
            </div>
            <div>
              <Form.Item
                name="max_memory_kb"
                label={t('memory.maxMemoryKb')}
                rules={[{ required: true }]}
              >
                <InputNumber min={1} max={1024} style={{ width: 140 }} />
              </Form.Item>
              <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
                {t('memory.maxMemoryKbHelp')}
              </Text>
            </div>
            <div>
              <Form.Item
                name="timeout_seconds"
                label={t('memory.timeout')}
                rules={[{ required: true }]}
              >
                <InputNumber min={1} max={3600} style={{ width: 140 }} />
              </Form.Item>
              <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
                {t('memory.timeoutHelp')}
              </Text>
            </div>
          </Space>

          <Space style={{ marginTop: 'var(--google-space-2)' }}>
            <Button type="primary" onClick={handleSave} loading={saving}>
              {t('memory.save')}
            </Button>
            <Button
              icon={<PlayCircleOutlined />}
              onClick={handleConsolidateNow}
              loading={running}
            >
              {running ? t('memory.consolidationRunning') : t('memory.consolidateNow')}
            </Button>
          </Space>
        </Form>

        {payload && (
          <div style={{ marginTop: 'var(--google-space-8)' }}>
            <Text strong>{t('memory.nextRun')}</Text>
            <Text style={{ marginLeft: 'var(--google-space-3)' }}>
              {formatTime(payload.consolidation.next_run_at)}
            </Text>
            <div style={{ marginTop: 'var(--google-space-2)' }}>
              <Text strong>{t('memory.lastRun')}</Text>
              {payload.last_run ? (
                <span style={{ marginLeft: 'var(--google-space-3)' }}>
                  <Tag color={STATUS_COLOR[payload.last_run.status] ?? 'default'}>
                    {t(
                      STATUS_KEY[payload.last_run.status] ?? 'memory.statusSuccess',
                    )}
                  </Tag>
                  <Text>{formatTime(payload.last_run.at)}</Text>
                  {payload.last_run.summary && (
                    <div
                      style={{
                        marginTop: 'var(--google-space-2)',
                        color: 'var(--google-muted-foreground)',
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      {payload.last_run.summary}
                    </div>
                  )}
                  {payload.last_run.status === 'pending_approval' && payload.last_run.session_id && (
                    <Button
                      type="link"
                      size="small"
                      icon={<ThunderboltOutlined />}
                      style={{ marginTop: 'var(--google-space-2)', padding: 0 }}
                      onClick={() => {
                        navigate(`/agents/${agentId}/chat`);
                        setTimeout(() => {
                          import('../../stores/chatStore').then(({ useChatStore }) => {
                            useChatStore.getState().selectSession(payload.last_run!.session_id!);
                          });
                        }, 500);
                      }}
                    >
                      {t('memory.goToApproval')}
                    </Button>
                  )}
                </span>
              ) : (
                <Text
                  style={{
                    marginLeft: 'var(--google-space-3)',
                    color: 'var(--google-muted-foreground)',
                  }}
                >
                  {t('memory.none')}
                </Text>
              )}
            </div>
          </div>
        )}
      </GoogleCard>

      {/* ── Editable memory files ── */}
      <GoogleCard
        title={t('memory.filesTitle')}
        style={{ marginBottom: 'var(--google-space-6)' }}
      >
        <Text
          style={{
            display: 'block',
            marginBottom: 'var(--google-space-4)',
            fontSize: 12,
            color: 'var(--google-muted-foreground)',
          }}
        >
          {t('memory.filesHint')}
        </Text>
        <Tabs
          items={MEMORY_FILE_NAMES.map((name) => ({
            key: name,
            label: name,
            children: (
              <div>
                <Input.TextArea
                  value={fileDrafts[name] ?? ''}
                  onChange={(e) =>
                    setFileDrafts((prev) => ({ ...prev, [name]: e.target.value }))
                  }
                  autoSize={{ minRows: 10, maxRows: 24 }}
                  style={{ fontFamily: 'var(--google-font-mono)', fontSize: 13 }}
                />
                <div style={{ marginTop: 'var(--google-space-3)' }}>
                  <Button
                    type="primary"
                    size="small"
                    loading={savingFile === name}
                    onClick={() => handleSaveFile(name)}
                  >
                    {t('memory.saveFile')}
                  </Button>
                </div>
              </div>
            ),
          }))}
        />
      </GoogleCard>

      {/* ── Session archives ── */}
      <GoogleCard title={t('memory.archivesTitle')}>
        {payload ? (
          <>
            {renderArchiveList(payload.archives, false)}
            {payload.archived.length > 0 && (
              <Collapse
                ghost
                style={{ marginTop: 'var(--google-space-4)' }}
                items={[
                  {
                    key: 'archived',
                    label: `${t('memory.archivedTitle')} (${payload.archived.length})`,
                    children: renderArchiveList(payload.archived, true),
                  },
                ]}
              />
            )}
          </>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t('memory.archivesEmpty')}
          />
        )}
      </GoogleCard>

      {/* ── Archive preview modal ── */}
      <Modal
        open={preview !== null}
        title={preview?.name}
        footer={null}
        width={720}
        onCancel={() => setPreview(null)}
      >
        {previewLoading ? (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <Spin />
          </div>
        ) : (
          <div style={{ maxHeight: '60vh', overflow: 'auto' }}>
            <MarkdownView text={preview?.content ?? ''} />
          </div>
        )}
      </Modal>
    </div>
  );
}
