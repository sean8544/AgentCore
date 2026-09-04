import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Select,
  Skeleton,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ApiOutlined,
  ArrowRightOutlined,
  BranchesOutlined,
  CloudServerOutlined,
  CloudSyncOutlined,
  DeploymentUnitOutlined,
  InboxOutlined,
  RollbackOutlined,
  SaveOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { extractErrorMessage, formatDateTime } from '../../utils/helpers';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

/* ───────── Types ───────── */

interface AgentListItem {
  agent_id: string;
  state?: string | null;
  description?: string;
  enable_subagents?: boolean;
  subagents?: { name: string; description?: string }[];
}

interface AgentDetail {
  agent_id: string;
  settings?: {
    description?: string;
    system_prompt?: string;
    enable_subagents?: boolean;
    inherit_parent_tools?: boolean;
    enable_planning?: boolean;
    enable_a2ui?: boolean;
    subagent_ids?: string[];
    interrupt_rules?: { tool_name?: string; require_approval?: boolean }[];
    backend?: {
      type?: string;
      provider?: string;
      image?: string;
      memory_mb?: number;
      lifecycle?: {
        strategy?: string;
        timeout?: number;
        idle_timeout?: number;
        renew_on_chat?: boolean;
        renew_on_execute?: boolean;
        max_resume_count?: number;
      };
    };
  };
  model?: {
    provider?: string;
    name?: string;
    base_url?: string;
  };
  subagents?: { name: string; description?: string }[];
}

interface ModelLibraryItem {
  id: string;
  name: string;
  provider?: string;
  base_url?: string;
  is_default?: boolean;
}

interface DelegationRecord {
  parent_agent_id?: string;
  task_description?: string;
  timestamp?: string;
}

/* ───────── Page ───────── */

