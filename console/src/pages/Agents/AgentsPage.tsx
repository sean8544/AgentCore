import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Checkbox,
  Descriptions,
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
  CloudServerOutlined,
  ColumnWidthOutlined,
  DeleteOutlined,
  DeploymentUnitOutlined,
  FileMarkdownOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  InfoCircleOutlined,
  BranchesOutlined,
  PlusOutlined,
  PoweroffOutlined,
  ReloadOutlined,
  SearchOutlined,
  StarFilled,
  StarOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { useAgentStore } from '../../stores/agentStore';
import { extractErrorMessage, formatDateTime } from '../../utils/helpers';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// 后端状态模型只有 idle / busy（请求驱动的派生事实），不再有 running/paused/stopped/error。
type AgentState = 'idle' | 'busy' | string;

interface AgentInfo {
  agent_id: string;
  state: AgentState | null;
  loaded: boolean;
  session_count: number;
  description?: string;
  enable_subagents?: boolean;
  backend_type?: string;
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
  exec_env?: 'local' | 'sandbox';
  sandbox_provider?: string;
  sandbox_image?: string;
  sandbox_lifecycle?: string;
  sandbox_timeout?: number;
  sandbox_memory_mb?: number;
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
  busy: { color: 'green', text: t('agents.stateBusy'), icon: <SyncOutlined spin /> },
  unloaded: { color: 'default', text: t('agents.stateUnloaded'), icon: <InboxOutlined /> },
});

// 展示态派生：workspace 未装载 → unloaded；否则按后端事实状态显示 busy / idle。
const deriveDisplayState = (agent: { state: AgentState | null; loaded: boolean }): string =>
  !agent.loaded ? 'unloaded' : agent.state === 'busy' ? 'busy' : 'idle';

function StateTag({ state }: { state: string }) {
  const { t } = useI18n();
  const STATE_META = getStateMeta(t);
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
// Resizable columns
//
// antd Table 原生不支持列宽拖拽，这里用 pointer 事件手写表头单元格手柄（无第三方依赖）。
// 宽度由页面 state 持有并落 localStorage，刷新后保留用户习惯。
// ---------------------------------------------------------------------------

type ColumnKey = 'agent_id' | 'state' | 'backend_type' | 'description' | 'session_count' | 'actions';

type ColumnWidths = Record<ColumnKey, number>;

const COL_WIDTH_STORAGE_KEY = 'agentcore.agents.columnWidths';
const MIN_COL_WIDTH = 60;
const MAX_COL_WIDTH = 900;
// rowSelection 勾选列固定占位，参与 scroll.x 计算
const SELECTION_COL_WIDTH = 48;

const DEFAULT_COL_WIDTHS: ColumnWidths = {
  agent_id: 220,
  state: 140,
  backend_type: 120,
  description: 200,
  session_count: 90,
  actions: 400,
};

const clampWidth = (value: number) =>
  Math.min(MAX_COL_WIDTH, Math.max(MIN_COL_WIDTH, Math.round(value)));

const loadColumnWidths = (): ColumnWidths => {
  const merged: ColumnWidths = { ...DEFAULT_COL_WIDTHS };
  try {
    const raw = window.localStorage.getItem(COL_WIDTH_STORAGE_KEY);
    if (!raw) return merged;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return merged;
    Object.entries(parsed as Record<string, unknown>).forEach(([key, value]) => {
      if (key in merged && typeof value === 'number' && Number.isFinite(value)) {
        merged[key as ColumnKey] = clampWidth(value);
      }
    });
  } catch {
    // 存储不可用或数据损坏时退回默认宽度
  }
  return merged;
};

type HeaderCellProps = {
  width?: number;
  onResize?: (width: number) => void;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  colSpan?: number;
  rowSpan?: number;
};

// rc-table 的 onHeaderCell 只声明了标准 HTML 属性，自定义 prop 需断言绕过
const asHeaderCellAttrs = (props: HeaderCellProps) =>
  props as React.HTMLAttributes<HTMLElement> & React.TdHTMLAttributes<HTMLElement>;

/** 表头单元格：右边缘拖拽手柄调整本列宽度。 */
function ResizableTitle(props: HeaderCellProps) {
  const { width, onResize, children, style, ...restProps } = props;
  const handleRef = useRef<HTMLDivElement | null>(null);
  const originRef = useRef({ x: 0, width: 0 });
  const [resizing, setResizing] = useState(false);

  // 无宽度列（勾选列等）不渲染手柄
  if (!onResize || typeof width !== 'number') {
    return <th {...restProps} style={style}>{children}</th>;
  }

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    originRef.current = { x: event.clientX, width };
    setResizing(true);
    handleRef.current?.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!resizing) return;
    onResize(clampWidth(originRef.current.width + (event.clientX - originRef.current.x)));
  };

  const handlePointerEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!resizing) return;
    setResizing(false);
    if (handleRef.current?.hasPointerCapture(event.pointerId)) {
      handleRef.current.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <th {...restProps} style={{ ...style, position: 'relative' }}>
      {children}
      <div
        ref={handleRef}
        role="separator"
        aria-orientation="vertical"
        className={`col-resize-handle${resizing ? ' is-resizing' : ''}`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onClick={(event) => event.stopPropagation()}
      />
    </th>
  );
}

