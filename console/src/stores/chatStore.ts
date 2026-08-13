import { create } from 'zustand';
import { apiClient } from '../api/client';
import { getAgentId } from './agentStore';

/* ───────── Types ───────── */

export interface ToolCall {
  name: string;
  args: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  thinking?: string;
  toolCalls?: ToolCall[];
  /** true while this message is being generated via streaming */
  streaming?: boolean;
  /** true if the message failed to send */
  error?: boolean;
}

export interface Session {
  session_id: string;
  agent_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

interface ChatState {
  sessions: Session[];
  currentSessionId: string | null;
  messages: Message[];
  isStreaming: boolean;
  error: string | null;
  /** currently streaming assistant message id (stable across updates) */
  streamingMsgId: string | null;
  /** live thinking text accumulated during streaming */
  thinkingText: string;

  loadSessions: () => Promise<void>;
  loadHistory: (sessionId: string) => Promise<void>;
  selectSession: (sessionId: string | null) => Promise<void>;
  newSession: () => void;
  sendMessage: (content: string) => Promise<void>;
  clearError: () => void;
}

/* ───────── Constants / helpers ───────── */

let seq = 0;
const uid = (prefix: string) =>
  `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** Format ISO timestamp to compact display form, e.g. "08-12 14:30:05". */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso || '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function toMessage(m: Record<string, unknown>, index: number): Message {
  const role = String(m.role ?? 'assistant');
  const toolCallsRaw = Array.isArray(m.tool_calls) ? m.tool_calls : [];
  const toolCalls: ToolCall[] = toolCallsRaw.map((tc) => {
    const call = tc as Record<string, unknown>;
    const args = call.args;
    return {
      name: String(call.name ?? ''),
      args:
        typeof args === 'string'
          ? args
          : args && typeof args === 'object'
            ? JSON.stringify(args)
            : '',
    };
  });
  return {
    id: `hist-${index}-${String(m.timestamp ?? '')}`,
    role: (role === 'user' || role === 'system' ? role : 'assistant') as Message['role'],
    content: String(m.content ?? ''),
    timestamp: formatTimestamp(String(m.timestamp ?? '')),
    toolCalls: toolCalls.length ? toolCalls : undefined,
  };
}

function apiErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const resp = (err as { response?: { status?: number; data?: { detail?: unknown } } }).response;
    const detail = resp?.data?.detail;
    const detailStr =
      typeof detail === 'string' ? detail : detail != null ? JSON.stringify(detail) : '';
    if (resp?.status) {
      return detailStr ? `请求失败（${resp.status}）：${detailStr}` : `请求失败（${resp.status}）`;
    }
  }
  if (err instanceof Error) return err.message;
  return '请求失败，请稍后重试';
}

/* ───────── Store ───────── */

export const useChatStore = create<ChatState>((set, get) => {
  /** Fill the streaming placeholder with the full response from the sync endpoint. */
  const sendViaSync = async (msgId: string, sessionId: string | null, content: string) => {
    try {
      const resp = await apiClient.post('/chat', {
        agent_id: getAgentId(),
        message: content,
        session_id: sessionId ?? undefined,
      });
      const data = resp.data as {
        session_id?: string;
        content?: string;
        timestamp?: string;
        tool_calls?: { name?: string; args?: unknown }[];
      };
      const toolCalls: ToolCall[] = (data.tool_calls ?? []).map((tc) => ({
        name: tc.name ?? '',
        args: typeof tc.args === 'string' ? tc.args : JSON.stringify(tc.args ?? ''),
      }));
      set((state) => ({
        currentSessionId: data.session_id ?? state.currentSessionId,
        isStreaming: false,
        streamingMsgId: null,
        thinkingText: '',
        messages: state.messages.map((m) =>
          m.id === msgId
            ? {
                ...m,
                content: data.content ?? '',
                timestamp: formatTimestamp(data.timestamp ?? new Date().toISOString()),
                toolCalls: toolCalls.length ? toolCalls : undefined,
                streaming: false,
              }
            : m,
        ),
      }));
      await get().loadSessions();
    } catch (err) {
      set((state) => ({
        error: apiErrorMessage(err),
        isStreaming: false,
        streamingMsgId: null,
        thinkingText: '',
        messages: state.messages.map((m) =>
          m.id === msgId ? { ...m, error: true, streaming: false } : m,
        ),
      }));
    }
  };

  return {
    sessions: [],
    currentSessionId: null,
    messages: [],
    isStreaming: false,
    error: null,
    streamingMsgId: null,
    thinkingText: '',

    loadSessions: async () => {
      try {
        const resp = await apiClient.get('/chat/sessions', { params: { agent_id: getAgentId() } });
        const data = Array.isArray(resp.data) ? (resp.data as Session[]) : [];
        set({ sessions: data });
      } catch (err) {
        set({ error: `加载会话列表失败：${apiErrorMessage(err)}` });
      }
    },

    loadHistory: async (sessionId: string) => {
      try {
        const resp = await apiClient.get('/chat/history', {
          params: { session_id: sessionId, limit: 50 },
        });
        const data = Array.isArray(resp.data) ? (resp.data as Record<string, unknown>[]) : [];
        set({ messages: data.map(toMessage) });
      } catch (err) {
        set({ messages: [], error: `加载历史消息失败：${apiErrorMessage(err)}` });
      }
    },

    selectSession: async (sessionId: string | null) => {
      if (get().isStreaming) return;
      set({ currentSessionId: sessionId, messages: [], error: null, thinkingText: '' });
      if (sessionId) await get().loadHistory(sessionId);
    },

    newSession: () => {
      if (get().isStreaming) return;
      set({ currentSessionId: null, messages: [], error: null, thinkingText: '' });
    },

    sendMessage: async (content: string) => {
      const { isStreaming, currentSessionId } = get();
      if (isStreaming || !content.trim()) return;

      const userMsgId = uid('usr');
      const placeholderId = uid('ast');
      const now = formatTimestamp(new Date().toISOString());
      set((state) => ({
        isStreaming: true,
        error: null,
        thinkingText: '',
        streamingMsgId: placeholderId,
        messages: [
          ...state.messages,
          { id: userMsgId, role: 'user', content: content.trim(), timestamp: now },
          {
            id: placeholderId,
            role: 'assistant',
            content: '',
            timestamp: now,
            streaming: true,
          },
        ],
      }));

      await sendViaSync(placeholderId, currentSessionId, content.trim());
    },

    clearError: () => set({ error: null }),
  };
});