export default function AgentConfigPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { agentId: urlAgentId } = useParams<{ agentId?: string }>();

  const [agents, setAgents] = useState<AgentListItem[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string | undefined>(urlAgentId);

  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentDetail | null>(null);

  const [delegations, setDelegations] = useState<DelegationRecord[]>([]);
  const [delegationsLoading, setDelegationsLoading] = useState(false);

  const [modelLibrary, setModelLibrary] = useState<ModelLibraryItem[]>([]);

  const [saving, setSaving] = useState(false);
  /** Last values loaded from the backend — used by the discard button. */
  const initialValuesRef = useRef<Record<string, unknown>>({});
  const [form] = Form.useForm<{
    description?: string;
    system_prompt?: string;
    enable_subagents?: boolean;
    inherit_parent_tools?: boolean;
    enable_planning?: boolean;
    subagent_ids?: string[];
    model_id?: string;
  }>();
  const enableSubagents = Form.useWatch('enable_subagents', form);

  // ─── Sandbox config state ───
  const [sandboxEnabled, setSandboxEnabled] = useState(false);
  const [sandboxProvider, setSandboxProvider] = useState('opensandbox');
  const [sandboxLifecycle, setSandboxLifecycle] = useState('ephemeral');
  const [sandboxImage, setSandboxImage] = useState('ghcr.io/agent-infra/sandbox:latest');
  const [sandboxTimeout, setSandboxTimeout] = useState(600);
  const [sandboxMemoryMb, setSandboxMemoryMb] = useState(512);
  const [sandboxSaving, setSandboxSaving] = useState(false);

  // ─── Agent list (also used to derive parent → subagent topology) ───
  const fetchAgents = useCallback(async () => {
    setAgentsLoading(true);
    try {
      const resp = await apiClient.get<AgentListItem[]>('/agents');
      setAgents(Array.isArray(resp.data) ? resp.data : []);
    } catch (error) {
      message.error(`${t('agents.loadFailed', { error: extractErrorMessage(error) })}`);
    } finally {
      setAgentsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchAgents();
    // Fetch model library for the model selector
    void apiClient
      .get<{ models: ModelLibraryItem[] }>('/models/library')
      .then((resp) => setModelLibrary(resp.data?.models ?? []))
      .catch(() => setModelLibrary([]));
  }, [fetchAgents]);

  // ─── Detail + delegations for the selected agent ───
  useEffect(() => {
    if (!selectedAgentId) {
      setDetail(null);
      setDelegations([]);
      form.resetFields();
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    void apiClient
      .get<AgentDetail>(`/agents/${selectedAgentId}`)
      .then((resp) => {
        setDetail(resp.data);
        const settings = resp.data?.settings ?? {};
        const modelCfg = resp.data?.model ?? {};
        // Find matching model_id from library by name+base_url
        const matchedModel = modelLibrary.find(
          (m) => m.name === modelCfg.name && m.base_url === modelCfg.base_url,
        );
        initialValuesRef.current = {
          description: settings.description ?? '',
          system_prompt: settings.system_prompt ?? '',
          enable_subagents: Boolean(settings.enable_subagents),
          inherit_parent_tools: Boolean(settings.inherit_parent_tools),
          enable_planning: Boolean(settings.enable_planning),
          subagent_ids: settings.subagent_ids ?? [],
          model_id: matchedModel?.id ?? undefined,
        };
        form.setFieldsValue(initialValuesRef.current);

        // Initialize sandbox state from loaded detail
        const backend = settings.backend ?? {};
        const isSandbox = backend.type === 'sandbox';
        setSandboxEnabled(isSandbox);
        setSandboxProvider(backend.provider ?? 'opensandbox');
        setSandboxImage(backend.image ?? 'ghcr.io/agent-infra/sandbox:latest');
        setSandboxMemoryMb(backend.memory_mb ?? 512);
        const lc = backend.lifecycle ?? {};
        setSandboxLifecycle(lc.strategy ?? 'ephemeral');
        setSandboxTimeout(lc.timeout ?? 600);
      })
      .catch((error) => setDetailError(extractErrorMessage(error)))
      .finally(() => setDetailLoading(false));

    setDelegationsLoading(true);
    void apiClient
      .get<{ delegations: DelegationRecord[] }>(`/agents/${selectedAgentId}/delegations`)
      .then((resp) => setDelegations(Array.isArray(resp.data?.delegations) ? resp.data.delegations : []))
      .catch(() => setDelegations([]))
      .finally(() => setDelegationsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgentId]);

  // Keep the URL in sync when the user switches agent in the selector.
  const handleAgentChange = (agentId: string) => {
    setSelectedAgentId(agentId);
    navigate(`/agents/${agentId}/config`, { replace: true });
  };

  const handleSave = async () => {
    if (!selectedAgentId) return;
    let values: { description?: string; system_prompt?: string; enable_subagents?: boolean; inherit_parent_tools?: boolean; enable_planning?: boolean; subagent_ids?: string[]; model_id?: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, unknown> = {};
      if (values.description !== undefined) body.description = values.description?.trim() ?? '';
      if (values.system_prompt !== undefined) body.system_prompt = values.system_prompt?.trim() ?? '';
      if (values.enable_subagents !== undefined) body.enable_subagents = values.enable_subagents;
      if (values.inherit_parent_tools !== undefined) body.inherit_parent_tools = values.inherit_parent_tools;
      if (values.enable_planning !== undefined) body.enable_planning = values.enable_planning;
      if (values.subagent_ids !== undefined) body.subagent_ids = values.subagent_ids;
      if (values.model_id !== undefined) body.model_id = values.model_id ?? '';
      await apiClient.put(`/agents/${selectedAgentId}/settings`, body);
      message.success(t('agentConfig.saveSuccess'));
      await fetchAgents();
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleRestore = () => {
    form.setFieldsValue(initialValuesRef.current);
    message.info(t('agentConfig.restored'));
  };

  // ─── Sandbox save ───
  const handleSaveSandbox = async () => {
    if (!selectedAgentId) return;
    setSandboxSaving(true);
    try {
      const body: Record<string, unknown> = {};
      if (sandboxEnabled) {
        body.backend = {
          type: 'sandbox',
          provider: sandboxProvider,
          ...(sandboxImage ? { image: sandboxImage } : {}),
          ...(sandboxMemoryMb ? { memory_mb: sandboxMemoryMb } : {}),
          lifecycle: {
            strategy: sandboxLifecycle,
            timeout: sandboxTimeout,
            renew_on_chat: true,
          },
        };
      } else {
        body.backend = {};
      }
      await apiClient.put(`/agents/${selectedAgentId}/settings`, body);
      message.success(t('agentConfig.saveSuccess'));
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setSandboxSaving(false);
    }
  };

  // ─── Topology ───
  // Sub-agents this agent can delegate to (only when enable_subagents).
  const subagents = useMemo(() => detail?.subagents ?? [], [detail]);
  // Parent agents that can delegate to this agent (derived from all agents).
  const parentAgents = useMemo(
    () =>
      agents.filter(
        (a) => a.agent_id !== selectedAgentId && (a.subagents ?? []).some((s) => s.name === selectedAgentId),
      ),
    [agents, selectedAgentId],
  );

  const agentSelector = (
    <Select
      style={{ width: 320 }}
      placeholder={t('agentConfig.selectAgentPlaceholder')}
      loading={agentsLoading}
      showSearch
      optionFilterProp="label"
      value={selectedAgentId}
      onChange={handleAgentChange}
      options={agents.map((a) => ({
        value: a.agent_id,
        label: `${a.agent_id}${a.description ? ` — ${a.description}` : ''}`,
      }))}
      notFoundContent={agentsLoading ? <Skeleton active paragraph={{ rows: 1 }} /> : t('agents.noMatching')}
    />
  );

  return (
    <div>
      <GooglePageHeader
        icon={<DeploymentUnitOutlined />}
        title={t('agentConfig.title')}
        subtitle={t('agentConfig.subtitle')}
        extra={agentSelector}
      />

      {!selectedAgentId ? (
        <GoogleCard>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t('agentConfig.selectAgentHint')}
            style={{ padding: '32px 0' }}
          />
        </GoogleCard>
      ) : detailLoading ? (
        <GoogleCard>
          <Skeleton active paragraph={{ rows: 6 }} />
        </GoogleCard>
      ) : detailError ? (
        <Alert type="error" showIcon message={t('agents.detailLoadFailed')} description={detailError} />
      ) : (
        <>
          {/* Identity tags */}
          <div style={{ marginBottom: 'var(--google-space-6)' }}>
            <Space size={8} wrap>
              <Tag
                icon={<DeploymentUnitOutlined />}
                style={{
                  marginInlineEnd: 0,
                  fontSize: 13,
                  padding: '4px 10px',
                  borderRadius: 'var(--google-radius-md)',
                  borderColor: 'var(--google-border)',
                  background: 'var(--google-muted)',
                }}
              >
                {selectedAgentId}
              </Tag>
              {enableSubagents && (
                <Tag
                  color="blue"
                  icon={<BranchesOutlined />}
                  style={{ marginInlineEnd: 0, fontSize: 13, padding: '4px 10px' }}
                >
                  {t('agentConfig.supervisorTag')}
                </Tag>
              )}
              {parentAgents.length > 0 && (
                <Tag
                  color="purple"
                  icon={<InboxOutlined />}
                  style={{ marginInlineEnd: 0, fontSize: 13, padding: '4px 10px' }}
                >
                  {t('agentConfig.subagentTag')}
                </Tag>
              )}
            </Space>
          </div>

          {/* Basic settings */}
          <GoogleCard title={t('agentConfig.basicSettings')} style={{ marginBottom: 'var(--google-space-8)' }}>
            <Form form={form} layout="vertical" style={{ maxWidth: 720 }}>
              <Form.Item label={t('agents.description')} name="description">
                <Input.TextArea rows={2} showCount maxLength={200} placeholder={t('agents.descriptionPlaceholder')} />
              </Form.Item>
              <Form.Item label={t('agentConfig.model')} name="model_id" tooltip={t('agentConfig.modelHelp')}>
                <Select
                  allowClear
                  placeholder={t('agentConfig.modelPlaceholder')}
                  optionFilterProp="label"
                  options={modelLibrary.map((m) => ({
                    value: m.id,
                    label: `${m.name}${m.provider ? ` (${m.provider})` : ''}${m.is_default ? ' ★' : ''}`,
                  }))}
                />
              </Form.Item>
              <Form.Item
                label={
                  <Space size={4}>
                    {t('agentConfig.systemPrompt')}
                    <Tooltip title={t('agentConfig.systemPromptHelp')}>
                      <Text type="secondary" style={{ fontSize: 12 }}>(bootstrap.md)</Text>
                    </Tooltip>
                  </Space>
                }
                name="system_prompt"
                extra={t('agentConfig.systemPromptExtra')}
              >
                <Input.TextArea rows={8} showCount maxLength={8000} placeholder={t('agents.systemPromptPlaceholder')} />
              </Form.Item>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                  gap: 'var(--google-space-4) var(--google-space-8)',
                }}
              >
                <Form.Item
                  label={t('agents.enableSubagents')}
                  name="enable_subagents"
                  valuePropName="checked"
                  tooltip={t('agents.enableSubagentsHelp')}
                  style={{ marginBottom: 16 }}
                >
                  <Switch />
                </Form.Item>
                <Form.Item
                  label={t('agents.inheritParentTools')}
                  name="inherit_parent_tools"
                  valuePropName="checked"
                  tooltip={t('agents.inheritParentToolsHelp')}
                  style={{ marginBottom: 16 }}
                >
                  <Switch />
                </Form.Item>
                <Form.Item
                  label={t('agents.enablePlanning')}
                  name="enable_planning"
                  valuePropName="checked"
                  tooltip={t('agents.enablePlanningHelp')}
                  style={{ marginBottom: 16 }}
                >
                  <Switch />
                </Form.Item>
              </div>

              {/* Subagent selector — only shown when enable_subagents is on */}
              {enableSubagents && (
                <Form.Item
                  label={t('agentConfig.subagentWhitelist')}
                  name="subagent_ids"
                  tooltip={t('agentConfig.subagentWhitelistHelp')}
                  style={{ maxWidth: 480 }}
                >
                  <Select
                    mode="multiple"
                    allowClear
                    placeholder={t('agentConfig.subagentWhitelistPlaceholder')}
                    optionFilterProp="label"
                    options={agents
                      .filter((a) => a.agent_id !== selectedAgentId)
                      .map((a) => ({
                        value: a.agent_id,
                        label: `${a.agent_id}${a.description ? ` — ${a.description}` : ''}`,
                      }))}
                  />
                </Form.Item>
              )}

              <Form.Item
                style={{
                  marginBottom: 0,
                  marginTop: 'var(--google-space-4)',
                  paddingTop: 'var(--google-space-6)',
                  borderTop: '1px solid var(--google-border)',
                }}
              >
                <Space size={8}>
                  <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
                    {t('agentConfig.save')}
                  </Button>
                  <Button icon={<RollbackOutlined />} onClick={handleRestore}>
                    {t('agentConfig.restore')}
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </GoogleCard>

          {/* Topology */}
          <GoogleCard title={<Space><BranchesOutlined style={{ color: 'var(--google-primary)' }} /><span>{t('agentConfig.topology')}</span></Space>} style={{ marginBottom: 'var(--google-space-8)' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 'var(--google-space-8)' }}>
              {/* My subagents */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--google-space-3)', marginBottom: 'var(--google-space-4)' }}>
                  <BranchesOutlined style={{ color: 'var(--google-primary)' }} />
                  <Text strong style={{ fontSize: 14 }}>{t('agentConfig.mySubagents')}</Text>
                  {!enableSubagents && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      ({t('agentConfig.disabledHint')})
                    </Text>
                  )}
                </div>
                {subagents.length === 0 ? (
                  <Text type="secondary" style={{ fontSize: 13 }}>
                    {t('agentConfig.noSubagents')}
                  </Text>
                ) : (
                  <List
                    size="small"
                    dataSource={subagents}
                    renderItem={(s) => (
                      <List.Item
                        style={{
                          padding: '10px 12px',
                          borderRadius: 'var(--google-radius-md)',
                          border: '1px solid var(--google-border)',
                          marginBottom: 'var(--google-space-3)',
                          background: 'var(--google-muted)',
                        }}
                        actions={[
                          <Tooltip key="chat" title={t('agentConfig.goChat')}>
                            <Button
                              type="text"
                              size="small"
                              icon={<SendOutlined style={{ color: 'var(--google-primary)' }} />}
                              onClick={() => navigate(`/agents/${s.name}/chat`)}
                            />
                          </Tooltip>,
                        ]}
                      >
                        <Space direction="vertical" size={0}>
                          <Space size={6}>
                            <ArrowRightOutlined style={{ color: 'var(--google-muted-foreground)', fontSize: 12 }} />
                            <Text code style={{ fontSize: 13 }}>{s.name}</Text>
                          </Space>
                          {s.description && (
                            <Text type="secondary" style={{ fontSize: 12, marginLeft: 20 }}>
                              {s.description}
                            </Text>
                          )}
                        </Space>
                      </List.Item>
                    )}
                  />
                )}
              </div>

              {/* My parents */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--google-space-3)', marginBottom: 'var(--google-space-4)' }}>
                  <ApiOutlined style={{ color: 'var(--google-chart-2)' }} />
                  <Text strong style={{ fontSize: 14 }}>{t('agentConfig.myParents')}</Text>
                </div>
                {parentAgents.length === 0 ? (
                  <Text type="secondary" style={{ fontSize: 13 }}>
                    {t('agentConfig.noParents')}
                  </Text>
                ) : (
                  <List
                    size="small"
                    dataSource={parentAgents}
                    renderItem={(p) => (
                      <List.Item
                        style={{
                          padding: '10px 12px',
                          borderRadius: 'var(--google-radius-md)',
                          border: '1px solid var(--google-border)',
                          marginBottom: 'var(--google-space-3)',
                          background: 'var(--google-muted)',
                        }}
                      >
                        <Space size={6}>
                          <ArrowRightOutlined style={{ color: 'var(--google-muted-foreground)', fontSize: 12, transform: 'scaleX(-1)' }} />
                          <Text code style={{ fontSize: 13 }}>{p.agent_id}</Text>
                          {p.description && (
                            <Text type="secondary" style={{ fontSize: 12 }}>{p.description}</Text>
                          )}
                        </Space>
                      </List.Item>
                    )}
                  />
                )}
              </div>
            </div>
          </GoogleCard>

          {/* Delegation records */}
          <GoogleCard title={<Space><CloudSyncOutlined style={{ color: 'var(--google-chart-3)' }} /><span>{t('agentConfig.delegationRecords')}</span></Space>}>
            {delegationsLoading ? (
              <Skeleton active paragraph={{ rows: 2 }} />
            ) : delegations.length === 0 ? (
              <Text type="secondary" style={{ fontSize: 13 }}>
                {t('agentConfig.noDelegations')}
              </Text>
            ) : (
              <List
                size="small"
                dataSource={[...delegations].reverse()}
                renderItem={(d) => (
                  <List.Item
                    style={{
                      padding: '12px',
                      borderRadius: 'var(--google-radius-md)',
                      border: '1px solid var(--google-border)',
                      marginBottom: 'var(--google-space-3)',
                      background: 'var(--google-muted)',
                    }}
                  >
                    <Space direction="vertical" size={4} style={{ width: '100%' }}>
                      <Space size={8} wrap>
                        <Tag color="orange" style={{ marginInlineEnd: 0 }}>{t('agentConfig.delegatedFrom')}</Tag>
                        <Text code style={{ fontSize: 13 }}>{d.parent_agent_id ?? '—'}</Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>{formatDateTime(d.timestamp)}</Text>
                      </Space>
                      {d.task_description && (
                        <Text style={{ fontSize: 13, color: 'var(--google-foreground)', marginTop: 4 }}>{d.task_description}</Text>
                      )}
                    </Space>
                  </List.Item>
                )}
              />
            )}
          </GoogleCard>

          {/* Sandbox config */}
          <GoogleCard
            title={<Space><CloudServerOutlined style={{ color: 'var(--google-chart-1)' }} /><span>{t('sandbox.title')}</span></Space>}
            style={{ marginTop: 'var(--google-space-8)' }}
          >
            <div style={{ maxWidth: 600 }}>
              <div style={{ marginBottom: 'var(--google-space-6)' }}>
                <Space>
                  <Text strong>{t('sandbox.execEnv')}:</Text>
                  <Switch
                    checked={sandboxEnabled}
                    onChange={(checked) => setSandboxEnabled(checked)}
                    checkedChildren="Sandbox"
                    unCheckedChildren="Local"
                  />
                </Space>
              </div>

              {sandboxEnabled && (
                <>
                  <div style={{ marginBottom: 'var(--google-space-4)' }}>
                    <div style={{ marginBottom: 4 }}><Text strong style={{ fontSize: 13 }}>{t('sandbox.provider')}</Text></div>
                    <Select
                      value={sandboxProvider}
                      onChange={setSandboxProvider}
                      style={{ width: 280 }}
                      options={[
                        { value: 'opensandbox', label: 'OpenSandbox' },
                        { value: 'e2b', label: 'E2B (coming soon)', disabled: true },
                        { value: 'daytona', label: 'Daytona (coming soon)', disabled: true },
                      ]}
                    />
                  </div>

                  <div style={{ marginBottom: 'var(--google-space-4)' }}>
                    <div style={{ marginBottom: 4 }}><Text strong style={{ fontSize: 13 }}>{t('sandbox.lifecycle')}</Text></div>
                    <Select
                      value={sandboxLifecycle}
                      onChange={setSandboxLifecycle}
                      style={{ width: 400 }}
                      options={[
                        { value: 'ephemeral', label: `${t('sandbox.ephemeral')} — ${t('sandbox.ephemeralDesc')}` },
                        { value: 'pause-on-idle', label: `${t('sandbox.pauseOnIdle')} — ${t('sandbox.pauseOnIdleDesc')}` },
                        { value: 'persistent', label: `${t('sandbox.persistent')} — ${t('sandbox.persistentDesc')}` },
                      ]}
                    />
                  </div>

                  <div style={{ marginBottom: 'var(--google-space-4)' }}>
                    <div style={{ marginBottom: 4 }}><Text strong style={{ fontSize: 13 }}>{t('sandbox.image')}</Text></div>
                    <Select
                      value={sandboxImage}
                      onChange={setSandboxImage}
                      style={{ width: 400 }}
                      options={[
                        { value: 'ghcr.io/agent-infra/sandbox:latest', label: 'AIO (Browser + Shell + VSCode + Jupyter)' },
                        { value: 'python:3.12-slim', label: 'Python 3.12 (slim)' },
                      ]}
                    />
                  </div>

                  <div style={{ display: 'flex', gap: 'var(--google-space-8)', marginBottom: 'var(--google-space-4)' }}>
                    <div>
                      <div style={{ marginBottom: 4 }}><Text strong style={{ fontSize: 13 }}>{t('sandbox.timeout')}</Text></div>
                      <InputNumber
                        value={sandboxTimeout}
                        onChange={(v) => setSandboxTimeout(v ?? 600)}
                        min={60}
                        style={{ width: 120 }}
                      />
                    </div>
                    <div>
                      <div style={{ marginBottom: 4 }}><Text strong style={{ fontSize: 13 }}>{t('sandbox.memoryMb')}</Text></div>
                      <InputNumber
                        value={sandboxMemoryMb}
                        onChange={(v) => setSandboxMemoryMb(v ?? 512)}
                        min={128}
                        step={128}
                        style={{ width: 120 }}
                      />
                    </div>
                  </div>

                  <Alert
                    type="warning"
                    showIcon
                    message={t('sandbox.sandboxConfigWarning')}
                    style={{ marginBottom: 'var(--google-space-4)' }}
                  />

                  <Button
                    type="primary"
                    icon={<SaveOutlined />}
                    loading={sandboxSaving}
                    onClick={handleSaveSandbox}
                  >
                    {t('agentConfig.save')}
                  </Button>
                </>
              )}
            </div>
          </GoogleCard>
        </>
      )}
    </div>
  );
}
