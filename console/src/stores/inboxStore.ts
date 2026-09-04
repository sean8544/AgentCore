import { create } from 'zustand';
import { apiClient } from '../api/client';

interface SessionInfo {
  session_id: string;
  agent_id: string;
  has_pending_approval?: boolean;
}

interface InboxState {
  /** Number of sessions currently waiting for a HITL approval. */
  pendingApprovalCount: number;

  // actions
  refreshPendingCount: () => Promise<void>;
  /** Sync the count from data a caller already fetched (no extra request). */
  setPendingApprovalCount: (count: number) => void;
}

/**
 * Global inbox badge state — counts sessions paused waiting for a HITL
 * approval decision (``GET /api/chat/sessions`` → ``has_pending_approval``).
 *
 * Kept tiny on purpose: MainLayout polls it periodically and the sidebar
 * menu renders the badge; pages that resolve approvals (Chat / Inbox)
 * call ``refreshPendingCount()`` to update it immediately.
 */
export const useInboxStore = create<InboxState>((set) => ({
  pendingApprovalCount: 0,

  refreshPendingCount: async () => {
    try {
      const { data } = await apiClient.get<SessionInfo[]>('/chat/sessions');
      const count = (data ?? []).filter((s) => s.has_pending_approval).length;
      set({ pendingApprovalCount: count });
    } catch {
      // Badge refresh must never surface errors — keep the last count.
    }
  },

  setPendingApprovalCount: (count: number) => {
    set({ pendingApprovalCount: count });
  },
}));
