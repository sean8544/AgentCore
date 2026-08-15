import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Skeleton,
  Space,
  Steps,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CaretRightOutlined,
  CheckCircleFilled,
  DeleteOutlined,
  FileMarkdownOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  InfoCircleOutlined,
  BranchesOutlined,
  PauseCircleOutlined,
  PlusOutlined,
  PoweroffOutlined,
  ReloadOutlined,
  SearchOutlined,
  StarFilled,
  StarOutlined,
  StopOutlined,
  SyncOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { useAgentStore } from '../../stores/agentStore';
import { extractErrorMessage, formatDateTime } from '../../utils/helpers';

const { Title, Text } = Typography;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AgentState = 'idle' | 'running' | 'paused' | 'stopped' | 'error' | string;

interface AgentInfo {
  agent_id: string;
  state: AgentState | null;
  loaded: boolean;
  session_count: number;
  description?: string;
  enable_subagents?: boolean;
}

interface AgentDetail {
  agent_id: string;
  state: AgentState | null;
  loaded: boolean;
  session_count: number;
  workspace_dir?: string;
  kernel_files?: string[];
  initialized?: boolean;
  created_at?: string;
  settings?: {
    description?: string;
    system_prompt?: string;
    enable_subagents?: boolean;
    inherit_parent_tools?: boolean;
  };
  subagents?: { name: string; description?: string }[];
}

interface CreateAgentFormValues {
  agent_id: string;
  description?: string;
  system_prompt?: string;
  enable_subagents?: boolean;
  inherit_parent_tools?: boolean;
  model_mode?: 'inherit' | 'existing' | 'custom';
  selected_model?: string;
  provider?: string;
  model_name?: string;
  base_url?: string;
  api_key_env?: string;
}

interface ModelCatalogItem {
  provider: string;
  name: string;
  base_url?: string;
  api_key_env?: string;
}

type RowAction = 'start' | 'stop' | 'reload' | 'delete';

// ---------------------------------------------------------------------------
// Current-agent marker
//
// “设为当前”直接写入全局 agentStore（与侧边栏下拉、聊天页共用同一数据源），
// 不再维护独立的 localStorage 副本，避免两处状态不一致。
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Status presentation
// ---------------------------------------------------------------------------

const getStateMeta = (t: (key: string) => string): Record<
  string,
  { color: string; text: string; icon: ReactNode }
> => ({
  idle: { color: 'geekblue', text: t('agents.stateIdle'), icon: <InboxOutlined /> },
  running: { color: 'green', text: t('agents.stateRunning'), icon: <SyncOutlined spin /> },
  paused: { color: 'orange', text: t('agents.statePaused'), icon: <PauseCircleOutlined /> },
  stopped: { color: 'default', text: t('agents.stateStopped'), icon: <StopOutlined /> },
  error: { color: 'red', text: t('agents.stateError'), icon: <WarningOutlined /> },
});

function StateTag({ state }: { state: AgentState | null }) {
  const { t } = useI18n();
  const STATE_META = getStateMeta(t);
  if (!state) {
    return (
      <Tag icon={<InboxOutlined />} style={{ margin: 0 }}>
        {t('agents.stateUnloaded')}
      </Tag>
    );
  }
  const meta = STATE_META[state] ?? { color: 'default', text: state, icon: null };
  return (
    <Tag icon={meta.icon} color={meta.color} style={{ margin: 0 }}>
      {meta.text}
    </Tag>
  );
}

const getKernelFileLabels = (t: (key: string) => string): Record<string, string> => ({
  'agent.md': t('agents.agentIdentity'),
  'profile.md': t('agents.profileConfig'),
  'soul.md': t('agents.corePersona'),
});

// ---------------------------------------------------------------------------
// Scoped styles — 控制台风格的字体与细节（仅作用于本页）
// ---------------------------------------------------------------------------

