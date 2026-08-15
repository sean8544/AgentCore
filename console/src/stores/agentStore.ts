import { useEffect } from 'react';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { apiClient } from '../api/client';

export interface AgentInfo {
  agent_id: string;
  state?: string;
  session_count?: number;
  description?: string;
  enable_subagents?: boolean;
}

/** Agent-aware routes: /chat/:agentId, /files/:agentId, etc. */
const AGENT_ROUTES = ['chat', 'files', 'agent-config', 'agent-stats', 'sessions'];

/**
 * Resolve the agent id from the URL only (path param or ?agent= query).
 * Returns null when the URL carries no agent id.
 */
export const getAgentIdFromUrl = (): string | null => {
  const pathParts = window.location.pathname.split('/').filter(Boolean);
  if (pathParts.length >= 2 && AGENT_ROUTES.includes(pathParts[0]) && pathParts[1]) {
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
      name: 'agentcore-agent-store',
      partialize: (state) => ({ selectedAgent: state.selectedAgent }),
    },
  ),
);
