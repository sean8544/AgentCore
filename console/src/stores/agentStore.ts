import { useEffect } from 'react';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { apiClient, getBasePath } from '../api/client';

export interface AgentInfo {
  agent_id: string;
  state?: string;
  session_count?: number;
  description?: string;
  enable_subagents?: boolean;
}

/**
 * Agent-scoped sections nested under ``/agents/:agentId/<section>``.
 * Global (instance-shared) pages stay at the top level instead.
 */
export const AGENT_SECTIONS = [
  'chat',
  'sessions',
  'files',
  'skills',
  'tools',
  'mcp',
  'memory',
  'heartbeat',
  'sandbox',
  'config',
  'stats',
] as const;

export type AgentSection = (typeof AGENT_SECTIONS)[number];

/** Build a deep-linkable agent-scoped path, e.g. ``/agents/foo/chat``. */
export const agentPath = (section: AgentSection | string, agentId: string): string =>
  `/agents/${agentId}/${section}`;

/**
 * Resolve the agent id from the URL only (``/agents/:agentId/...`` path or
 * ``?agent=`` query). Returns null when the URL carries no agent id.
 */
export const getAgentIdFromUrl = (): string | null => {
  // 子路径部署（如 /instance01）时先剥离挂载前缀；根路径部署时为空串，行为不变。
  const base = getBasePath();
  const rawPath = window.location.pathname;
  const pathname = base && rawPath.startsWith(base) ? rawPath.slice(base.length) || '/' : rawPath;
  const pathParts = pathname.split('/').filter(Boolean);
  if (pathParts.length >= 2 && pathParts[0] === 'agents' && pathParts[1]) {
    return pathParts[1];
  }
  return new URLSearchParams(window.location.search).get('agent');
};

/**
 * Resolve the current agent id (project-wide single entry point).
 * Priority: URL path segment (/chat/:agentId) > URL `?agent=` param > agentStore.selectedAgent.
 */
export const getAgentId = (): string => {
  return getAgentIdFromUrl() ?? useAgentStore.getState().selectedAgent;
};

/**
 * Reactive hook version of {@link getAgentId}. Whenever the URL carries
 * an agent id (path param or query param) it is written back to the
 * store, so the global selector stays in sync — including navigations
 * that happen without a remount (e.g. delegation-bubble clicks).
 */
export const useAgentId = (): string => {
  const selectedAgent = useAgentStore((s) => s.selectedAgent);
  const setSelectedAgent = useAgentStore((s) => s.setSelectedAgent);

  const urlAgent = getAgentIdFromUrl();

  useEffect(() => {
    if (urlAgent && urlAgent !== useAgentStore.getState().selectedAgent) {
      setSelectedAgent(urlAgent);
    }
  }, [urlAgent, setSelectedAgent]);

  return urlAgent ?? selectedAgent;
};

interface AgentState {
  agents: AgentInfo[];
  selectedAgent: string;
  loading: boolean;

  // actions
  refreshAgents: () => Promise<void>;
  setSelectedAgent: (agentId: string) => void;
}

/** 同域多实例（K8s 子路径部署）时按挂载前缀隔离存储，根路径部署保持原 key。 */
const storageSuffix = getBasePath();

export const useAgentStore = create<AgentState>()(
  persist(
    (set, get) => ({
      agents: [],
      selectedAgent: 'default',
      loading: false,

      refreshAgents: async () => {
        set({ loading: true });
        try {
          const res = await apiClient.get<AgentInfo[]>('/agents');
          const agents = Array.isArray(res.data) ? res.data : [];
          set({ agents, loading: false });

          // 同步选择：URL 中的 agent 优先（刷新后下拉列表与 URL 保持一致）；
          // 否则若当前选中的 agent 已不存在，回落到第一个。
          const { selectedAgent } = get();
          if (agents.length > 0) {
            const urlAgent = getAgentIdFromUrl();
            if (urlAgent && agents.some((a) => a.agent_id === urlAgent)) {
              if (selectedAgent !== urlAgent) {
                set({ selectedAgent: urlAgent });
              }
            } else if (!agents.some((a) => a.agent_id === selectedAgent)) {
              set({ selectedAgent: agents[0].agent_id });
            }
          }
        } catch (error) {
          set({ loading: false });
          console.error('Failed to refresh agents:', error);
        }
      },

      setSelectedAgent: (agentId: string) => {
        set({ selectedAgent: agentId });
      },
    }),
    {
      name: storageSuffix ? `agentcore-agent-store-${storageSuffix}` : 'agentcore-agent-store',
      partialize: (state) => ({ selectedAgent: state.selectedAgent }),
    },
  ),
);