const TABLE_COMPONENTS = { header: { cell: ResizableTitle } };

// ---------------------------------------------------------------------------
// Scoped styles — 控制台风格的字体与细节（仅作用于本页）
// ---------------------------------------------------------------------------

const PAGE_STYLES = `
.agents-page .ant-table-wrapper .ant-table {
  background: transparent;
}
.agents-page .agent-id-cell {
  font-family: var(--google-font-mono);
  font-weight: 600;
  font-size: 13px;
  color: var(--google-foreground);
}
/* Agent ID 列：单行截断，完整 ID 由 Tooltip 呈现；星标始终可见 */
.agents-page .agent-id-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.agents-page .agent-id-main .agent-id-cell {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agents-page .agent-id-main .current-mark {
  flex: 0 0 auto;
}
.agents-page .col-resize-handle {
  position: absolute;
  top: 0;
  right: -5px;
  bottom: 0;
  width: 10px;
  cursor: col-resize;
  user-select: none;
  touch-action: none;
  z-index: 2;
}
.agents-page .col-resize-handle::after {
  content: '';
  position: absolute;
  top: 22%;
  bottom: 22%;
  left: 4px;
  width: 2px;
  border-radius: 1px;
  background: transparent;
  transition: background var(--google-transition-fast);
}
.agents-page .col-resize-handle:hover::after,
.agents-page .col-resize-handle.is-resizing::after {
  background: var(--google-primary);
}
/* 最后一列的手柄不能探出表格边界，否则被横向滚动容器裁切 */
.agents-page .ant-table-thead th:last-child .col-resize-handle {
  right: 0;
}
.agents-page .mono {
  font-family: var(--google-font-mono);
  font-size: 12.5px;
}
.agents-page .ant-table-row.is-current > td {
  background: rgba(251, 188, 5, 0.08) !important;
}
.agents-page .ant-table-row.is-current > td:first-child {
  box-shadow: inset 3px 0 0 var(--google-chart-3);
}
.agents-page .current-mark {
  color: var(--google-chart-3);
}
.agents-page .detail-path {
  font-family: var(--google-font-mono);
  font-size: 12px;
  background: var(--google-muted);
  border: 1px solid var(--google-border);
  border-radius: var(--google-radius-md);
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

  // 列宽：表头手柄拖拽调整，并持久化到 localStorage
  const [columnWidths, setColumnWidths] = useState<ColumnWidths>(loadColumnWidths);

  useEffect(() => {
    try {
      window.localStorage.setItem(COL_WIDTH_STORAGE_KEY, JSON.stringify(columnWidths));
    } catch {
      // 存储不可用时仅影响刷新后的记忆，不影响当前会话
    }
  }, [columnWidths]);

  const setColumnWidth = useCallback((key: ColumnKey, width: number) => {
    setColumnWidths((prev) => (prev[key] === width ? prev : { ...prev, [key]: width }));
  }, []);

  const headerCell = (key: ColumnKey) =>
    asHeaderCellAttrs({ width: columnWidths[key], onResize: (width) => setColumnWidth(key, width) });

  // scroll.x：列宽之和 + 勾选列。容器变窄时走横向滚动，而不是挤压 Agent ID 列
  const tableScrollX = useMemo(
    () => Object.values(columnWidths).reduce((acc, value) => acc + value, 0) + SELECTION_COL_WIDTH,
    [columnWidths],
  );

  const resetColumnWidths = useCallback(() => {
    setColumnWidths({ ...DEFAULT_COL_WIDTHS });
    message.success(t('agents.columnWidthsReset'));
  }, [t]);

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
  const execEnv = Form.useWatch('exec_env', form) ?? 'local';

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

      // Sandbox backend config
      if (values.exec_env === 'sandbox') {
        const lifecycle: Record<string, unknown> = {
          strategy: values.sandbox_lifecycle ?? 'ephemeral',
          timeout: values.sandbox_timeout ?? 600,
          renew_on_chat: true,
        };
        const backend: Record<string, unknown> = {
          type: 'sandbox',
          provider: values.sandbox_provider ?? 'opensandbox',
          lifecycle,
        };
        if (values.sandbox_image?.trim()) backend.image = values.sandbox_image.trim();
        if (values.sandbox_memory_mb) backend.memory_mb = values.sandbox_memory_mb;
        body.settings = { backend };
      }

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
          navigate(`/agents/${createdId}/chat`);
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
    runAction(agentId, 'start', () => apiClient.post(`/agents/${agentId}/start`), t('agents.loadedSuccess', { id: agentId }));

  const handleStop = (agentId: string) =>
    runAction(agentId, 'stop', () => apiClient.post(`/agents/${agentId}/stop`), t('agents.unloadedSuccess', { id: agentId }));

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
        if (deriveDisplayState(agent) !== stateFilter) return false;
      }
      return true;
    });
  }, [agents, searchText, stateFilter]);

  const stateOptions = useMemo(() => {
    const present = new Set<string>();
    agents.forEach((agent) => present.add(deriveDisplayState(agent)));
    const base = [
      { value: 'busy', label: t('agents.stateBusy') },
      { value: 'idle', label: t('agents.stateIdle') },
      { value: 'unloaded', label: t('agents.stateUnloaded') },
    ];
    return base.filter((option) => present.has(option.value));
  }, [agents, t]);

  // 统计 chips
  const stats = useMemo(() => {
    const total = agents.length;
    const busy = agents.filter((a) => a.loaded && a.state === 'busy').length;
    const sessions = agents.reduce((acc, a) => acc + (a.session_count || 0), 0);
    return { total, busy, sessions };
  }, [agents]);

  // --- Table columns ---------------------------------------------------------

  const columns = useMemo<ColumnsType<AgentInfo>>(
    () => [
      {
        title: 'Agent ID',
        dataIndex: 'agent_id',
        key: 'agent_id',
        width: columnWidths.agent_id,
        onHeaderCell: () => headerCell('agent_id'),
        render: (value: string, record: AgentInfo) => {
          const isCurrent = currentAgentId === record.agent_id;
          return (
            <div className="agent-id-main">
              <Tooltip title={value}>
                <span className="agent-id-cell">{value}</span>
              </Tooltip>
              {isCurrent && (
                <Tooltip title={t('agents.current')}>
                  <StarFilled className="current-mark" style={{ fontSize: 13 }} />
                </Tooltip>
              )}
            </div>
          );
        },
      },
      {
        title: t('common.status'),
        dataIndex: 'state',
        key: 'state',
        width: columnWidths.state,
        onHeaderCell: () => headerCell('state'),
        render: (_: AgentState | null, record: AgentInfo) => (
          <StateTag state={deriveDisplayState(record)} />
        ),
      },
      {
        title: t('agents.backendEnv'),
        dataIndex: 'backend_type',
        key: 'backend_type',
        width: columnWidths.backend_type,
        onHeaderCell: () => headerCell('backend_type'),
        filters: [
          { text: t('agents.backendLocal'), value: 'local' },
          { text: t('agents.backendSandbox'), value: 'sandbox' },
        ],
        onFilter: (value: unknown, record: AgentInfo) => {
          const bt = record.backend_type || 'local';
          return bt === value;
        },
        render: (value: string | undefined) => {
          const isSandbox = value === 'sandbox';
          return (
            <Tag
              color={isSandbox ? 'green' : 'default'}
              icon={isSandbox ? <CloudServerOutlined /> : <FolderOpenOutlined />}
              style={{ fontSize: 12 }}
            >
              {isSandbox ? t('agents.backendSandbox') : t('agents.backendLocal')}
            </Tag>
          );
        },
      },
      {
        title: t('agents.descriptionLabel'),
        dataIndex: 'description',
        key: 'description',
        width: columnWidths.description,
        onHeaderCell: () => headerCell('description'),
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
        width: columnWidths.session_count,
        onHeaderCell: () => headerCell('session_count'),
        render: (value: number) => <span className="mono">{value ?? 0}</span>,
      },
      {
        title: t('common.actions'),
        key: 'actions',
        width: columnWidths.actions,
        onHeaderCell: () => headerCell('actions'),
        render: (_: unknown, record: AgentInfo) => {
          const busy = busyOf(record.agent_id);
          const anyBusy = busy !== null;
          // 启动/停止现在是装载/卸载语义：基于 workspace 是否已装载，而非生命周期状态。
          const canStart = !record.loaded;
          const canStop = record.loaded;
          const isCurrent = currentAgentId === record.agent_id;

          return (
            <Space size="small" wrap>
              <Tooltip title={t('agents.setAsCurrent')}>
                <Button
                  size="small"
                  type="text"
                  icon={isCurrent ? <CheckCircleFilled style={{ color: 'var(--google-chart-3)' }} /> : <StarOutlined />}
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
              <Tooltip title={t('agents.loadHint')}>
                <Button
                  size="small"
                  type="link"
                  icon={<CaretRightOutlined />}
                  disabled={!canStart || anyBusy}
                  loading={busy === 'start'}
                  onClick={() => handleStart(record.agent_id)}
                >
                  {t('agents.load')}
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
                {t('agents.unload')}
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
    [rowAction, currentAgentId, columnWidths, t],
  );

  // --- Render ----------------------------------------------------------------

  const kernelFiles = detail?.kernel_files ?? [];

  return (
    <div className="agents-page">
      <style>{PAGE_STYLES}</style>

      <GooglePageHeader
        icon={<DeploymentUnitOutlined />}
        title={t('agents.title')}
        subtitle={t('agents.subtitle')}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
            {t('agents.createAgent')}
          </Button>
        }
      />

      {/* 统计卡片 */}
      <GoogleCard style={{ marginBottom: 'var(--google-space-8)' }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: 'var(--google-space-6)',
          }}
        >
          <div>
            <div
              style={{
                fontSize: 'var(--google-text-3xl)',
                fontWeight: 'var(--google-font-bold)',
                color: 'var(--google-foreground)',
                lineHeight: 'var(--google-leading-tight)',
              }}
            >
              {stats.total}
            </div>
            <div style={{ fontSize: 'var(--google-text-sm)', color: 'var(--google-muted-foreground)' }}>
              {t('agents.totalAgents')}
            </div>
          </div>
          <div>
            <div
              style={{
                fontSize: 'var(--google-text-3xl)',
                fontWeight: 'var(--google-font-bold)',
                color: 'var(--google-chart-5)',
                lineHeight: 'var(--google-leading-tight)',
              }}
            >
              {stats.busy}
            </div>
            <div style={{ fontSize: 'var(--google-text-sm)', color: 'var(--google-muted-foreground)' }}>
              {t('agents.busyAgents')}
            </div>
          </div>
          <div>
            <div
              style={{
                fontSize: 'var(--google-text-3xl)',
                fontWeight: 'var(--google-font-bold)',
                color: 'var(--google-foreground)',
                lineHeight: 'var(--google-leading-tight)',
              }}
            >
              {stats.sessions}
            </div>
            <div style={{ fontSize: 'var(--google-text-sm)', color: 'var(--google-muted-foreground)' }}>
              {t('agents.totalSessions')}
            </div>
          </div>
        </div>
      </GoogleCard>

      {/* Agent 列表 */}
      <GoogleCard>
        {/* 工具栏：搜索 + 筛选 + 批量操作 */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            marginBottom: 'var(--google-space-6)',
            flexWrap: 'wrap',
            gap: 'var(--google-space-4)',
          }}
        >
          <Space wrap>
            <Input
              allowClear
              prefix={<SearchOutlined style={{ color: 'var(--google-muted-foreground)' }} />}
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
          <Tooltip title={t('agents.resetColumnWidthsHint')}>
            <Button size="small" type="text" icon={<ColumnWidthOutlined />} onClick={resetColumnWidths}>
              {t('agents.resetColumnWidths')}
            </Button>
          </Tooltip>
        </div>

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
        tableLayout="fixed"
        scroll={{ x: tableScrollX }}
        components={TABLE_COMPONENTS}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('agents.noMatching')} /> }}
      />
      </GoogleCard>

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
                  <StateTag state={deriveDisplayState(detail)} />
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
                  <Space size="small" wrap>
                    {kernelFiles.map((name) => (
                      <Tag key={name} icon={<FileMarkdownOutlined />} color="green">
                        {name}
                        <Text type="secondary" style={{ fontSize: 11, marginLeft: 'var(--google-space-2)' }}>
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

            <div style={{ marginTop: 'var(--google-space-6)' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                <FolderOpenOutlined style={{ marginRight: 'var(--google-space-3)' }} />
                {t('agents.workspaceDir')}
              </Text>
              <div className="detail-path" style={{ marginTop: 'var(--google-space-3)' }}>
                {detail.workspace_dir ?? '—'}
              </div>
            </div>

            {(detail.settings?.enable_subagents || (detail.subagents ?? []).length > 0) && (
              <div style={{ marginTop: 'var(--google-space-6)' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <BranchesOutlined style={{ marginRight: 'var(--google-space-3)' }} />
                  {t('agentConfig.mySubagents')}
                </Text>
                <Space size="small" wrap style={{ marginTop: 'var(--google-space-3)' }}>
                  {(detail.subagents ?? []).map((s) => (
                    <Tag key={s.name} color="blue" icon={<BranchesOutlined />} style={{ marginInlineEnd: 0 }}>
                      {s.name}
                      {s.description && (
                        <Text type="secondary" style={{ fontSize: 11, marginLeft: 'var(--google-space-2)' }}>
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
                      if (createStep === 1 && execEnv === 'sandbox') {
                        await form.validateFields(['sandbox_image']);
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
          style={{ marginBottom: 'var(--google-space-8)' }}
          items={[
            { title: t('agents.stepBasicInfo') },
            { title: t('agents.stepModelConfig') },
            { title: t('agents.stepCapabilities') },
          ]}
        />

        <Form form={form} layout="vertical" style={{ marginTop: 'var(--google-space-4)' }}>
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

            {/* ──── 执行环境 ──── */}
            <div style={{ marginTop: 24, borderTop: '1px solid var(--google-border)', paddingTop: 20 }}>
              <Form.Item label={t('sandbox.execEnv')} name="exec_env" initialValue="local">
                <Radio.Group>
                  <Radio value="local">
                    <div>
                      <div style={{ fontWeight: 500 }}>{t('sandbox.localFs')}</div>
                      <div style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>{t('sandbox.localFsDesc')}</div>
                    </div>
                  </Radio>
                  <Radio value="sandbox">
                    <div>
                      <div style={{ fontWeight: 500 }}>{t('sandbox.sandboxEnv')}</div>
                      <div style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>{t('sandbox.sandboxEnvDesc')}</div>
                    </div>
                  </Radio>
                </Radio.Group>
              </Form.Item>

              {execEnv === 'sandbox' && (
                <div style={{ marginLeft: 24, marginTop: 8 }}>
                  <Form.Item label={t('sandbox.provider')} name="sandbox_provider" initialValue="opensandbox">
                    <Select
                      options={[
                        { value: 'opensandbox', label: 'OpenSandbox' },
                        { value: 'e2b', label: 'E2B' },
                        { value: 'daytona', label: 'Daytona' },
                      ]}
                      style={{ width: 200 }}
                    />
                  </Form.Item>

                  <Form.Item
                    label={t('sandbox.image')}
                    name="sandbox_image"
                    rules={[{ required: true, message: t('sandbox.imageRequired') }]}
                  >
                    <AutoComplete
                      placeholder={t('sandbox.imagePlaceholder')}
                      options={[
                        { value: 'ghcr.io/agent-infra/sandbox:latest', label: 'AIO (Browser + Shell + VSCode + Jupyter)' },
                        { value: 'python:3.12-slim', label: 'Python 3.12 (slim)' },
                      ]}
                      filterOption={(input, option) =>
                        String(option?.value ?? '').toLowerCase().includes(input.toLowerCase())
                      }
                    />
                  </Form.Item>

                  <Form.Item label={t('sandbox.lifecycle')} name="sandbox_lifecycle" initialValue="ephemeral">
                    <Select
                      options={[
                        { value: 'ephemeral', label: `${t('sandbox.ephemeral')} — ${t('sandbox.ephemeralDesc')}` },
                        { value: 'pause-on-idle', label: `${t('sandbox.pauseOnIdle')} — ${t('sandbox.pauseOnIdleDesc')}` },
                        { value: 'persistent', label: `${t('sandbox.persistent')} — ${t('sandbox.persistentDesc')}` },
                      ]}
                      style={{ width: 400 }}
                    />
                  </Form.Item>

                  <Form.Item label={t('sandbox.timeout')} name="sandbox_timeout" initialValue={600}>
                    <Input type="number" style={{ width: 120 }} />
                  </Form.Item>

                  <Form.Item label={t('sandbox.memoryMb')} name="sandbox_memory_mb" initialValue={512}>
                    <Input type="number" style={{ width: 120 }} />
                  </Form.Item>
                </div>
              )}
            </div>
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
