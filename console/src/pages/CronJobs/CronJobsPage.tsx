import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  DatePicker,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message as antdMessage,
} from 'antd';
import {
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { Dayjs } from 'dayjs';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

/* ───────── Types ───────── */

interface ScheduleSpec {
  type: 'cron' | 'once';
  cron?: string | null;
  run_at?: string | null;
  timezone: string;
}

interface RuntimeSpec {
  max_concurrency: number;
  timeout_seconds: number;
  misfire_grace_seconds: number;
  share_session: boolean;
}

interface CronJobSpec {
  id?: string | null;
  name: string;
  enabled: boolean;
  agent_id: string;
  message: string;
  schedule: ScheduleSpec;
  runtime: RuntimeSpec;
  meta?: Record<string, unknown>;
}

interface CronJobState {
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_status?: string | null;
  last_error?: string | null;
  last_session_id?: string | null;
}

interface CronJobView {
  spec: CronJobSpec;
  state: CronJobState;
}

interface CronExecutionRecord {
  run_at: string;
  status: string;
  error?: string | null;
  trigger: 'scheduled' | 'manual';
}

interface AgentInfo {
  agent_id: string;
  description?: string;
}

interface JobFormValues {
  name: string;
  agent_id: string;
  message: string;
  schedule_type: 'cron' | 'once';
  cron?: string;
  run_at?: Dayjs;
  timezone: string;
  timeout_seconds: number;
  share_session: boolean;
}

const TIMEZONES = [
  'Asia/Shanghai',
  'UTC',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Europe/London',
  'America/New_York',
];

const CRON_PRESETS: { value: string; labelKey: string }[] = [
  { value: '0 9 * * *', labelKey: 'cronDaily9' },
  { value: '0 * * * *', labelKey: 'cronHourly' },
  { value: '*/5 * * * *', labelKey: 'cronEvery5min' },
  { value: '0 9 * * 1-5', labelKey: 'cronWorkday9' },
  { value: '0 9 * * 1', labelKey: 'cronMonday9' },
  { value: '0 0 1 * *', labelKey: 'cronMonthly' },
];

const STATUS_COLOR: Record<string, string> = {
  success: 'green',
  error: 'red',
  running: 'blue',
  skipped: 'orange',
  interrupted: 'purple',
  cancelled: 'default',
};

function formatTime(value?: string | null): string {
  if (!value) return '—';
  const d = dayjs(value);
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : value;
}

function describeSchedule(t: (key: string) => string, schedule: ScheduleSpec): string {
  if (schedule.type === 'once' && schedule.run_at) {
    return `${t('cron.scheduleOnce')} ${formatTime(schedule.run_at)}`;
  }
  return schedule.cron ?? '—';
}

export default function CronJobsPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<CronJobView[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [historyJob, setHistoryJob] = useState<CronJobSpec | null>(null);
  const [historyRecords, setHistoryRecords] = useState<CronExecutionRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [form] = Form.useForm<JobFormValues>();
  const scheduleType = Form.useWatch('schedule_type', form);

  /* ── Load ── */

  const loadJobs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get('/cron/jobs');
      setJobs(res.data ?? []);
    } catch {
      setJobs([]);
      antdMessage.error(t('cron.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAgents = useCallback(async () => {
    try {
      const res = await apiClient.get('/agents');
      setAgents(res.data ?? []);
    } catch {
      setAgents([]);
    }
  }, []);

  useEffect(() => {
    void loadJobs();
    void loadAgents();
  }, [loadJobs, loadAgents]);

  /* ── Create / Edit ── */

  const openCreate = () => {
    setEditingId(null);
    form.resetFields();
    form.setFieldsValue({
      agent_id: agents[0]?.agent_id ?? 'default',
      schedule_type: 'cron',
      cron: '0 9 * * *',
      timezone: 'Asia/Shanghai',
      timeout_seconds: 600,
      share_session: true,
    });
    setModalOpen(true);
  };

  const openEdit = (view: CronJobView) => {
    const { spec } = view;
    setEditingId(spec.id ?? null);
    form.resetFields();
    form.setFieldsValue({
      name: spec.name,
      agent_id: spec.agent_id,
      message: spec.message,
      schedule_type: spec.schedule.type,
      cron: spec.schedule.cron ?? '0 9 * * *',
      run_at: spec.schedule.run_at ? dayjs(spec.schedule.run_at) : undefined,
      timezone: spec.schedule.timezone || 'Asia/Shanghai',
      timeout_seconds: spec.runtime.timeout_seconds,
      share_session: spec.runtime.share_session,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    let values: JobFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const payload: CronJobSpec = {
      name: values.name.trim(),
      enabled: true,
      agent_id: values.agent_id,
      message: values.message.trim(),
      schedule:
        values.schedule_type === 'cron'
          ? {
              type: 'cron',
              cron: (values.cron ?? '').trim(),
              timezone: values.timezone,
            }
          : {
              type: 'once',
              run_at: values.run_at?.toISOString() ?? null,
              timezone: values.timezone,
            },
      runtime: {
        max_concurrency: 1,
        timeout_seconds: values.timeout_seconds,
        misfire_grace_seconds: 600,
        share_session: values.share_session,
      },
    };
    setSaving(true);
    try {
      if (editingId) {
        await apiClient.put(`/cron/jobs/${editingId}`, payload);
      } else {
        await apiClient.post('/cron/jobs', payload);
      }
      antdMessage.success(t('cron.saved'));
      setModalOpen(false);
      void loadJobs();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      antdMessage.error(
        typeof detail === 'string' ? detail : t('cron.saveFailed'),
      );
    } finally {
      setSaving(false);
    }
  };

  /* ── Control actions ── */

  const handleToggleEnabled = async (view: CronJobView, enabled: boolean) => {
    const id = view.spec.id;
    if (!id) return;
    try {
      await apiClient.post(
        `/cron/jobs/${id}/${enabled ? 'resume' : 'pause'}`,
      );
      void loadJobs();
    } catch {
      antdMessage.error(t('common.operationFailed'));
    }
  };

  const handleRunNow = async (id: string) => {
    setRunningId(id);
    try {
      await apiClient.post(`/cron/jobs/${id}/run`);
      antdMessage.info(t('cron.runStarted'));
      // 执行为后台任务 —— 稍后刷新以展示最新状态。
      setTimeout(() => void loadJobs(), 1500);
    } catch {
      antdMessage.error(t('common.operationFailed'));
    } finally {
      setRunningId(null);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    try {
      await apiClient.delete(`/cron/jobs/${id}`);
      antdMessage.success(t('common.deletedSuccess', { name }));
      void loadJobs();
    } catch {
      antdMessage.error(t('common.deleteFailed'));
    }
  };

  /* ── History ── */

  const openHistory = async (job: CronJobSpec) => {
    setHistoryJob(job);
    setHistoryLoading(true);
    try {
      const res = await apiClient.get(`/cron/jobs/${job.id}/history`);
      setHistoryRecords([...(res.data ?? [])].reverse());
    } catch {
      setHistoryRecords([]);
      antdMessage.error(t('cron.loadFailed'));
    } finally {
      setHistoryLoading(false);
    }
  };

  /* ── Table columns ── */

  const columns: ColumnsType<CronJobView> = [
    {
      title: t('cron.jobName'),
      dataIndex: ['spec', 'name'],
      width: 200,
      render: (_: unknown, view) => (
        <Space direction="vertical" size={0}>
          <Text strong>{view.spec.name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }} ellipsis>
            {view.spec.message}
          </Text>
        </Space>
      ),
    },
    {
      title: 'Agent',
      dataIndex: ['spec', 'agent_id'],
      width: 120,
      render: (agentId: string) => <Tag>{agentId}</Tag>,
    },
    {
      title: t('cron.schedule'),
      width: 220,
      render: (_: unknown, view) => (
        <Space direction="vertical" size={0}>
          <Text code style={{ fontSize: 12 }}>
            {describeSchedule(t, view.spec.schedule)}
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {view.spec.schedule.timezone}
          </Text>
        </Space>
      ),
    },
    {
      title: t('cron.nextRun'),
      width: 170,
      render: (_: unknown, view) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {formatTime(view.state.next_run_at)}
        </Text>
      ),
    },
    {
      title: t('cron.lastRun'),
      width: 210,
      render: (_: unknown, view) => {
        const { last_status: status, last_run_at: runAt, last_error: err } =
          view.state;
        return (
          <Space direction="vertical" size={0}>
            <Space size={6}>
              {status ? (
                <Tag color={STATUS_COLOR[status] ?? 'default'}>
                  {t(`cron.status.${status}`)}
                </Tag>
              ) : (
                <Text type="secondary">—</Text>
              )}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {formatTime(runAt)}
              </Text>
            </Space>
            {err && (
              <Text type="danger" style={{ fontSize: 12 }} ellipsis>
                {err}
              </Text>
            )}
            {status === 'interrupted' && view.state.last_session_id && (
              <Button
                type="link"
                size="small"
                icon={<ThunderboltOutlined />}
                style={{ padding: 0, marginTop: 'var(--google-space-1)' }}
                onClick={() => {
                  navigate(`/agents/${view.spec.agent_id}/chat`);
                  setTimeout(() => {
                    import('../../stores/chatStore').then(({ useChatStore }) => {
                      useChatStore.getState().selectSession(view.state.last_session_id!);
                    });
                  }, 500);
                }}
              >
                {t('cron.goToApproval')}
              </Button>
            )}
          </Space>
        );
      },
    },
    {
      title: t('common.actions'),
      width: 230,
      align: 'center',
      render: (_: unknown, view) => {
        const id = view.spec.id ?? '';
        return (
          <Space size="small">
            <Switch
              size="small"
              checked={view.spec.enabled}
              onChange={(checked) => void handleToggleEnabled(view, checked)}
            />
            <Button
              type="text"
              size="small"
              icon={<PlayCircleOutlined />}
              loading={runningId === id}
              onClick={() => void handleRunNow(id)}
            />
            <Button
              type="text"
              size="small"
              icon={<HistoryOutlined />}
              onClick={() => void openHistory(view.spec)}
            />
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEdit(view)}
            />
            <Popconfirm
              title={t('cron.deleteConfirm', { name: view.spec.name })}
              onConfirm={() => void handleDelete(id, view.spec.name)}
              okText={t('common.delete')}
              cancelText={t('common.cancel')}
              okButtonProps={{ danger: true }}
            >
              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const historyColumns: ColumnsType<CronExecutionRecord> = [
    {
      title: t('cron.historyRunAt'),
      dataIndex: 'run_at',
      width: 180,
      render: (v: string) => (
        <Text style={{ fontSize: 12 }}>{formatTime(v)}</Text>
      ),
    },
    {
      title: t('common.status'),
      dataIndex: 'status',
      width: 110,
      render: (status: string) => (
        <Tag color={STATUS_COLOR[status] ?? 'default'}>
          {t(`cron.status.${status}`)}
        </Tag>
      ),
    },
    {
      title: t('cron.historyTrigger'),
      dataIndex: 'trigger',
      width: 100,
      render: (trigger: string) => t(`cron.trigger.${trigger}`),
    },
    {
      title: t('cron.historyError'),
      dataIndex: 'error',
      ellipsis: true,
      render: (err?: string | null) =>
        err ? (
          <Text type="danger" style={{ fontSize: 12 }}>
            {err}
          </Text>
        ) : (
          '—'
        ),
    },
  ];

  return (
    <div>
      <GooglePageHeader
        icon={<ClockCircleOutlined />}
        title={t('cron.title')}
        subtitle={t('cron.subtitle')}
        extra={
          <Button
            size="small"
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreate}
          >
            {t('cron.createJob')}
          </Button>
        }
      />

      <GoogleCard bodyStyle={{ padding: 0 }}>
        <Table<CronJobView>
          rowKey={(view) => view.spec.id ?? view.spec.name}
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={jobs}
          pagination={false}
          locale={{ emptyText: t('cron.emptyHint') }}
        />
      </GoogleCard>

      <Modal
        title={editingId ? t('cron.editJob') : t('cron.createJob')}
        open={modalOpen}
        onOk={() => void handleSave()}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        okText={t('common.save')}
        cancelText={t('common.cancel')}
        width={620}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 'var(--google-space-4)' }}>
          <Form.Item
            name="name"
            label={t('cron.jobName')}
            rules={[{ required: true, message: t('cron.nameRequired') }]}
          >
            <Input placeholder={t('cron.namePlaceholder')} />
          </Form.Item>

          <Form.Item
            name="agent_id"
            label={t('cron.agent')}
            rules={[{ required: true, message: t('cron.agentRequired') }]}
          >
            <Select
              options={agents.map((a) => ({
                value: a.agent_id,
                label: a.description ? `${a.agent_id} — ${a.description}` : a.agent_id,
              }))}
            />
          </Form.Item>

          <Form.Item
            name="message"
            label={t('cron.message')}
            rules={[{ required: true, message: t('cron.messageRequired') }]}
          >
            <Input.TextArea rows={3} placeholder={t('cron.messagePlaceholder')} />
          </Form.Item>

          <Form.Item name="schedule_type" label={t('cron.scheduleType')}>
            <Radio.Group
              options={[
                { value: 'cron', label: t('cron.scheduleTypeCron') },
                { value: 'once', label: t('cron.scheduleTypeOnce') },
              ]}
            />
          </Form.Item>

          {scheduleType === 'once' ? (
            <Form.Item
              name="run_at"
              label={t('cron.runAt')}
              rules={[{ required: true, message: t('cron.runAtRequired') }]}
            >
              <DatePicker showTime style={{ width: '100%' }} />
            </Form.Item>
          ) : (
            <Form.Item
              label={t('cron.cronExpr')}
              required
              extra={t('cron.cronHelp')}
            >
              <Space.Compact style={{ width: '100%' }}>
                <Form.Item
                  name="cron"
                  noStyle
                  rules={[{ required: true, message: t('cron.cronRequired') }]}
                >
                  <Input placeholder="0 9 * * *" style={{ width: '60%' }} />
                </Form.Item>
                <Select
                  style={{ width: '40%' }}
                  placeholder={t('cron.cronPresets')}
                  options={CRON_PRESETS.map((p) => ({
                    value: p.value,
                    label: t(`cron.${p.labelKey}`),
                  }))}
                  onChange={(value) => form.setFieldValue('cron', value)}
                />
              </Space.Compact>
            </Form.Item>
          )}

          <Form.Item name="timezone" label={t('cron.timezone')}>
            <Select options={TIMEZONES.map((tz) => ({ value: tz, label: tz }))} />
          </Form.Item>

          <Space size="large">
            <Form.Item name="timeout_seconds" label={t('cron.timeoutSeconds')}>
              <InputNumber min={1} max={86400} />
            </Form.Item>
            <Form.Item
              name="share_session"
              label={t('cron.shareSession')}
              valuePropName="checked"
              tooltip={t('cron.shareSessionTip')}
            >
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Drawer
        title={`${t('cron.history')} — ${historyJob?.name ?? ''}`}
        open={historyJob !== null}
        onClose={() => setHistoryJob(null)}
        width={720}
      >
        <Table<CronExecutionRecord>
          rowKey={(r) => `${r.run_at}-${r.trigger}`}
          size="small"
          loading={historyLoading}
          columns={historyColumns}
          dataSource={historyRecords}
          pagination={{ pageSize: 10 }}
          locale={{ emptyText: t('cron.historyEmpty') }}
        />
      </Drawer>
    </div>
  );
}