const PAGE_STYLES = `
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

.agents-page {
  --ink: #16241f;
  --paper: #f4f6f2;
  --accent: #0e7a5f;
  --amber: #d97706;
  padding: 28px 32px 40px;
  background:
    radial-gradient(circle at 12% -10%, rgba(14, 122, 95, 0.08), transparent 42%),
    radial-gradient(circle at 95% 0%, rgba(217, 119, 6, 0.06), transparent 36%),
    var(--paper);
  min-height: 100%;
  font-feature-settings: 'tnum';
}
.agents-page .deck-title {
  font-family: 'Chakra Petch', 'Segoe UI', sans-serif;
  letter-spacing: 0.02em;
  color: var(--ink);
  margin: 0 !important;
}
.agents-page .deck-sub {
  font-family: 'IBM Plex Mono', 'Cascadia Code', monospace;
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #5c6b63;
}
.agents-page .mono {
  font-family: 'IBM Plex Mono', 'Cascadia Code', monospace;
  font-size: 12.5px;
}
.agents-page .agent-id-cell {
  font-family: 'IBM Plex Mono', 'Cascadia Code', monospace;
  font-weight: 600;
  font-size: 13px;
  color: var(--ink);
}
.agents-page .ant-table-wrapper .ant-table {
  background: transparent;
}
.agents-page .ant-table-row.is-current > td {
  background: rgba(217, 119, 6, 0.055) !important;
}
.agents-page .ant-table-row.is-current > td:first-child {
  box-shadow: inset 3px 0 0 var(--amber);
}
.agents-page .stat-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 10px 18px;
  border: 1px solid rgba(22, 36, 31, 0.12);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(2px);
}
.agents-page .stat-chip .num {
  font-family: 'Chakra Petch', sans-serif;
  font-size: 22px;
  font-weight: 700;
  color: var(--ink);
  line-height: 1;
}
.agents-page .stat-chip .lbl {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #5c6b63;
}
.agents-page .current-mark {
  color: var(--amber);
}
.agents-page .detail-path {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  background: rgba(22, 36, 31, 0.06);
  border: 1px solid rgba(22, 36, 31, 0.1);
  border-radius: 6px;
  padding: 6px 10px;
  word-break: break-all;
}
`;

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function AgentsPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const KERNEL_FILE_LABELS = getKernelFileLabels(t);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [listLoading, setListLoading] = useState(false);

  // 搜索与筛选
  const [searchText, setSearchText] = useState('');
  const [stateFilter, setStateFilter] = useState<string | undefined>(undefined);

  // 批量选择
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);

  // 当前 Agent：与全局下拉共用 agentStore.selectedAgent
  const currentAgentId = useAgentStore((s) => s.selectedAgent);
  const setSelectedAgent = useAgentStore((s) => s.setSelectedAgent);

  // 详情 Modal
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailCreatedAt, setDetailCreatedAt] = useState<string | null>(null);

  // 创建 Modal
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createStep, setCreateStep] = useState(0);
  const [form] = Form.useForm<CreateAgentFormValues>();
  const modelMode = Form.useWatch('model_mode', form) ?? 'inherit';

  // 模型目录（GET /api/models，用于创建表单的「选择现有模型」）
  const [modelCatalog, setModelCatalog] = useState<ModelCatalogItem[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);

  // Per-row action loading: agent_id -> action currently in flight.
  const [rowAction, setRowAction] = useState<Record<string, RowAction | null>>({});

  const markRowAction = (agentId: string, action: RowAction | null) => {
    setRowAction((prev) => ({ ...prev, [agentId]: action }));
  };

  // 页面活跃状态（用于轮询）
  const pageActiveRef = useRef(true);

  // --- Data fetching ---------------------------------------------------------

  const fetchAgents = useCallback(async (silent = false) => {
    if (!silent) setListLoading(true);
    try {
      const resp = await apiClient.get<AgentInfo[]>('/agents');
      setAgents(Array.isArray(resp.data) ? resp.data : []);
    } catch (error) {
      if (!silent) {
        message.error(`${t('agents.loadFailed', { error: extractErrorMessage(error) })}`);
      }
    } finally {
      setListLoading(false);
    }
  }, [t]);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  // --- 状态轮询：页面活跃时每 5 秒静默刷新 ------------------------------------

  useEffect(() => {
    const handleVisibility = () => {
      pageActiveRef.current = document.visibilityState === 'visible';
      if (pageActiveRef.current) {
        fetchAgents(true);
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);

    const timer = window.setInterval(() => {
      if (pageActiveRef.current) {
        fetchAgents(true);
      }
    }, 5000);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.clearInterval(timer);
    };
  }, [fetchAgents]);

  // --- Create agent ----------------------------------------------------------

  const ensureModelsLoaded = useCallback(async () => {
    setModelsLoading(true);
    try {
      const res = await apiClient.get('/models');
      setModelCatalog(Array.isArray(res.data.models) ? res.data.models : []);
    } catch {
      setModelCatalog([]);
    } finally {
      setModelsLoading(false);
    }
  }, []);

  const openCreateModal = () => {
    form.resetFields();
    setCreateStep(0);
    setCreateOpen(true);
    void ensureModelsLoaded();
  };

  const handleCreate = async () => {
    let values: CreateAgentFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setCreating(true);
    try {
      const model: Record<string, string> = {};
      if (values.model_mode === 'existing' && values.selected_model) {
        // option value format: provider|name|base_url
        const [provider, name, base_url] = values.selected_model.split('|');
        if (provider) model.provider = provider;
        if (name) model.name = name;
        if (base_url) model.base_url = base_url;
      } else if (values.model_mode === 'custom') {
        if (values.provider?.trim()) model.provider = values.provider.trim();
        if (values.model_name?.trim()) model.name = values.model_name.trim();
        if (values.base_url?.trim()) model.base_url = values.base_url.trim();
        if (values.api_key_env?.trim()) model.api_key_env = values.api_key_env.trim();
      }

      const body: Record<string, unknown> = {
        agent_id: values.agent_id.trim(),
        ...(Object.keys(model).length > 0 ? { model } : {}),
      };
      if (values.description?.trim()) body.description = values.description.trim();
      if (values.system_prompt?.trim()) body.system_prompt = values.system_prompt.trim();
      if (values.enable_subagents !== undefined) body.enable_subagents = values.enable_subagents;
      if (values.inherit_parent_tools !== undefined) body.inherit_parent_tools = values.inherit_parent_tools;

      const createdId = values.agent_id.trim();
      await apiClient.post('/agents', body);
      message.success(t('agents.createSuccess', { id: createdId }));
      setCreateOpen(false);
      fetchAgents();
      // 创建完成后询问是否进入初始化流程：通过对话（bootstrap）修改新 Agent 的提示词。
      Modal.confirm({
        title: t('agents.initPromptTitle', { id: createdId }),
        content: t('agents.initPromptContent'),
        okText: t('agents.initPromptGo'),
        cancelText: t('agents.initPromptLater'),
        okButtonProps: { type: 'primary' },
        onOk: () => {
          useAgentStore.getState().setSelectedAgent(createdId);
          navigate(`/chat/${createdId}`);
        },
      });
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setCreating(false);
    }
  };

  // --- Row actions -----------------------------------------------------------

  const runAction = async (
    agentId: string,
    action: RowAction,
    request: () => Promise<unknown>,
    successText: string,
  ) => {
    markRowAction(agentId, action);
    try {
      await request();
      message.success(successText);
      fetchAgents(true);
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      markRowAction(agentId, null);
    }
  };

  const handleStart = (agentId: string) =>
    runAction(agentId, 'start', () => apiClient.post(`/agents/${agentId}/start`), t('agents.startedSuccess', { id: agentId }));

  const handleStop = (agentId: string) =>
    runAction(agentId, 'stop', () => apiClient.post(`/agents/${agentId}/stop`), t('agents.stoppedSuccess', { id: agentId }));

  const handleReload = (agentId: string) =>
    runAction(agentId, 'reload', () => apiClient.post(`/agents/${agentId}/reload`), t('agents.reloadedSuccess', { id: agentId }));

  const handleDelete = (agentId: string) =>
    runAction(agentId, 'delete', () => apiClient.delete(`/agents/${agentId}`), t('agents.deletedSuccess', { id: agentId }));

  const busyOf = (agentId: string): RowAction | null => rowAction[agentId] ?? null;

  // --- 设为当前 Agent（同步全局下拉选择器）---------------------------------

  const handleSetCurrent = (agentId: string) => {
    setSelectedAgent(agentId);
    message.success(t('agents.setCurrentSuccess', { id: agentId }));
  };

  // --- 详情 Modal -------------------------------------------------------------

  const openDetail = async (agentId: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    setDetailCreatedAt(null);

    try {
      const resp = await apiClient.get<AgentDetail>(`/agents/${agentId}`);
      setDetail(resp.data);

      // 后端详情接口暂不返回创建时间：尝试从会话列表的最早 created_at 推断，失败则留空。
      if (resp.data?.created_at) {
        setDetailCreatedAt(resp.data.created_at);
      } else {
        try {
          const sessionsResp = await apiClient.get<Array<{ created_at?: string }>>(
            '/chat/sessions',
            { params: { agent_id: agentId } },
          );
          const sessions = Array.isArray(sessionsResp.data) ? sessionsResp.data : [];
          const earliest = sessions
            .map((s) => s.created_at)
            .filter((v): v is string => typeof v === 'string' && v.length > 0)
            .sort()[0];
          setDetailCreatedAt(earliest ?? null);
        } catch {
          setDetailCreatedAt(null);
        }
      }
    } catch (error) {
      setDetailError(extractErrorMessage(error));
    } finally {
      setDetailLoading(false);
    }
  };

  // --- 批量删除 ----------------------------------------------------------------

  const handleBatchDelete = async () => {
    if (selectedIds.length === 0) return;
    setBatchDeleting(true);
    const failed: string[] = [];
    for (const agentId of selectedIds) {
      try {
        // eslint-disable-next-line no-await-in-loop
        await apiClient.delete(`/agents/${agentId}`);
      } catch {
        failed.push(agentId);
      }
    }
    setBatchDeleting(false);
    if (failed.length === 0) {
      message.success(t('agents.batchDeleteSuccess', { count: selectedIds.length }));
    } else {
      message.warning(t('agents.batchDeletePartial', { count: selectedIds.length, success: selectedIds.length - failed.length, failed: failed.length, ids: failed.join('、') }));
    }
    // 清理选择（当前 agent 被删除时由 refreshAgents 自动回落到第一个）
    setSelectedIds([]);
    fetchAgents();
  };

  // --- 搜索 / 筛选 ---------------------------------------------------------------

  const filteredAgents = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    return agents.filter((agent) => {
      if (keyword) {
        const idMatch = agent.agent_id.toLowerCase().includes(keyword);
        const descMatch = (agent.description ?? '').toLowerCase().includes(keyword);
        if (!idMatch && !descMatch) return false;
      }
      if (stateFilter !== undefined) {
        const state = agent.state ?? '__unloaded__';
        if (state !== stateFilter) return false;
      }
      return true;
    });
  }, [agents, searchText, stateFilter]);

  const stateOptions = useMemo(() => {
    const present = new Set<string>();
    agents.forEach((agent) => present.add(agent.state ?? '__unloaded__'));
    const base = [
      { value: 'running', label: t('agents.stateRunning') },
      { value: 'idle', label: t('agents.stateIdle') },
      { value: 'paused', label: t('agents.statePaused') },
      { value: 'stopped', label: t('agents.stateStopped') },
      { value: 'error', label: t('agents.stateError') },
      { value: '__unloaded__', label: t('agents.stateUnloaded') },
    ];
    return base.filter((option) => present.has(option.value));
  }, [agents, t]);

  // 统计 chips
  const stats = useMemo(() => {
    const total = agents.length;
    const running = agents.filter((a) => a.state === 'running').length;
    const sessions = agents.reduce((acc, a) => acc + (a.session_count || 0), 0);
    return { total, running, sessions };
  }, [agents]);

  // --- Table columns ---------------------------------------------------------

  const columns = useMemo<ColumnsType<AgentInfo>>(
    () => [
      {
        title: 'Agent ID',
        dataIndex: 'agent_id',
        key: 'agent_id',
        render: (value: string, record: AgentInfo) => {
          const isCurrent = currentAgentId === record.agent_id;
          return (
            <Space size={6}>
              <span className="agent-id-cell">{value}</span>
              {isCurrent && (
                <Tooltip title={t('agents.current')}>
                  <StarFilled className="current-mark" style={{ fontSize: 13 }} />
                </Tooltip>
              )}
              {!record.loaded && (
                <Tooltip title={t('common.workspaceNotLoaded')}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    ({t('agents.stateUnloaded')})
                  </Text>
                </Tooltip>
              )}
            </Space>
          );
        },
      },
      {
        title: t('common.status'),
        dataIndex: 'state',
        key: 'state',
        width: 140,
        render: (state: AgentState | null) => <StateTag state={state} />,
      },
      {
        title: t('agents.descriptionLabel'),
        dataIndex: 'description',
        key: 'description',
        width: 200,
        ellipsis: true,
        render: (value: string | undefined, record: AgentInfo) => (
          <Space size={4}>
            {value ? (
              <Tooltip title={value}>
                <Text type="secondary" style={{ fontSize: 12 }}>{value}</Text>
              </Tooltip>
            ) : (
              <Text type="secondary" style={{ fontSize: 12, fontStyle: 'italic' }}>—</Text>
            )}
            {record.enable_subagents && (
              <Tag color="blue" style={{ marginInlineEnd: 0, fontSize: 11 }}>{t('agents.delegationTag')}</Tag>
            )}
          </Space>
        ),
      },
      {
        title: t('agents.sessionCount'),
        dataIndex: 'session_count',
        key: 'session_count',
        width: 90,
        render: (value: number) => <span className="mono">{value ?? 0}</span>,
      },
      {
        title: t('common.actions'),
        key: 'actions',
        width: 400,
        render: (_: unknown, record: AgentInfo) => {
          const busy = busyOf(record.agent_id);
          const anyBusy = busy !== null;
          const state = record.state;
          const canStart = state === 'idle' || state === 'stopped' || state === null;
          const canStop = state === 'running' || state === 'paused';
          const isCurrent = currentAgentId === record.agent_id;

          return (
            <Space size="small" wrap>
              <Tooltip title={t('agents.setAsCurrent')}>
                <Button
                  size="small"
                  type="text"
                  icon={isCurrent ? <CheckCircleFilled style={{ color: '#d97706' }} /> : <StarOutlined />}
                  disabled={anyBusy || isCurrent}
                  onClick={() => handleSetCurrent(record.agent_id)}
                >
                  {isCurrent ? t('agents.current') : t('agents.setCurrent')}
                </Button>
              </Tooltip>
              <Button
                size="small"
                type="link"
                icon={<InfoCircleOutlined />}
                onClick={() => openDetail(record.agent_id)}
              >
                {t('agents.detail')}
              </Button>
              <Tooltip title={t('agents.startHint')}>
                <Button
                  size="small"
                  type="link"
                  icon={<CaretRightOutlined />}
                  disabled={!canStart || anyBusy}
                  loading={busy === 'start'}
                  onClick={() => handleStart(record.agent_id)}
                >
                  {t('agents.start')}
                </Button>
              </Tooltip>
              <Button
                size="small"
                type="link"
                danger
                icon={<PoweroffOutlined />}
                disabled={!canStop || anyBusy}
                loading={busy === 'stop'}
                onClick={() => handleStop(record.agent_id)}
              >
                {t('agents.stop')}
              </Button>
              <Button
                size="small"
                type="link"
                icon={<ReloadOutlined />}
                disabled={anyBusy}
                loading={busy === 'reload'}
                onClick={() => handleReload(record.agent_id)}
              >
                {t('agents.reload')}
              </Button>
              <Popconfirm
                title={t('agents.deleteConfirmTitle')}
                description={t('agents.deleteConfirmDesc', { id: record.agent_id })}
                okText={t('common.delete')}
                okButtonProps={{ danger: true }}
                cancelText={t('common.cancel')}
                onConfirm={() => handleDelete(record.agent_id)}
              >
                <Button
                  size="small"
                  type="link"
                  danger
                  icon={<DeleteOutlined />}
                  disabled={anyBusy}
                  loading={busy === 'delete'}
                >
                  {t('common.delete')}
                </Button>
              </Popconfirm>
            </Space>
          );
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rowAction, currentAgentId, t],
  );

  // --- Render ----------------------------------------------------------------

  const kernelFiles = detail?.kernel_files ?? [];

  return (
    <div className="agents-page">
      <style>{PAGE_STYLES}</style>

      {/* 顶部：标题 + 统计 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div className="deck-sub">{t('agents.subtitle')}</div>
          <Title level={3} className="deck-title">
            {t('agents.title')}
          </Title>
        </div>
        <Space size={12} wrap>
          <span className="stat-chip">
            <span className="num">{stats.total}</span>
            <span className="lbl">{t('agents.totalAgents')}</span>
          </span>
          <span className="stat-chip">
            <span className="num" style={{ color: '#0e7a5f' }}>
              {stats.running}
            </span>
            <span className="lbl">{t('agents.runningAgents')}</span>
          </span>
          <span className="stat-chip">
            <span className="num">{stats.sessions}</span>
            <span className="lbl">{t('agents.totalSessions')}</span>
          </span>
        </Space>
      </div>

      <Divider style={{ margin: '18px 0 16px' }} />

      {/* 工具栏：搜索 + 筛选 + 批量操作 + 创建 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <Space wrap>
          <Input
            allowClear
            prefix={<SearchOutlined style={{ color: '#8a998f' }} />}
            placeholder={t('agents.searchPlaceholder')}
            style={{ width: 240 }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          <Select
            placeholder={t('agents.filterByStatus')}
            allowClear
            style={{ width: 140 }}
            value={stateFilter}
            onChange={(value) => setStateFilter(value)}
            options={stateOptions}
          />
          {selectedIds.length > 0 && (
            <Popconfirm
              title={t('agents.batchDelete')}
              description={t('agents.batchDeleteConfirm', { count: selectedIds.length })}
              okText={t('common.delete')}
              okButtonProps={{ danger: true }}
              cancelText={t('common.cancel')}
              onConfirm={handleBatchDelete}
            >
              <Button danger icon={<DeleteOutlined />} loading={batchDeleting}>
                {t('agents.deleteSelected', { count: selectedIds.length })}
              </Button>
            </Popconfirm>
          )}
        </Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
          {t('agents.createAgent')}
        </Button>
      </div>

      {/* Agent 列表 */}
      <Table<AgentInfo>
        columns={columns}
        dataSource={filteredAgents}
        rowKey="agent_id"
        loading={listLoading}
        pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => t('agents.pagination', { total }) }}
        rowSelection={{
          selectedRowKeys: selectedIds,
          onChange: (keys) => setSelectedIds(keys as string[]),
          columnTitle: (
            <Checkbox
              checked={filteredAgents.length > 0 && selectedIds.length === filteredAgents.length}
              indeterminate={selectedIds.length > 0 && selectedIds.length < filteredAgents.length}
              onChange={(e) =>
                setSelectedIds(e.target.checked ? filteredAgents.map((a) => a.agent_id) : [])
              }
            />
          ),
        }}
        rowClassName={(record) => (currentAgentId === record.agent_id ? 'is-current' : '')}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('agents.noMatching')} /> }}
      />

      {/* 详情 Modal */}
      <Modal
        title={
          <Space>
            <InfoCircleOutlined />
            <span>{t('agents.agentDetail')}</span>
            {detail && <Typography.Text code>{detail.agent_id}</Typography.Text>}
          </Space>
        }
        open={detailOpen}
        footer={
          <Button onClick={() => setDetailOpen(false)}>{t('common.cancel')}</Button>
        }
        onCancel={() => setDetailOpen(false)}
        width={620}
        destroyOnClose
      >
        {detailLoading ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : detailError ? (
          <Alert type="error" showIcon message={t('agents.detailLoadFailed')} description={detailError} />
        ) : detail ? (
          <>
            <Descriptions column={1} size="small" bordered styles={{ label: { width: 120 } }}>
              <Descriptions.Item label="Agent ID">
                <span className="agent-id-cell">{detail.agent_id}</span>
              </Descriptions.Item>
              <Descriptions.Item label={t('common.status')}>
                <Space size={8}>
                  <StateTag state={detail.state} />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {detail.loaded ? t('common.workspaceLoaded') : t('common.workspaceNotLoaded')}
                    {detail.initialized === false ? ` · ${t('agents.notInitialized')}` : ''}
                  </Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label={t('agents.sessionCount')}>
                <span className="mono">{detail.session_count}</span>
              </Descriptions.Item>
              <Descriptions.Item label={t('agents.createdAt')}>
                <span className="mono">{formatDateTime(detailCreatedAt)}</span>
              </Descriptions.Item>
              <Descriptions.Item label={t('agents.kernelFiles')}>
                {kernelFiles.length > 0 ? (
                  <Space size={6} wrap>
                    {kernelFiles.map((name) => (
                      <Tag key={name} icon={<FileMarkdownOutlined />} color="green">
                        {name}
                        <Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                          {KERNEL_FILE_LABELS[name] ?? ''}
                        </Text>
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  <Text type="secondary">{t('agents.none')}</Text>
                )}
              </Descriptions.Item>
            </Descriptions>

            <div style={{ marginTop: 14 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                <FolderOpenOutlined style={{ marginRight: 6 }} />
                {t('agents.workspaceDir')}
              </Text>
              <div className="detail-path" style={{ marginTop: 6 }}>
                {detail.workspace_dir ?? '—'}
              </div>
            </div>

            {(detail.settings?.enable_subagents || (detail.subagents ?? []).length > 0) && (
              <div style={{ marginTop: 14 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <BranchesOutlined style={{ marginRight: 6 }} />
                  {t('agentConfig.mySubagents')}
                </Text>
                <Space size={6} wrap style={{ marginTop: 6 }}>
                  {(detail.subagents ?? []).map((s) => (
                    <Tag key={s.name} color="blue" icon={<BranchesOutlined />} style={{ marginInlineEnd: 0 }}>
                      {s.name}
                      {s.description && (
                        <Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                          {s.description}
                        </Text>
                      )}
                    </Tag>
                  ))}
                  {(detail.subagents ?? []).length === 0 && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t('agentConfig.noSubagents')}
                    </Text>
                  )}
                </Space>
              </div>
            )}
          </>
        ) : null}
      </Modal>

      {/* 创建 Modal */}
      <Modal
        title={t('agents.createAgent')}
        open={createOpen}
        width={560}
        cancelText={t('common.cancel')}
        onCancel={() => setCreateOpen(false)}
        destroyOnClose
        footer={
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div>
              {createStep > 0 && (
                <Button onClick={() => setCreateStep(createStep - 1)}>
                  {t('agents.stepPrev')}
                </Button>
              )}
            </div>
            <Space>
              <Button onClick={() => setCreateOpen(false)}>{t('common.cancel')}</Button>
              {createStep < 2 ? (
                <Button
                  type="primary"
                  onClick={async () => {
                    try {
                      // Validate current step fields before advancing.
                      if (createStep === 0) {
                        await form.validateFields(['agent_id']);
                      }
                      if (createStep === 1 && modelMode === 'existing') {
                        await form.validateFields(['selected_model']);
                      }
                      if (createStep === 1 && modelMode === 'custom') {
                        await form.validateFields(['provider', 'model_name']);
                      }
                      setCreateStep(createStep + 1);
                    } catch {
                      // validation failed — stay on current step
                    }
                  }}
                >
                  {t('agents.stepNext')}
                </Button>
              ) : (
                <Button type="primary" loading={creating} onClick={handleCreate}>
                  {t('agents.createAgent')}
                </Button>
              )}
            </Space>
          </div>
        }
      >
        <Steps
          current={createStep}
          size="small"
          style={{ marginBottom: 24 }}
          items={[
            { title: t('agents.stepBasicInfo') },
            { title: t('agents.stepModelConfig') },
            { title: t('agents.stepCapabilities') },
          ]}
        />

        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          {/* ──── Step 0: 基础信息 ──── */}
          <div style={{ display: createStep === 0 ? 'block' : 'none' }}>
            <Form.Item
              label="Agent ID"
              name="agent_id"
              rules={[
                { required: true, message: t('agents.agentIdRequired') },
                {
                  pattern: /^[a-zA-Z0-9_-]+$/,
                  message: t('agents.agentIdPattern'),
                },
              ]}
            >
              <Input placeholder={t('agents.agentIdExample')} autoComplete="off" />
            </Form.Item>

            <Form.Item
              label={t('agents.description')}
              name="description"
            >
              <Input.TextArea
                rows={2}
                showCount
                maxLength={200}
                placeholder={t('agents.descriptionPlaceholder')}
              />
            </Form.Item>

            <Form.Item
              label={t('agents.systemPrompt')}
              name="system_prompt"
            >
              <Input.TextArea
                rows={4}
                showCount
                maxLength={2000}
                placeholder={t('agents.systemPromptPlaceholder')}
              />
            </Form.Item>
          </div>

          {/* ──── Step 1: 模型配置 ──── */}
          <div style={{ display: createStep === 1 ? 'block' : 'none' }}>
            <Form.Item label={t('agents.modelSource')} name="model_mode" initialValue="inherit">
              <Radio.Group>
                <Radio.Button value="inherit">{t('agents.inheritDefault')}</Radio.Button>
                <Radio.Button value="existing">{t('agents.selectExisting')}</Radio.Button>
                <Radio.Button value="custom">{t('agents.custom')}</Radio.Button>
              </Radio.Group>
            </Form.Item>

            {modelMode === 'existing' && (
              <Form.Item
                label={t('agents.existingModel')}
                name="selected_model"
                rules={[{ required: modelMode === 'existing', message: t('agents.selectModelRequired') }]}
              >
                <Select
                  placeholder={t('agents.selectModelPlaceholder')}
                  loading={modelsLoading}
                  showSearch
                  optionFilterProp="label"
                  options={modelCatalog.map((m) => ({
                    value: `${m.provider}|${m.name}|${m.base_url ?? ''}`,
                    label: `${m.name}（${m.provider}${m.base_url ? ` · ${m.base_url}` : ''}）`,
                  }))}
                  notFoundContent={modelsLoading ? '加载中…' : '暂无已配置的模型'}
                />
              </Form.Item>
            )}

            {modelMode === 'custom' && (
              <>
                <Form.Item
                  label={t('agents.provider')}
                  name="provider"
                  rules={[{ required: modelMode === 'custom', message: t('agents.providerRequired') }]}
                >
                  <Input placeholder={t('agents.providerExample')} autoComplete="off" />
                </Form.Item>

                <Form.Item
                  label={t('agents.model')}
                  name="model_name"
                  rules={[{ required: modelMode === 'custom', message: t('agents.modelRequired') }]}
                >
                  <Input placeholder={t('agents.modelExample')} autoComplete="off" />
                </Form.Item>

                <Form.Item label={t('agents.baseURL')} name="base_url">
                  <Input placeholder={t('agents.baseURLExample')} autoComplete="off" />
                </Form.Item>

                <Form.Item label={t('agents.apiKeyEnv')} name="api_key_env">
                  <Input placeholder={t('agents.apiKeyExample')} autoComplete="off" />
                </Form.Item>
              </>
            )}
          </div>

          {/* ──── Step 2: 能力与协作 ──── */}
          <div style={{ display: createStep === 2 ? 'block' : 'none' }}>
            <Form.Item
              label={t('agents.enableSubagents')}
              name="enable_subagents"
              valuePropName="checked"
              initialValue={false}
              tooltip={t('agents.enableSubagentsHelp')}
            >
              <Switch />
            </Form.Item>

            <Form.Item
              label={t('agents.inheritParentTools')}
              name="inherit_parent_tools"
              valuePropName="checked"
              initialValue={true}
              tooltip={t('agents.inheritParentToolsHelp')}
            >
              <Switch />
            </Form.Item>
          </div>
        </Form>
      </Modal>
    </div>
  );
}
