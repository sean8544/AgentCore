import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Collapse,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message as antdMessage,
} from 'antd';
import {
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  ExperimentOutlined,
  PlusOutlined,
  StarFilled,
  WarningOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useAgentStore } from '../../stores/agentStore';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;
const { TextArea } = Input;
const PROVIDER_OPTIONS = [
  { value: 'openai', labelKey: 'providerOpenaiCompat' },
  { value: 'azure', labelKey: 'providerAzure' },
  { value: 'qwen', labelKey: 'providerQwen' },
  { value: 'custom', labelKey: 'providerCustom' },
];

/* ───────── Types ───────── */
interface RegistryModel {
  id: string;
  name: string;
  provider: string;
  base_url: string;
  api_key_env: string;
  api_key_set: boolean;
  headers: { key: string; value: string }[];
  extra_body: Record<string, unknown>;
  is_default: boolean;
  agents: string[];
}

interface InUseModel {
  provider: string;
  name: string;
  base_url: string;
  agents: string[];
}

type TestState = 'idle' | 'testing' | 'ok' | 'failed' | 'warning';

interface FormValues {
  name: string;
  provider: string;
  base_url: string;
  api_key: string;
  api_key_env: string;
  headers: { key: string; value: string }[];
  extraBodyText: string;
  is_default: boolean;
}

const EMPTY_FORM: FormValues = {
  name: '',
  provider: 'openai',
  base_url: '',
  api_key: '',
  api_key_env: '',
  headers: [],
  extraBodyText: '',
  is_default: false,
};

const keyOf = (m: { provider: string; name: string; base_url: string }) =>
  `${m.provider}|${m.name}|${m.base_url}`;

/** Extract a human-readable detail from an axios/network error. */
const extractErrorDetail = (err: unknown): string | undefined => {
  const e = err as {
    response?: { data?: { detail?: string; error?: string } };
    message?: string;
  };
  return e?.response?.data?.detail ?? e?.response?.data?.error ?? e?.message;
};

/** Long upstream errors (e.g. raw 429 JSON) are truncated for toasts. */
const shortError = (s?: string) =>
  s && s.length > 120 ? `${s.slice(0, 120)}…` : s;

