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
import { apiClient } from '../../api/client';

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
}

interface CreateAgentFormValues {
  agent_id: string;
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
// Current-agent store (localStorage fallback)
//
// Task #25 将引入独立的 agentStore（Zustand）。此处先用 localStorage 兜底，
// 接口保持最小化（get/set），后续切换时只需替换这两个函数。
// ---------------------------------------------------------------------------

const CURRENT_AGENT_KEY = 'agentcore.current_agent_id';

const currentAgentStore = {
  get(): string | null {
    try {
      return window.localStorage.getItem(CURRENT_AGENT_KEY);
    } catch {
      return null;
    }
  },
  set(agentId: string | null) {
    try {
      if (agentId === null) {
        window.localStorage.removeItem(CURRENT_AGENT_KEY);
      } else {
        window.localStorage.setItem(CURRENT_AGENT_KEY, agentId);
      }
    } catch {
      // localStorage 不可用时静默降级
    }
  },
};

// ---------------------------------------------------------------------------
// Status presentation
// ---------------------------------------------------------------------------

const STATE_META: Record<
  string,
  { color: string; text: string; icon: ReactNode }
> = {
  idle: { color: 'geekblue', text: '空闲', icon: <InboxOutlined /> },
  running: { color: 'green', text: '运行中', icon: <SyncOutlined spin /> },
  paused: { color: 'orange', text: '已暂停', icon: <PauseCircleOutlined /> },
  stopped: { color: 'default', text: '已停止', icon: <StopOutlined /> },
  error: { color: 'red', text: '错误', icon: <WarningOutlined /> },
};

function StateTag({ state }: { state: AgentState | null }) {
  if (!state) {
    return (
      <Tag icon={<InboxOutlined />} style={{ margin: 0 }}>
        未加载
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

const KERNEL_FILE_LABELS: Record<string, string> = {
  'agent.md': 'Agent 身份',
  'profile.md': 'Profile 配置',
  'soul.md': '核心人设',
};

function extractErrorMessage(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const resp = (error as { response?: { data?: { detail?: unknown } } }).response;
    const detail = resp?.data?.detail;
    if (typeof detail === 'string' && detail) return detail;
  }
  if (error instanceof Error) return error.message;
  return '请求失败';
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

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
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [listLoading, setListLoading] = useState(false);

  // 搜索与筛选
  const [searchText, setSearchText] = useState('');
  const [stateFilter, setStateFilter] = useState<string | undefined>(undefined);

  // 批量选择
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);

  // 当前 Agent（localStorage fallback，Task 25 后可替换为 agentStore）
  const [currentAgentId, setCurrentAgentId] = useState<string | null>(() => currentAgentStore.get());

  // 详情 Modal
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailCreatedAt, setDetailCreatedAt] = useState<string | null>(null);

  // 创建 Modal
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
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
        message.error(`获取 Agent 列表失败：${extractErrorMessage(error)}`);
      }
    } finally {
      setListLoading(false);
    }
  }, []);

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
      await apiClient.post('/agents', {
        agent_id: values.agent_id.trim(),
        ...(Object.keys(model).length > 0 ? { model } : {}),
      });
      message.success(`Agent「${values.agent_id}」创建成功`);
      setCreateOpen(false);
      fetchAgents();
    } catch (error) {
      message.error(`创建失败：${extractErrorMessage(error)}`);
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
      message.error(`操作失败：${extractErrorMessage(error)}`);
    } finally {
      markRowAction(agentId, null);
    }
  };

  const handleStart = (agentId: string) =>
    runAction(agentId, 'start', () => apiClient.post(`/agents/${agentId}/start`), `Agent「${agentId}」已启动`);

  const handleStop = (agentId: string) =>
    runAction(agentId, 'stop', () => apiClient.post(`/agents/${agentId}/stop`), `Agent「${agentId}」已停止`);

  const handleReload = (agentId: string) =>
    runAction(agentId, 'reload', () => apiClient.post(`/agents/${agentId}/reload`), `Agent「${agentId}」已重载`);

  const handleDelete = (agentId: string) =>
    runAction(agentId, 'delete', () => apiClient.delete(`/agents/${agentId}`), `Agent「${agentId}」已删除`);

  const busyOf = (agentId: string): RowAction | null => rowAction[agentId] ?? null;

  // --- 设为当前 Agent ----------------------------------------------------------

  const handleSetCurrent = (agentId: string) => {
    const next = currentAgentId === agentId ? null : agentId;
    currentAgentStore.set(next);
    setCurrentAgentId(next);
    if (next) {
      message.success(`已将「${agentId}」设为当前 Agent`);
    } else {
      message.info(`已取消「${agentId}」的当前 Agent 标记`);
    }
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
      message.success(`已删除 ${selectedIds.length} 个 Agent`);
    } else {
      message.warning(`删除完成：${selectedIds.length - failed.length} 成功，${failed.length} 失败（${failed.join('、')}）`);
    }
    // 清理选择与当前 Agent 标记
    setSelectedIds([]);
    if (currentAgentId && failed.every((id) => id !== currentAgentId) && selectedIds.includes(currentAgentId)) {
      currentAgentStore.set(null);
      setCurrentAgentId(null);
    }
    fetchAgents();
  };

  // --- 搜索 / 筛选 ---------------------------------------------------------------

  const filteredAgents = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    return agents.filter((agent) => {
      if (keyword && !agent.agent_id.toLowerCase().includes(keyword)) return false;
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
      { value: 'running', label: '运行中' },
      { value: 'idle', label: '空闲' },
      { value: 'paused', label: '已暂停' },
      { value: 'stopped', label: '已停止' },
      { value: 'error', label: '错误' },
      { value: '__unloaded__', label: '未加载' },
    ];
    return base.filter((option) => present.has(option.value));
  }, [agents]);

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
                <Tooltip title="当前 Agent">
                  <StarFilled className="current-mark" style={{ fontSize: 13 }} />
                </Tooltip>
              )}
              {!record.loaded && (
                <Tooltip title="工作区未加载">
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    (未加载)
                  </Text>
                </Tooltip>
              )}
            </Space>
          );
        },
      },
      {
        title: '状态',
        dataIndex: 'state',
        key: 'state',
        width: 140,
        render: (state: AgentState | null) => <StateTag state={state} />,
      },
      {
        title: '会话数',
        dataIndex: 'session_count',
        key: 'session_count',
        width: 90,
        render: (value: number) => <span className="mono">{value ?? 0}</span>,
      },
      {
        title: '操作',
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
              <Tooltip title={isCurrent ? '取消当前标记' : '设为当前 Agent'}>
                <Button
                  size="small"
                  type="text"
                  icon={isCurrent ? <CheckCircleFilled style={{ color: '#d97706' }} /> : <StarOutlined />}
                  disabled={anyBusy}
                  onClick={() => handleSetCurrent(record.agent_id)}
                >
                  {isCurrent ? '当前' : '设为当前'}
                </Button>
              </Tooltip>
              <Button
                size="small"
                type="link"
                icon={<InfoCircleOutlined />}
                onClick={() => openDetail(record.agent_id)}
              >
                详情
              </Button>
              <Tooltip title="聊天时会自动加载，无需手动启动；停止仅释放内存">
                <Button
                  size="small"
                  type="link"
                  icon={<CaretRightOutlined />}
                  disabled={!canStart || anyBusy}
                  loading={busy === 'start'}
                  onClick={() => handleStart(record.agent_id)}
                >
                  启动
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
                停止
              </Button>
              <Button
                size="small"
                type="link"
                icon={<ReloadOutlined />}
                disabled={anyBusy}
                loading={busy === 'reload'}
                onClick={() => handleReload(record.agent_id)}
              >
                重载
              </Button>
              <Popconfirm
                title="删除 Agent"
                description={`确认删除 Agent「${record.agent_id}」？该操作不可恢复。`}
                okText="删除"
                okButtonProps={{ danger: true }}
                cancelText="取消"
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
                  删除
                </Button>
              </Popconfirm>
            </Space>
          );
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rowAction, currentAgentId],
  );

  // --- Render ----------------------------------------------------------------

  const kernelFiles = detail?.kernel_files ?? [];

  return (
    <div className="agents-page">
      <style>{PAGE_STYLES}</style>

      {/* 顶部：标题 + 统计 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 16 }}>
        <div>
          <div className="deck-sub">Agent Control Deck</div>
          <Title level={3} className="deck-title">
            智能体管理
          </Title>
        </div>
        <Space size={12} wrap>
          <span className="stat-chip">
            <span className="num">{stats.total}</span>
            <span className="lbl">Agents</span>
          </span>
          <span className="stat-chip">
            <span className="num" style={{ color: '#0e7a5f' }}>
              {stats.running}
            </span>
            <span className="lbl">Running</span>
          </span>
          <span className="stat-chip">
            <span className="num">{stats.sessions}</span>
            <span className="lbl">Sessions</span>
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
            placeholder="按 Agent ID 搜索"
            style={{ width: 240 }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          <Select
            placeholder="状态筛选"
            allowClear
            style={{ width: 140 }}
            value={stateFilter}
            onChange={(value) => setStateFilter(value)}
            options={stateOptions}
          />
          {selectedIds.length > 0 && (
            <Popconfirm
              title="批量删除"
              description={`确认删除选中的 ${selectedIds.length} 个 Agent？该操作不可恢复。`}
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={handleBatchDelete}
            >
              <Button danger icon={<DeleteOutlined />} loading={batchDeleting}>
                删除所选（{selectedIds.length}）
              </Button>
            </Popconfirm>
          )}
        </Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
          创建 Agent
        </Button>
      </div>

      {/* Agent 列表 */}
      <Table<AgentInfo>
        columns={columns}
        dataSource={filteredAgents}
        rowKey="agent_id"
        loading={listLoading}
        pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => `共 ${total} 个 Agent` }}
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
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无匹配的 Agent" /> }}
      />

      {/* 详情 Modal */}
      <Modal
        title={
          <Space>
            <InfoCircleOutlined />
            <span>Agent 详情</span>
            {detail && <Typography.Text code>{detail.agent_id}</Typography.Text>}
          </Space>
        }
        open={detailOpen}
        footer={
          <Button onClick={() => setDetailOpen(false)}>关闭</Button>
        }
        onCancel={() => setDetailOpen(false)}
        width={620}
        destroyOnClose
      >
        {detailLoading ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : detailError ? (
          <Alert type="error" showIcon message="获取详情失败" description={detailError} />
        ) : detail ? (
          <>
            <Descriptions column={1} size="small" bordered styles={{ label: { width: 120 } }}>
              <Descriptions.Item label="Agent ID">
                <span className="agent-id-cell">{detail.agent_id}</span>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Space size={8}>
                  <StateTag state={detail.state} />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {detail.loaded ? '工作区已加载' : '工作区未加载'}
                    {detail.initialized === false ? ' · 未初始化' : ''}
                  </Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="会话数">
                <span className="mono">{detail.session_count}</span>
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                <span className="mono">{formatDateTime(detailCreatedAt)}</span>
              </Descriptions.Item>
              <Descriptions.Item label="内核文件">
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
                  <Text type="secondary">无</Text>
                )}
              </Descriptions.Item>
            </Descriptions>

            <div style={{ marginTop: 14 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                <FolderOpenOutlined style={{ marginRight: 6 }} />
                Workspace 目录
              </Text>
              <div className="detail-path" style={{ marginTop: 6 }}>
                {detail.workspace_dir ?? '—'}
              </div>
            </div>
          </>
        ) : null}
      </Modal>

      {/* 创建 Modal */}
      <Modal
        title="创建 Agent"
        open={createOpen}
        okText="创建"
        cancelText="取消"
        confirmLoading={creating}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            label="Agent ID"
            name="agent_id"
            rules={[
              { required: true, message: '请输入 Agent ID' },
              {
                pattern: /^[a-zA-Z0-9_-]+$/,
                message: '仅允许字母、数字、下划线和连字符',
              },
            ]}
          >
            <Input placeholder="例如：assistant-main" autoComplete="off" />
          </Form.Item>

          <Divider plain style={{ margin: '4px 0 14px' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              模型配置（可选，默认使用 agent.json 配置）
            </Text>
          </Divider>

          <Form.Item label="模型来源" name="model_mode" initialValue="inherit">
            <Radio.Group>
              <Radio.Button value="inherit">继承默认</Radio.Button>
              <Radio.Button value="existing">选择现有模型</Radio.Button>
              <Radio.Button value="custom">自定义</Radio.Button>
            </Radio.Group>
          </Form.Item>

          {modelMode === 'existing' && (
            <Form.Item
              label="现有模型"
              name="selected_model"
              rules={[{ required: true, message: '请选择模型' }]}
            >
              <Select
                placeholder="从已配置的模型中选择"
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
                label="Provider"
                name="provider"
                rules={[{ required: true, message: '请输入 Provider' }]}
              >
                <Input placeholder="例如：openai / qwen" autoComplete="off" />
              </Form.Item>

              <Form.Item
                label="Model"
                name="model_name"
                rules={[{ required: true, message: '请输入模型名' }]}
              >
                <Input placeholder="例如：qwen3.6-plus" autoComplete="off" />
              </Form.Item>

              <Form.Item label="Base URL" name="base_url">
                <Input placeholder="例如：https://coding.dashscope.aliyuncs.com/v1" autoComplete="off" />
              </Form.Item>

              <Form.Item label="API Key 环境变量" name="api_key_env">
                <Input placeholder="例如：AGENTCORE_LLM_API_KEY（可选）" autoComplete="off" />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
