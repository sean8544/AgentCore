import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Tag,
  TimePicker,
  Typography,
  message,
} from 'antd';
import { HeartOutlined, PlayCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';
import dayjs, { Dayjs } from 'dayjs';
import { useNavigate, useParams } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { extractErrorMessage } from '../../utils/helpers';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

/* ───────── Types ───────── */

interface HeartbeatConfig {
  enabled: boolean;
  every: string;
  timeout_seconds: number;
  active_hours: { start: string; end: string } | null;
}

interface HeartbeatLastRun {
  status: string;
  at: string;
  summary?: string;
  reason?: string;
  session_id?: string;
}

interface HeartbeatPayload {
  agent_id: string;
  config: HeartbeatConfig;
  query_file_exists: boolean;
  scheduled: boolean;
  next_run_at: string | null;
  last_status: string | null;
  last_run: HeartbeatLastRun | null;
}

interface AgentInfo {
  agent_id: string;
  description?: string;
}

interface FormValues {
  enabled: boolean;
  every: string;
  timeout_seconds: number;
  active_start?: Dayjs | null;
  active_end?: Dayjs | null;
}

const STATUS_COLOR: Record<string, string> = {
  success: 'green',
  skipped: 'default',
  timeout: 'orange',
  error: 'red',
  pending_approval: 'purple',
};

const STATUS_KEY: Record<string, string> = {
  success: 'heartbeat.statusSuccess',
  skipped: 'heartbeat.statusSkipped',
  timeout: 'heartbeat.statusTimeout',
  error: 'heartbeat.statusError',
  pending_approval: 'heartbeat.statusPendingApproval',
};

function formatTime(value?: string | null): string {
  if (!value) return '—';
  const d = dayjs(value);
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : value;
}

export default function HeartbeatPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { agentId: urlAgentId } = useParams<{ agentId?: string }>();
  const [form] = Form.useForm<FormValues>();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentId, setAgentId] = useState<string>('');
  const [payload, setPayload] = useState<HeartbeatPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

  const loadPayload = useCallback(
    async (id: string) => {
      if (!id) return;
      setLoading(true);
      try {
        const res = await apiClient.get(`/agents/${id}/heartbeat`);
        const data = res.data as HeartbeatPayload;
        setPayload(data);
        form.setFieldsValue({
          enabled: data.config?.enabled ?? false,
          every: data.config?.every ?? '30m',
          timeout_seconds: data.config?.timeout_seconds ?? 600,
          active_start: data.config?.active_hours?.start
            ? dayjs(data.config.active_hours.start, 'HH:mm')
            : null,
          active_end: data.config?.active_hours?.end
            ? dayjs(data.config.active_hours.end, 'HH:mm')
            : null,
        });
      } catch (error) {
        setPayload(null);
        message.error(
          `${t('heartbeat.loadFailed')}：${extractErrorMessage(error)}`,
        );
      } finally {
        setLoading(false);
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
          // Prefer the agent from the URL (/agents/:agentId/heartbeat),
          // then the seeded ``default`` agent.
          const preferred =
            (urlAgentId && list.find((a) => a.agent_id === urlAgentId)) ||
            list.find((a) => a.agent_id === 'default') ||
            list[0];
          setAgentId(preferred.agent_id);
          void loadPayload(preferred.agent_id);
        }
      })
      .catch(() => setAgents([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Follow external agent switches (e.g. the sidebar AgentSelector) that
  // only change the URL path.
  useEffect(() => {
    if (urlAgentId && urlAgentId !== agentId && agents.some((a) => a.agent_id === urlAgentId)) {
      setAgentId(urlAgentId);
      setPayload(null);
      void loadPayload(urlAgentId);
    }
  }, [urlAgentId, agentId, agents, loadPayload]);

  const handleAgentChange = (id: string) => {
    setAgentId(id);
    setPayload(null);
    void loadPayload(id);
    if (id !== urlAgentId) navigate(`/agents/${id}/heartbeat`);
  };

  const handleSave = async () => {
    let values: FormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const start = values.active_start?.format('HH:mm') ?? '';
    const end = values.active_end?.format('HH:mm') ?? '';
    if (Boolean(start) !== Boolean(end)) {
      message.error(t('heartbeat.activeHoursIncomplete'));
      return;
    }
    setSaving(true);
    try {
      await apiClient.put(`/agents/${agentId}/heartbeat`, {
        enabled: values.enabled,
        every: values.every,
        timeout_seconds: values.timeout_seconds,
        active_hours: start && end ? { start, end } : null,
      });
      message.success(t('heartbeat.saveSuccess'));
      void loadPayload(agentId);
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleRunNow = async () => {
    setRunning(true);
    try {
      const res = await apiClient.post(`/agents/${agentId}/heartbeat/run`);
      const record = res.data as HeartbeatLastRun;
      message.info(
        t('heartbeat.runFinished', {
          status: t(STATUS_KEY[record.status] ?? 'heartbeat.statusSuccess'),
        }),
      );
      void loadPayload(agentId);
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div>
      <GooglePageHeader
        icon={<HeartOutlined />}
        title={t('heartbeat.title')}
        subtitle={t('heartbeat.subtitle')}
      />

      <GoogleCard loading={loading}>
        <div style={{ marginBottom: 'var(--google-space-4)' }}>
          <Space wrap align="center">
            <Text strong>{t('heartbeat.agent')}</Text>
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
              <Tag color={payload.scheduled ? 'green' : 'default'}>
                {payload.scheduled
                  ? t('heartbeat.scheduled')
                  : t('heartbeat.notScheduled')}
              </Tag>
            )}
          </Space>
        </div>

        {payload && (
          <Alert
            type={payload.query_file_exists ? 'info' : 'warning'}
            showIcon
            message={
              payload.query_file_exists
                ? t('heartbeat.queryFilePresent')
                : t('heartbeat.queryFileMissing')
            }
            style={{ marginBottom: 'var(--google-space-6)' }}
          />
        )}

        <Form
          form={form}
          layout="vertical"
          style={{ maxWidth: 560 }}
        >
          <Form.Item label={t('heartbeat.enable')}>
            <Form.Item name="enabled" valuePropName="checked" noStyle>
              <Switch checkedChildren="ON" unCheckedChildren="OFF" />
            </Form.Item>
            <div style={{ marginTop: 'var(--google-space-2)' }}>
              <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
                {t('heartbeat.enableHelp')}
              </Text>
            </div>
          </Form.Item>

          <Form.Item
            name="every"
            label={t('heartbeat.interval')}
            rules={[{ required: true }]}
          >
            <Input placeholder="30m" style={{ maxWidth: 200 }} />
          </Form.Item>
          <Form.Item style={{ marginTop: 'calc(-1 * var(--google-space-4))' }}>
            <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
              {t('heartbeat.intervalHelp')}
            </Text>
          </Form.Item>

          <Form.Item
            name="timeout_seconds"
            label={t('heartbeat.timeout')}
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={3600} style={{ width: 200 }} />
          </Form.Item>
          <Form.Item style={{ marginTop: 'calc(-1 * var(--google-space-4))' }}>
            <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
              {t('heartbeat.timeoutHelp')}
            </Text>
          </Form.Item>

          <Form.Item label={t('heartbeat.activeHours')}>
            <Space>
              <Form.Item name="active_start" noStyle>
                <TimePicker
                  format="HH:mm"
                  allowClear
                  placeholder={t('heartbeat.activeStart')}
                />
              </Form.Item>
              <Text style={{ color: 'var(--google-muted-foreground)' }}>–</Text>
              <Form.Item name="active_end" noStyle>
                <TimePicker
                  format="HH:mm"
                  allowClear
                  placeholder={t('heartbeat.activeEnd')}
                />
              </Form.Item>
            </Space>
            <div style={{ marginTop: 'var(--google-space-2)' }}>
              <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
                {t('heartbeat.activeHoursHelp')}
              </Text>
            </div>
          </Form.Item>

          <Space style={{ marginTop: 'var(--google-space-2)' }}>
            <Button type="primary" onClick={handleSave} loading={saving}>
              {t('heartbeat.save')}
            </Button>
            <Button
              icon={<PlayCircleOutlined />}
              onClick={handleRunNow}
              loading={running}
            >
              {t('heartbeat.runNow')}
            </Button>
          </Space>
        </Form>

        {payload && (
          <div style={{ marginTop: 'var(--google-space-8)' }}>
            <Text strong>{t('heartbeat.nextRun')}</Text>
            <Text style={{ marginLeft: 'var(--google-space-3)' }}>
              {formatTime(payload.next_run_at)}
            </Text>
            <div style={{ marginTop: 'var(--google-space-2)' }}>
              <Text strong>{t('heartbeat.lastRun')}</Text>
              {payload.last_run ? (
                <span style={{ marginLeft: 'var(--google-space-3)' }}>
                  <Tag color={STATUS_COLOR[payload.last_run.status] ?? 'default'}>
                    {t(
                      STATUS_KEY[payload.last_run.status] ??
                        'heartbeat.statusSuccess',
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
                      {t('heartbeat.goToApproval')}
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
                  {t('heartbeat.none')}
                </Text>
              )}
            </div>
          </div>
        )}
      </GoogleCard>
    </div>
  );
}
