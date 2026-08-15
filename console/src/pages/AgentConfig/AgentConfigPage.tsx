import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Divider,
  Empty,
  Form,
  Input,
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
  CloudSyncOutlined,
  DeploymentUnitOutlined,
  InboxOutlined,
  SaveOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { extractErrorMessage, formatDateTime } from '../../utils/helpers';

const { Title, Text } = Typography;

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
    subagent_ids?: string[];
    interrupt_rules?: { tool_name?: string; require_approval?: boolean }[];
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
        form.setFieldsValue({
          description: settings.description ?? '',
          system_prompt: settings.system_prompt ?? '',
          enable_subagents: Boolean(settings.enable_subagents),
          inherit_parent_tools: Boolean(settings.inherit_parent_tools),
          enable_planning: Boolean(settings.enable_planning),
          subagent_ids: settings.subagent_ids ?? [],
          model_id: matchedModel?.id ?? undefined,
        });
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
    navigate(`/agent-config/${agentId}`, { replace: true });
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

  return (
    <div style={{ padding: 24 }}>
      <Card>
        <Space align="center" style={{ marginBottom: 4 }}>
          <DeploymentUnitOutlined style={{ fontSize: 20, color: '#1677ff' }} />
          <Title level={3} style={{ margin: 0 }}>
            {t('agentConfig.title')}
          </Title>
        </Space>
        <Text type="secondary">{t('agentConfig.subtitle')}</Text>

        <Divider style={{ margin: '16px 0' }} />

        {/* Agent selector */}
        <Form layout="inline">
          <Form.Item label={t('agentConfig.selectAgent')} style={{ marginBottom: 8 }}>
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
          </Form.Item>
        </Form>

        {!selectedAgentId ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t('agentConfig.selectAgentHint')}
            style={{ padding: '32px 0' }}
          />
        ) : detailLoading ? (
          <Skeleton active paragraph={{ rows: 6 }} style={{ marginTop: 16 }} />
        ) : detailError ? (
          <Alert type="error" showIcon message={t('agents.detailLoadFailed')} description={detailError} style={{ marginTop: 16 }} />
        ) : (
          <>
            <Space align="center" style={{ marginTop: 12, marginBottom: 4 }}>
              <Text code style={{ fontSize: 14 }}>{selectedAgentId}</Text>
              {enableSubagents && <Tag color="blue" icon={<BranchesOutlined />}>{t('agentConfig.supervisorTag')}</Tag>}
              {parentAgents.length > 0 && (
                <Tag color="purple" icon={<InboxOutlined />}>{t('agentConfig.subagentTag')}</Tag>
              )}
            </Space>

            <Divider titlePlacement="start" style={{ margin: '20px 0 12px' }}>
              {t('agentConfig.basicSettings')}
            </Divider>

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
              <Space size={32} wrap>
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
              </Space>

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

              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
                  {t('agentConfig.save')}
                </Button>
              </Form.Item>
            </Form>

            {/* Topology: children + parents */}
            <Divider titlePlacement="start" style={{ margin: '24px 0 12px' }}>
              {t('agentConfig.topology')}
            </Divider>

            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 280 }}>
                <Text strong style={{ fontSize: 13 }}>
                  <BranchesOutlined style={{ marginRight: 6, color: '#1677ff' }} />
                  {t('agentConfig.mySubagents')}
                  {!enableSubagents && (
                    <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                      ({t('agentConfig.disabledHint')})
                    </Text>
                  )}
                </Text>
                {subagents.length === 0 ? (
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                    {t('agentConfig.noSubagents')}
                  </Text>
                ) : (
                  <List
                    size="small"
                    style={{ marginTop: 8 }}
                    dataSource={subagents}
                    renderItem={(s) => (
                      <List.Item
                        style={{ padding: '6px 0' }}
                        actions={[
                          <Tooltip key="chat" title={t('agentConfig.goChat')}>
                            <Button
                              type="text"
                              size="small"
                              icon={<SendOutlined />}
                              onClick={() => navigate(`/chat/${s.name}`)}
                            />
                          </Tooltip>,
                        ]}
                      >
                        <Space direction="vertical" size={0}>
                          <Space size={6}>
                            <ArrowRightOutlined style={{ color: '#999', fontSize: 12 }} />
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

              <div style={{ flex: 1, minWidth: 280 }}>
                <Text strong style={{ fontSize: 13 }}>
                  <ApiOutlined style={{ marginRight: 6, color: '#722ed1' }} />
                  {t('agentConfig.myParents')}
                </Text>
                {parentAgents.length === 0 ? (
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                    {t('agentConfig.noParents')}
                  </Text>
                ) : (
                  <List
                    size="small"
                    style={{ marginTop: 8 }}
                    dataSource={parentAgents}
                    renderItem={(p) => (
                      <List.Item style={{ padding: '6px 0' }}>
                        <Space size={6}>
                          <ArrowRightOutlined style={{ color: '#999', fontSize: 12, transform: 'scaleX(-1)' }} />
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

            {/* Delegation records received by this agent */}
            <Divider titlePlacement="start" style={{ margin: '24px 0 12px' }}>
              <CloudSyncOutlined style={{ marginRight: 6 }} />
              {t('agentConfig.delegationRecords')}
            </Divider>
            {delegationsLoading ? (
              <Skeleton active paragraph={{ rows: 2 }} />
            ) : delegations.length === 0 ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('agentConfig.noDelegations')}
              </Text>
            ) : (
              <List
                size="small"
                dataSource={[...delegations].reverse()}
                renderItem={(d, i) => (
                  <List.Item style={{ padding: '8px 0' }}>
                    <Space direction="vertical" size={0} style={{ width: '100%' }}>
                      <Space size={8}>
                        <Tag color="orange" style={{ marginInlineEnd: 0 }}>{t('agentConfig.delegatedFrom')}</Tag>
                        <Text code style={{ fontSize: 13 }}>{d.parent_agent_id ?? '—'}</Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>{formatDateTime(d.timestamp)}</Text>
                      </Space>
                      {d.task_description && (
                        <Text style={{ fontSize: 13, color: '#555', marginTop: 4 }}>{d.task_description}</Text>
                      )}
                    </Space>
                    {i < delegations.length - 1 && <Divider style={{ margin: '4px 0' }} />}
                  </List.Item>
                )}
              />
            )}
          </>
        )}
      </Card>
    </div>
  );
}