export default function ModelsPage() {
  const agents = useAgentStore((s) => s.agents);
  const refreshAgents = useAgentStore((s) => s.refreshAgents);
  const { t } = useI18n();

  const [models, setModels] = useState<RegistryModel[]>([]);
  const [inUse, setInUse] = useState<InUseModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});
  const [testErrors, setTestErrors] = useState<Record<string, string>>({});
  const [assigningKey, setAssigningKey] = useState<string | null>(null);

  /* ── Modal state ── */
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<RegistryModel | null>(null);
  const [form] = Form.useForm<FormValues>();
  const [saving, setSaving] = useState(false);
  const [modalTest, setModalTest] = useState<{ state: TestState; detail?: string }>({
    state: 'idle',
  });

  /* ── Load ── */
  const loadModels = useCallback(async () => {
    setLoading(true);
    try {
      const [libRes, inUseRes] = await Promise.all([
        apiClient.get('/models/library'),
        apiClient.get('/models'),
      ]);
      setModels(libRes.data.models ?? []);
      setInUse(inUseRes.data.models ?? []);
    } catch {
      setModels([]);
      setInUse([]);
      antdMessage.error(t('models.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadModels();
    void refreshAgents();
  }, [loadModels, refreshAgents]);

  /* ── Merge "used by" info from the aggregated endpoint ── */
  const modelsWithAgents = useMemo(() => {
    const usage = new Map<string, string[]>();
    inUse.forEach((m) => usage.set(keyOf(m), m.agents ?? []));
    return models.map((m) => ({
      ...m,
      agents: usage.get(keyOf(m)) ?? [],
    }));
  }, [models, inUse]);

  /* ── Agent options for assignment ── */
  const agentOptions = useMemo(() => {
    const ids = new Set<string>(agents.map((a) => a.agent_id));
    modelsWithAgents.forEach((m) => (m.agents ?? []).forEach((id) => ids.add(id)));
    return Array.from(ids).sort().map((id) => ({ value: id, label: id }));
  }, [agents, modelsWithAgents]);

  /* ── Assign model to an agent ── */
  const assignToAgent = useCallback(
    async (model: RegistryModel, agentId: string) => {
      const key = `${model.id}->${agentId}`;
      setAssigningKey(key);
      try {
        await apiClient.put(`/agents/${agentId}/model`, {
          provider: model.provider,
          name: model.name,
          ...(model.base_url ? { base_url: model.base_url } : {}),
          ...(model.api_key_env ? { api_key_env: model.api_key_env } : {}),
          ...(model.headers?.length ? { headers: model.headers } : {}),
          ...(Object.keys(model.extra_body ?? {}).length
            ? { extra_body: model.extra_body }
            : {}),
        });
        antdMessage.success(t('models.assignSuccess', { name: model.name, agent: agentId }));
        await Promise.all([loadModels(), refreshAgents()]);
      } catch (err) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        antdMessage.error(detail || t('models.assignFailed'));
      } finally {
        setAssigningKey(null);
      }
    },
    [loadModels, refreshAgents, t],
  );

  /* ── Connection test (row-level) ── */
  const testModel = useCallback(
    async (model: RegistryModel) => {
      const key = model.id;
      setTestStates((prev) => ({ ...prev, [key]: 'testing' }));
      try {
        const res = await apiClient.post('/models/test', {
          provider: model.provider,
          name: model.name,
          base_url: model.base_url,
          api_key_env: model.api_key_env || undefined,
          headers: model.headers,
          extra_body: model.extra_body,
        });
        const result: string = res.data.result;
        setTestStates((prev) => ({ ...prev, [key]: (result as TestState) ?? 'ok' }));
        setTestErrors((prev) => ({ ...prev, [key]: res.data.error ?? '' }));
        if (result === 'ok') {
          antdMessage.success(t('models.connectionOk', { name: model.name }));
        } else if (result === 'warning') {
          antdMessage.warning(res.data.error ?? t('models.connectionWarning'));
        } else {
          antdMessage.error(res.data.error ?? t('models.connectionFailed'));
        }
      } catch (err) {
        const detail = extractErrorDetail(err) ?? t('models.testFailed');
        setTestStates((prev) => ({ ...prev, [key]: 'failed' }));
        setTestErrors((prev) => ({ ...prev, [key]: detail }));
        antdMessage.error(shortError(detail));
      }
    },
    [t],
  );

  /* ── Default model actions ── */
  const setDefaultModel = useCallback(
    async (model: RegistryModel, isDefault: boolean) => {
      try {
        await apiClient.put(`/models/${model.id}/default`, { is_default: isDefault });
        antdMessage.success(
          isDefault
            ? t('models.defaultSet', { name: model.name })
            : t('models.defaultModelCleared'),
        );
        await loadModels();
      } catch (err) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        antdMessage.error(detail || t('models.saveFailed'));
      }
    },
    [loadModels, t],
  );

  const deleteModel = useCallback(
    async (model: RegistryModel) => {
      try {
        await apiClient.delete(`/models/${model.id}`);
        antdMessage.success(t('models.deleted'));
        await loadModels();
      } catch (err) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        antdMessage.error(detail || t('models.deleteFailed'));
      }
    },
    [loadModels, t],
  );

  /* ── Modal helpers ── */
  const openCreateModal = () => {
    setEditing(null);
    setModalTest({ state: 'idle' });
    form.setFieldsValue(EMPTY_FORM);
    setModalOpen(true);
  };

  const openEditModal = (model: RegistryModel) => {
    setEditing(model);
    setModalTest({ state: 'idle' });
    form.setFieldsValue({
      name: model.name,
      provider: model.provider || 'openai',
      base_url: model.base_url,
      api_key: '',
      api_key_env: model.api_key_env ?? '',
      headers: model.headers?.length ? model.headers : [{ key: '', value: '' }],
      extraBodyText:
        model.extra_body && Object.keys(model.extra_body).length
          ? JSON.stringify(model.extra_body, null, 2)
          : '',
      is_default: model.is_default,
    });
    setModalOpen(true);
  };

  const testFromForm = useCallback(async () => {
    const values = form.getFieldsValue();
    if (!values.name.trim() || !values.base_url.trim()) {
      antdMessage.warning(t('models.baseUrlRequired'));
      return;
    }
    setModalTest({ state: 'testing' });
    try {
      // ``headers`` / ``extraBodyText`` live inside a Collapse — when it
      // stays collapsed the fields are unregistered and read as undefined.
      let extraBody: Record<string, unknown> | undefined;
      const extraText = values.extraBodyText?.trim();
      if (extraText) {
        try {
          extraBody = JSON.parse(extraText);
        } catch {
          antdMessage.warning(t('models.invalidJson'));
          setModalTest({ state: 'idle' });
          return;
        }
      }
      const res = await apiClient.post('/models/test', {
        name: values.name.trim(),
        base_url: values.base_url.trim(),
        api_key: values.api_key.trim() || undefined,
        api_key_env: values.api_key_env.trim() || undefined,
        headers: (values.headers ?? []).filter((h) => h?.key?.trim()),
        extra_body: extraBody,
      });
      const result: string = res.data.result;
      if (result === 'ok') {
        const names = (res.data.available_models ?? []) as string[];
        setModalTest({
          state: 'ok',
          detail:
            names.length > 0
              ? t('models.availableModels', { names: names.slice(0, 8).join(', ') })
              : undefined,
        });
        antdMessage.success(t('models.testOk'));
      } else if (result === 'warning') {
        setModalTest({ state: 'warning', detail: res.data.error });
        antdMessage.warning(shortError(res.data.error) ?? t('models.connectionWarning'));
      } else {
        setModalTest({ state: 'failed', detail: res.data.error });
        antdMessage.error(shortError(res.data.error) ?? t('models.connectionFailed'));
      }
    } catch (err) {
      const detail = extractErrorDetail(err) ?? t('models.testFailed');
      setModalTest({ state: 'failed', detail });
      antdMessage.error(shortError(detail));
    }
  }, [form, t]);

  const saveModel = useCallback(async () => {
    const values = await form.validateFields();
    if (!values.name.trim()) {
      antdMessage.error(t('models.nameRequired'));
      return;
    }
    if (!values.base_url.trim()) {
      antdMessage.error(t('models.baseUrlRequired'));
      return;
    }
    let extraBody: Record<string, unknown> | undefined;
    const extraText = values.extraBodyText?.trim();
    if (extraText) {
      try {
        const parsed = JSON.parse(extraText);
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          throw new Error('not-object');
        }
        extraBody = parsed;
      } catch {
        antdMessage.error(t('models.invalidJson'));
        return;
      }
    }

    const payload = {
      name: values.name.trim(),
      provider: values.provider,
      base_url: values.base_url.trim(),
      ...(values.api_key.trim() ? { api_key: values.api_key.trim() } : {}),
      ...(values.api_key_env.trim() ? { api_key_env: values.api_key_env.trim() } : {}),
      headers: (values.headers ?? []).filter((h) => h?.key?.trim()),
      extra_body: extraBody ?? {},
      is_default: values.is_default,
    };

    setSaving(true);
    try {
      if (editing) {
        await apiClient.put(`/models/${editing.id}`, payload);
      } else {
        await apiClient.post('/models', payload);
      }
      antdMessage.success(t('models.saved'));
      setModalOpen(false);
      await loadModels();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail || t('models.saveFailed'));
    } finally {
      setSaving(false);
    }
  }, [editing, form, loadModels, t]);

  /* ── Columns ── */
  const columns: ColumnsType<RegistryModel> = [
    {
      title: t('models.model'),
      dataIndex: 'name',
      render: (name: string, record) => (
        <Space direction="vertical" size={0}>
          <Space size={4}>
            {record.is_default && (
              <Tooltip title={t('models.defaultHint')}>
                <StarFilled style={{ color: 'var(--google-chart-3)', fontSize: 13 }} />
              </Tooltip>
            )}
            <Text strong style={{ fontSize: 13 }}>{name}</Text>
          </Space>
          <Text type="secondary" style={{ fontSize: 11 }}>{record.provider}</Text>
        </Space>
      ),
    },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      ellipsis: true,
      render: (url: string) => <Text code style={{ fontSize: 12 }}>{url || '—'}</Text>,
    },
    {
      title: 'API Key',
      dataIndex: 'api_key_set',
      width: 110,
      render: (set: boolean, record) => (
        <Tooltip
          title={
            record.api_key_env
              ? t('models.envVar', { var: record.api_key_env })
              : record.api_key_set
                ? t('models.apiKeyHint')
                : undefined
          }
        >
          {set
            ? <Badge status="success" text={<Text style={{ fontSize: 12 }}>{t('models.apiConfigured')}</Text>} />
            : <Badge status="error" text={<Text style={{ fontSize: 12 }}>{t('models.apiNotConfigured')}</Text>} />}
        </Tooltip>
      ),
    },
    {
      title: t('models.default'),
      width: 110,
      align: 'center',
      render: (_, record) =>
        record.is_default ? (
          <Tag color="gold" style={{ fontSize: 11 }}>{t('models.default')}</Tag>
        ) : (
          <Button size="small" type="link" style={{ fontSize: 11 }} onClick={() => void setDefaultModel(record, true)}>
            {t('models.setDefault')}
          </Button>
        ),
    },
    {
      title: t('models.usedByAgents'),
      dataIndex: 'agents',
      render: (agentIds: string[]) => (
        <Space size={4} wrap>
          {(agentIds ?? []).map((id) => (
            <Tag key={id} style={{ fontSize: 11, marginInlineEnd: 0 }}>{id}</Tag>
          ))}
          {!(agentIds ?? []).length && <Text type="secondary" style={{ fontSize: 11 }}>—</Text>}
        </Space>
      ),
    },
    {
      title: t('models.assignToAgent'),
      width: 190,
      render: (_, record) => (
        <Select
          size="small"
          placeholder={t('models.selectAgent')}
          style={{ width: 160 }}
          showSearch
          options={agentOptions}
          loading={assigningKey?.startsWith(`${record.id}->`) ?? false}
          onChange={(agentId: string) => void assignToAgent(record, agentId)}
        />
      ),
    },
    {
      title: t('models.connectionTest'),
      width: 180,
      align: 'center',
      render: (_, record) => {
        const state = testStates[record.id] ?? 'idle';
        return (
          <Space size={6}>
            <Button
              size="small"
              icon={<ExperimentOutlined />}
              loading={state === 'testing'}
              onClick={() => void testModel(record)}
            >
              {t('models.test')}
            </Button>
            {state === 'ok' && <CheckCircleOutlined style={{ color: 'var(--google-chart-5)' }} />}
            {(state === 'warning' || state === 'failed') && (
              <Tooltip title={testErrors[record.id]}>
                <CloseCircleOutlined style={{ color: state === 'warning' ? 'var(--google-chart-3)' : 'var(--google-destructive)' }} />
              </Tooltip>
            )}
          </Space>
        );
      },
    },
    {
      title: t('models.operation'),
      width: 110,
      align: 'center',
      render: (_, record) => (
        <Space size={0}>
          <Button
            size="small"
            type="text"
            icon={<EditOutlined />}
            onClick={() => openEditModal(record)}
          />
          <Popconfirm
            title={t('models.deleteConfirm', { name: record.name })}
            okText={t('models.delete')}
            cancelText={t('models.cancel')}
            onConfirm={() => void deleteModel(record)}
          >
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  /* ── Modal ── */
  const modalTestAlert =
    modalTest.state === 'ok' ? (
      <Alert type="success" showIcon message={modalTest.detail ?? t('models.testOk')} style={{ marginBottom: 'var(--google-space-4)' }} />
    ) : modalTest.state === 'warning' ? (
      <Alert type="warning" showIcon icon={<WarningOutlined />} message={modalTest.detail ?? t('models.connectionWarning')} style={{ marginBottom: 'var(--google-space-4)' }} />
    ) : modalTest.state === 'failed' ? (
      <Alert type="error" showIcon message={t('models.testFailedDetail', { error: modalTest.detail ?? '' })} style={{ marginBottom: 'var(--google-space-4)' }} />
    ) : null;

  const isEdit = editing !== null;

  return (
    <div>
      <GooglePageHeader
        icon={<ApiOutlined />}
        title={t('models.title')}
        subtitle={t('models.subtitle')}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
            {t('models.addModel')}
          </Button>
        }
      />

      <GoogleCard bodyStyle={{ padding: 0 }}>
        {!loading && models.length === 0 && (
          <Alert
            type="info"
            showIcon
            message={t('models.noRegistryHint')}
            style={{ margin: 'var(--google-space-6)', marginBottom: 0 }}
          />
        )}
        <Table<RegistryModel>
          rowKey={(m) => m.id}
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={modelsWithAgents}
          pagination={false}
          locale={{ emptyText: t('models.noModels') }}
        />
      </GoogleCard>

      <Modal
        title={isEdit ? t('models.editModel') : t('models.addModel')}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        width={680}
        maskClosable={false}
        destroyOnClose
        footer={
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Button
              icon={<ExperimentOutlined />}
              loading={modalTest.state === 'testing'}
              onClick={() => void testFromForm()}
            >
              {modalTest.state === 'testing' ? t('models.testing') : t('models.testConnection')}
            </Button>
            <Space>
              <Button onClick={() => setModalOpen(false)}>{t('models.cancel')}</Button>
              <Button type="primary" loading={saving} onClick={() => void saveModel()}>
                {t('models.save')}
              </Button>
            </Space>
          </Space>
        }
      >
        {modalTestAlert}
        <Form form={form} layout="vertical" initialValues={EMPTY_FORM}>
          <Space style={{ width: '100%' }} size={12} align="start">
            <Form.Item
              name="name"
              label={t('models.modelName')}
              style={{ flex: 1 }}
              rules={[{ required: true, message: t('models.nameRequired') }]}
            >
              <Input placeholder={t('models.modelNamePlaceholder')} />
            </Form.Item>
            <Form.Item name="provider" label={t('models.provider')} style={{ width: 200 }}>
              <Select
                options={PROVIDER_OPTIONS.map((o) => ({
                  value: o.value,
                  label: t(`models.${o.labelKey}`),
                }))}
              />
            </Form.Item>
          </Space>
          <Form.Item
            name="base_url"
            label={t('models.baseUrl')}
            rules={[{ required: true, message: t('models.baseUrlRequired') }]}
            extra={t('models.baseUrlHint')}
          >
            <Input placeholder={t('models.baseUrlPlaceholder')} />
          </Form.Item>
          <Form.Item
            name="api_key"
            label={t('models.apiKey')}
            extra={isEdit && editing?.api_key_set ? t('models.apiKeyHint') : undefined}
          >
            <Input.Password placeholder={t('models.apiKeyPlaceholder')} autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="api_key_env" label={t('models.apiKeyEnv')}>
            <Input placeholder={t('models.apiKeyEnvPlaceholder')} />
          </Form.Item>

          <Collapse
            ghost
            style={{ marginBottom: 'var(--google-space-3)', paddingInline: 0 }}
            items={[
              {
                key: 'advanced',
                label: t('models.advanced'),
                children: (
                  <>
                    <Text strong style={{ fontSize: 12, display: 'block', marginBottom: 'var(--google-space-2)' }}>
                      {t('models.customHeaders')}
                    </Text>
                    <Form.List name="headers">
                      {(fields, { add, remove }) => (
                        <>
                          {fields.map((field) => (
                            <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 'var(--google-space-3)' }}>
                              <Form.Item
                                name={[field.name, 'key']}
                                style={{ marginBottom: 0, width: 220 }}
                                rules={[{ required: true, message: t('models.headerKey') }]}
                              >
                                <Input placeholder={t('models.headerKey')} size="small" />
                              </Form.Item>
                              <Form.Item name={[field.name, 'value']} style={{ marginBottom: 0, width: 260 }}>
                                <Input placeholder={t('models.headerValue')} size="small" />
                              </Form.Item>
                              <Button
                                type="text"
                                size="small"
                                icon={<DeleteOutlined />}
                                onClick={() => remove(field.name)}
                              />
                            </Space>
                          ))}
                          <Button type="link" size="small" style={{ paddingLeft: 0 }} onClick={() => add({ key: '', value: '' })}>
                            {t('models.addHeader')}
                          </Button>
                        </>
                      )}
                    </Form.List>
                    <Text type="secondary" style={{ fontSize: 11 }}>{t('models.headerHint')}</Text>
                    <div style={{ marginTop: 'var(--google-space-6)' }}>
                      <Text strong style={{ fontSize: 12, display: 'block', marginBottom: 'var(--google-space-2)' }}>
                        {t('models.generationParams')}
                      </Text>
                      <Form.Item name="extraBodyText" style={{ marginBottom: 'var(--google-space-2)' }}>
                        <TextArea
                          rows={4}
                          placeholder={'{\n  "extra_body": {\n    "enable_thinking": false,\n    "max_tokens": 2048\n  }\n}'}
                          style={{ fontFamily: 'var(--google-font-mono)', fontSize: 12 }}
                        />
                      </Form.Item>
                      <Text type="secondary" style={{ fontSize: 11 }}>{t('models.generationParamsHint')}</Text>
                    </div>
                  </>
                ),
              },
            ]}
          />

          <Form.Item name="is_default" valuePropName="checked" style={{ marginBottom: 0 }}>
            <Switch checkedChildren={t('models.default')} unCheckedChildren={t('models.default')} />
            <Text style={{ marginLeft: 'var(--google-space-4)', fontSize: 12 }}>{t('models.isDefault')}</Text>
            <Text type="secondary" style={{ marginLeft: 'var(--google-space-4)', fontSize: 11 }}>{t('models.defaultHint')}</Text>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
