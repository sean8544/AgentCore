import { useEffect } from 'react';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { apiClient } from '../api/client';

export interface AgentInfo {
  agent_id: string;
  state?: string;
  session_count?: number;
}

/**
 * Resolve the current agent id (project-wide single entry point).
 * Priority: URL `?agent=` param > agentStore.selectedAgent.
 */
export const getAgentId = (): string =>
  new URLSearchParams(window.location.search).get('agent') ??
  useAgentStore.getState().selectedAgent;

/**
 * Reactive hook version of {@link getAgentId}. If the URL carries an
 * `?agent=` param it is written back to the store once on mount, so the
 * global selector stays in sync.
 */
export const useAgentId = (): string => {
  const selectedAgent = useAgentStore((s) => s.selectedAgent);
  const setSelectedAgent = useAgentStore((s) => s.setSelectedAgent);
  const urlAgent = new URLSearchParams(window.location.search).get('agent');

  useEffect(() => {
    if (urlAgent && urlAgent !== useAgentStore.getState().selectedAgent) {
      setSelectedAgent(urlAgent);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

          // 如果当前选中的 agent 不存在，回落到第一个或 'default'
          const { selectedAgent } = get();
          if (agents.length > 0 && !agents.find((a) => a.agent_id === selectedAgent)) {
            set({ selectedAgent: agents[0].agent_id });
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
