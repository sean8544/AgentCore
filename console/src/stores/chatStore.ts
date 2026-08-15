import { create } from 'zustand';
import { apiClient } from '../api/client';
import { getAgentId } from './agentStore';

/* ───────── Types ───────── */

export interface ToolCall {
  name: string;
  args: string;
}

export interface Delegation {
  /** subagent id the task was delegated to */
  subagent: string;
  description: string;
}

export interface TodoItem {
  title?: string;
  content?: string;
  description?: string;
  status?: string;
  priority?: string;
}

export interface DelegationRecord {
  parent_agent_id?: string;
  task_description?: string;
  timestamp?: string;
}

export interface ApprovalAction {
  name: string;
  args: Record<string, unknown>;
  description: string;
  /**
   * Decisions the backend allows for this action (subset of
   * approve / edit / reject / respond).  Absent = all decisions open.
   */
  allowed_decisions?: string[];
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
  /** subagent delegations made by this message ("🤖 已委派给 …") */
  delegations?: Delegation[];
  /** present when this message triggered a HITL approval */
  approvalRequest?: { actions: ApprovalAction[] };
  /** persisted todo plan for this turn (restored from history) */
  todos?: TodoItem[];
  /** HITL decision recorded for the approval this message requested */
  approval?: { decision: string; tool_name?: string; timestamp?: string };
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
  /** structured task plan from write_todos events (current turn) */
  todos: TodoItem[];
  /** delegation records received by the current agent (who delegated what) */
  delegationRecords: DelegationRecord[];
  /** cumulative token usage for the current streaming turn */
  tokenUsage: { input_tokens: number; output_tokens: number } | null;

  loadSessions: () => Promise<void>;
  loadHistory: (sessionId: string) => Promise<void>;
  selectSession: (sessionId: string | null) => Promise<void>;
  newSession: () => void;
  sendMessage: (content: string) => Promise<void>;
  /** Abort the in-flight stream — keeps the partial reply, stops the agent */
  stopStreaming: () => void;
  submitApproval: (decision: 'approve' | 'edit' | 'reject', editedArgs?: Record<string, unknown>, message?: string) => Promise<void>;
  loadDelegations: () => Promise<void>;
  clearError: () => void;
}

/* ───────── Constants / helpers ───────── */

let seq = 0;
const uid = (prefix: string) =>
  `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** Abort controller of the in-flight stream (stop-conversation support). */
let abortController: AbortController | null = null;

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
  const allCalls: ToolCall[] = toolCallsRaw.map((tc) => {
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
  // Recover delegations from persisted tool_calls (task → subagent).
  const delegations: Delegation[] = allCalls
    .filter((tc) => tc.name === 'task')
    .map((tc) => {
      let args: Record<string, unknown> = {};
      try {
        args = tc.args ? (JSON.parse(tc.args) as Record<string, unknown>) : {};
      } catch {
        args = {};
      }
      return {
        subagent: String(args.subagent_type ?? ''),
        description: String(args.description ?? ''),
      };
    })
    .filter((d) => d.subagent);
  // write_todos renders as the todo panel and task as a delegation
  // bubble — never as raw tool cards.
  const toolCalls = allCalls.filter(
    (tc) => tc.name && tc.name !== 'write_todos' && tc.name !== 'task',
  );
  // Recover reasoning chain, todo plan and approval state persisted by the
  // backend (fields: reasoning / todos / approval_request / approval).
  const reasoning = String(m.reasoning ?? m.thinking ?? '');
  const todosRaw = Array.isArray(m.todos) ? (m.todos as TodoItem[]) : undefined;
  const approvalRaw = (m.approval ?? null) as {
    decision?: string;
    tool_name?: string;
    timestamp?: string;
  } | null;
  const approvalRequestRaw = (m.approval_request ?? null) as {
    actions?: unknown[];
  } | null;
  return {
    id: `hist-${index}-${String(m.timestamp ?? '')}`,
    role: (role === 'user' || role === 'system' ? role : 'assistant') as Message['role'],
    content: String(m.content ?? ''),
    timestamp: formatTimestamp(String(m.timestamp ?? '')),
    thinking: reasoning || undefined,
    toolCalls: toolCalls.length ? toolCalls : undefined,
    delegations: delegations.length ? delegations : undefined,
    todos: todosRaw?.length ? todosRaw : undefined,
    approval:
      approvalRaw && approvalRaw.decision
        ? {
            decision: String(approvalRaw.decision),
            tool_name: approvalRaw.tool_name ? String(approvalRaw.tool_name) : undefined,
            timestamp: approvalRaw.timestamp ? String(approvalRaw.timestamp) : undefined,
          }
        : undefined,
    approvalRequest:
      approvalRequestRaw && Array.isArray(approvalRequestRaw.actions) && approvalRequestRaw.actions.length
        ? {
            actions: approvalRequestRaw.actions.map((a) => {
              const aa = a as Record<string, unknown>;
              return {
                name: String(aa.name ?? ''),
                args: (aa.args ?? {}) as Record<string, unknown>,
                description: String(aa.description ?? ''),
              };
            }),
          }
        : undefined,
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
  /** Stream response via SSE (Task 3.3) — token-by-token updates. */
  const sendViaStream = async (msgId: string, sessionId: string | null, content: string) => {
    const agentId = getAgentId();
    let accumulated = '';
    let sessionIdResolved = sessionId;

    try {
      // Abort any previous stream first, then arm the controller for this
      // turn so the stop button can cancel it mid-flight.
      abortController?.abort();
      abortController = new AbortController();
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: abortController.signal,
        body: JSON.stringify({
          agent_id: agentId,
          message: content,
          session_id: sessionId ?? undefined,
        }),
      });

      if (!response.ok || !response.body) {
        throw new Error(`Stream failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        let currentEvent = '';
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            const dataStr = line.slice(6);
            try {
              const data = JSON.parse(dataStr);
              if (currentEvent === 'messages') {
                accumulated += data.content ?? '';
                const reasoningChunk = data.reasoning ?? '';
                set((state) => ({
                  thinkingText: reasoningChunk
                    ? state.thinkingText + reasoningChunk
                    : state.thinkingText,
                  messages: state.messages.map((m) =>
                    m.id === msgId ? { ...m, content: accumulated } : m,
                  ),
                }));
              } else if (currentEvent === 'interrupts') {
                if (data.approval_request) {
                  const actions = (data.approval_request.actions ?? []).map((a: Record<string, unknown>) => ({
                    name: String(a.name ?? ''),
                    args: (a.args ?? {}) as Record<string, unknown>,
                    description: String(a.description ?? ''),
                  }));
                  // The session is created server-side for the new-session
                  // case; capture it here or the approval buttons silently
                  // do nothing (submitApproval bails on a null session id).
                  if (data.session_id) sessionIdResolved = data.session_id;
                  set((state) => ({
                    currentSessionId: sessionIdResolved ?? state.currentSessionId,
                    isStreaming: false,
                    streamingMsgId: null,
                    messages: state.messages.map((m) =>
                      m.id === msgId
                        ? { ...m, content: '\u23F8 \u7B49\u5F85\u5BA1\u6279\u2026', streaming: false, approvalRequest: { actions } }
                        : m,
                    ),
                  }));
                  return;
                }
              } else if (currentEvent === 'tool_calls') {
                // Early tool_calls detection — show cards before node completes.
                // write_todos is projected via todo events, never as a card.
                const earlyCalls = Array.isArray(data.tool_calls) ? data.tool_calls : [];
                const taskCalls = earlyCalls.filter(
                  (c: Record<string, unknown>) => c && c.name === 'task',
                );
                // task calls surface as delegation bubbles, not cards.
                if (taskCalls.length > 0) {
                  const delegations = taskCalls
                    .map((c: Record<string, unknown>) => {
                      const rawArgs = c.args;
                      let args: Record<string, unknown> = {};
                      if (typeof rawArgs === 'string') {
                        try { args = JSON.parse(rawArgs) as Record<string, unknown>; } catch { args = {}; }
                      } else if (rawArgs && typeof rawArgs === 'object') {
                        args = rawArgs as Record<string, unknown>;
                      }
                      return {
                        subagent: String(args.subagent_type ?? ''),
                        description: String(args.description ?? ''),
                      };
                    })
                    .filter((d: Delegation) => d.subagent);
                  if (delegations.length > 0) {
                    set((state) => ({
                      messages: state.messages.map((m) => {
                        if (m.id !== msgId) return m;
                        const existing = m.delegations ?? [];
                        const fresh = delegations.filter(
                          (d: Delegation) =>
                            !existing.some((e) => e.subagent === d.subagent && e.description === d.description),
                        );
                        return fresh.length ? { ...m, delegations: [...existing, ...fresh] } : m;
                      }),
                    }));
                  }
                }
                const cardCalls = earlyCalls.filter(
                  (c: Record<string, unknown>) => c && c.name && c.name !== 'task' && c.name !== 'write_todos',
                );
                if (cardCalls.length > 0) {
                  const calls: ToolCall[] = cardCalls.map((c: Record<string, unknown>) => ({
                    name: String(c.name ?? ''),
                    args: typeof c.args === 'string' ? c.args : JSON.stringify(c.args ?? {}),
                  }));
                  set((state) => ({
                    messages: state.messages.map((m) => {
                      if (m.id !== msgId) return m;
                      const existing = m.toolCalls ?? [];
                      // Deduplicate: skip calls already shown (same name + args).
                      const fresh = calls.filter(
                        (c) => !existing.some((e) => e.name === c.name && e.args === c.args),
                      );
                      return fresh.length ? { ...m, toolCalls: [...existing, ...fresh] } : m;
                    }),
                  }));
                }
              } else if (currentEvent === 'subagents') {
                // Subagent delegation / tool execution updates.
                const toolCalls = Array.isArray(data.tool_calls) ? data.tool_calls : [];
                const taskCalls = toolCalls.filter(
                  (c: Record<string, unknown>) => c && c.name === 'task',
                );
                if (taskCalls.length > 0) {
                  const delegations = taskCalls
                    .map((c: Record<string, unknown>) => {
                      const rawArgs = c.args;
                      let args: Record<string, unknown> = {};
                      if (typeof rawArgs === 'string') {
                        try {
                          args = JSON.parse(rawArgs) as Record<string, unknown>;
                        } catch {
                          args = {};
                        }
                      } else if (rawArgs && typeof rawArgs === 'object') {
                        args = rawArgs as Record<string, unknown>;
                      }
                      return {
                        subagent: String(args.subagent_type ?? ''),
                        description: String(args.description ?? ''),
                      };
                    })
                    .filter((d: Delegation) => d.subagent);
                  if (delegations.length > 0) {
                    set((state) => ({
                      messages: state.messages.map((m) => {
                        if (m.id !== msgId) return m;
                        const existing = m.delegations ?? [];
                        const fresh = delegations.filter(
                          (d: Delegation) =>
                            !existing.some((e) => e.subagent === d.subagent && e.description === d.description),
                        );
                        return fresh.length ? { ...m, delegations: [...existing, ...fresh] } : m;
                      }),
                    }));
                  }
                }
              } else if (currentEvent === 'todo') {
                // Structured task plan from write_todos.
                const todos = Array.isArray(data.todos) ? data.todos : [];
                if (todos.length > 0) {
                  set({ todos: todos as TodoItem[] });
                }
              } else if (currentEvent === 'token_usage') {
                // Cumulative token consumption.
                const inp = Number(data.input_tokens ?? 0);
                const out = Number(data.output_tokens ?? 0);
                if (inp || out) {
                  set({ tokenUsage: { input_tokens: inp, output_tokens: out } });
                }
              } else if (currentEvent === 'done') {
                sessionIdResolved = data.session_id ?? sessionIdResolved;
                // Fallback: some models only report usage on the final done
                // event — surface it if no token_usage event arrived.
                const inp = Number(data.input_tokens ?? 0);
                const out = Number(data.output_tokens ?? 0);
                if ((inp || out) && !get().tokenUsage) {
                  set({ tokenUsage: { input_tokens: inp, output_tokens: out } });
                }
              } else if (currentEvent === 'error') {
                throw new Error(data.detail ?? 'Stream error');
              }
            } catch (e) {
              // 真实流式错误（error 事件）必须向上传播，由外层 catch 显示提示；
              // 仅跳过损坏的数据行（如半行 JSON）。
              if (currentEvent === 'error') throw e;
              continue;
            }
          }
        }
      }

      // Stream completed — finalise the message.
      // Preserve thinking text and todos so they don't disappear.
      set((state) => ({
        currentSessionId: sessionIdResolved ?? state.currentSessionId,
        isStreaming: false,
        streamingMsgId: null,
        thinkingText: '',
        messages: state.messages.map((m) =>
          m.id === msgId
            ? {
                ...m,
                content: accumulated,
                streaming: false,
                thinking: state.thinkingText || m.thinking,
                todos: state.todos.length ? state.todos : m.todos,
              }
            : m,
        ),
      }));
      await get().loadSessions();
    } catch (err) {
      // User pressed stop: keep the partial reply, no error banner.
      const aborted =
        typeof DOMException !== 'undefined' &&
        err instanceof DOMException &&
        err.name === 'AbortError';
      set((state) => ({
        error: aborted ? state.error : apiErrorMessage(err),
        isStreaming: false,
        streamingMsgId: null,
        thinkingText: '',
        messages: state.messages.map((m) =>
          m.id === msgId
            ? {
                ...m,
                content: accumulated || m.content,
                error: aborted ? m.error : true,
                streaming: false,
                thinking: state.thinkingText || m.thinking,
                todos: state.todos.length ? state.todos : m.todos,
              }
            : m,
        ),
      }));
      abortController = null;
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
    todos: [],
    delegationRecords: [],
    tokenUsage: null,

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
        const msgs = data.map(toMessage);
        // Restore the latest persisted todo plan into the side panel so the
        // task list survives a page reload.
        let todos: TodoItem[] = [];
        for (let i = msgs.length - 1; i >= 0; i -= 1) {
          if (msgs[i].todos && msgs[i].todos!.length) {
            todos = msgs[i].todos!;
            break;
          }
        }
        set({ messages: msgs, todos });
      } catch (err) {
        set({ messages: [], error: `加载历史消息失败：${apiErrorMessage(err)}` });
      }
    },

    selectSession: async (sessionId: string | null) => {
      if (get().isStreaming) return;
      set({ currentSessionId: sessionId, messages: [], error: null, thinkingText: '', todos: [], tokenUsage: null });
      if (sessionId) await get().loadHistory(sessionId);
    },

    newSession: () => {
      if (get().isStreaming) return;
      set({ currentSessionId: null, messages: [], error: null, thinkingText: '', todos: [], tokenUsage: null });
    },

    sendMessage: async (content: string) => {
      const { isStreaming, currentSessionId } = get();
      if (isStreaming || !content.trim()) return;

      // Reset the todo panel and token usage for the new turn.
      set({ todos: [], tokenUsage: null });

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

      await sendViaStream(placeholderId, currentSessionId, content.trim());
    },

    stopStreaming: () => {
      if (!get().isStreaming) return;
      // Cancel the fetch — the backend stops the agent graph run when the
      // connection drops and keeps the partial turn in session history.
      abortController?.abort();
      abortController = null;
      set((state) => ({
        isStreaming: false,
        streamingMsgId: null,
        thinkingText: '',
        messages: state.messages.map((m) =>
          m.streaming ? { ...m, streaming: false } : m,
        ),
      }));
    },

    submitApproval: async (decision, editedArgs, message) => {
      let { currentSessionId, isStreaming } = get();
      if (isStreaming) return;

      // 新会话中的审批：会话 id 由 SSE interrupts 事件带回（后端修复版）。
      // 兼容旧后端（事件无 session_id）时，从刚刷新的会话列表找回服务端
      // 已创建的会话（列表按 updated_at 降序），避免审批按钮“点了没反应”。
      if (!currentSessionId) {
        await get().loadSessions();
        currentSessionId = get().sessions[0]?.session_id ?? null;
      }
      if (!currentSessionId) return;

      const agentId = getAgentId();
      const approvalMsgId = uid('ast');
      const now = formatTimestamp(new Date().toISOString());

      // Add a placeholder assistant message for the approval result.
      set((state) => ({
        isStreaming: true,
        error: null,
        thinkingText: '',
        streamingMsgId: approvalMsgId,
        messages: [
          ...state.messages,
          {
            id: approvalMsgId,
            role: 'assistant' as const,
            content: '',
            timestamp: now,
            streaming: true,
          },
        ],
      }));

      try {
        // Use streaming endpoint for real-time feedback after approval.
        const resp = await fetch(
          `${apiClient.defaults.baseURL}/chat/${agentId}/sessions/${currentSessionId}/approval/stream`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              decision,
              edited_args: editedArgs ?? undefined,
              message: message ?? undefined,
            }),
          },
        );

        if (!resp.ok) {
          const errBody = await resp.json().catch(() => ({ detail: resp.statusText }));
          throw new Error((errBody as { detail?: string }).detail ?? resp.statusText);
        }

        const reader = resp.body?.getReader();
        if (!reader) throw new Error('No response body');
        const decoder = new TextDecoder();
        let accumulated = '';

        // Clear approvalRequest from the previous message.
        const clearPrevApproval = (msgs: Message[]) =>
          msgs.map((m) =>
            m.approvalRequest
              ? { ...m, approvalRequest: undefined, approval: { decision, timestamp: now } }
              : m,
          );

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const text = decoder.decode(value, { stream: true });
          const lines = text.split('\n');
          let currentEvent = '';

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              const dataStr = line.slice(6);
              try {
                const data = JSON.parse(dataStr);
                if (currentEvent === 'messages') {
                  accumulated += data.content ?? '';
                  const reasoningChunk = data.reasoning ?? '';
                  set((state) => ({
                    thinkingText: reasoningChunk
                      ? state.thinkingText + reasoningChunk
                      : state.thinkingText,
                    messages: clearPrevApproval(state.messages).map((m) =>
                      m.id === approvalMsgId ? { ...m, content: accumulated } : m,
                    ),
                  }));
                } else if (currentEvent === 'tool_calls') {
                  const earlyCalls = Array.isArray(data.tool_calls) ? data.tool_calls : [];
                  if (earlyCalls.length > 0) {
                    const calls: ToolCall[] = earlyCalls.map((c: Record<string, unknown>) => ({
                      name: String(c.name ?? ''),
                      args: typeof c.args === 'string' ? c.args : JSON.stringify(c.args ?? {}),
                    }));
                    set((state) => ({
                      messages: clearPrevApproval(state.messages).map((m) =>
                        m.id === approvalMsgId
                          ? { ...m, toolCalls: [...(m.toolCalls ?? []), ...calls] }
                          : m,
                      ),
                    }));
                  }
                } else if (currentEvent === 'todo') {
                  const todos = Array.isArray(data.todos) ? data.todos : [];
                  if (todos.length > 0) {
                    set({ todos: todos as TodoItem[] });
                  }
                } else if (currentEvent === 'token_usage') {
                  const inp = Number(data.input_tokens ?? 0);
                  const out = Number(data.output_tokens ?? 0);
                  if (inp || out) {
                    set({ tokenUsage: { input_tokens: inp, output_tokens: out } });
                  }
                } else if (currentEvent === 'interrupts') {
                  // Another round of approval needed.
                  if (data.approval_request) {
                    const actions = (data.approval_request.actions ?? []).map((a: Record<string, unknown>) => ({
                      name: String(a.name ?? ''),
                      args: (a.args ?? {}) as Record<string, unknown>,
                      description: String(a.description ?? ''),
                    }));
                    set((state) => ({
                      isStreaming: false,
                      streamingMsgId: null,
                      thinkingText: '',
                      messages: [
                        ...clearPrevApproval(state.messages).map((m) =>
                          m.id === approvalMsgId
                            ? { ...m, content: '\u23F8 \u7B49\u5F85\u5BA1\u6279\u2026', streaming: false, approvalRequest: { actions } }
                            : m,
                        ),
                      ],
                    }));
                    await get().loadSessions();
                    return;
                  }
                } else if (currentEvent === 'done') {
                  // Stream completed successfully — usage fallback as in the
                  // main stream (some models only report usage here).
                  const inp = Number(data.input_tokens ?? 0);
                  const out = Number(data.output_tokens ?? 0);
                  if ((inp || out) && !get().tokenUsage) {
                    set({ tokenUsage: { input_tokens: inp, output_tokens: out } });
                  }
                } else if (currentEvent === 'error') {
                  throw new Error(data.detail ?? 'Stream error');
                }
              } catch (e) {
                if (currentEvent === 'error') throw e;
                continue;
              }
            }
          }
        }

        // Stream completed — finalise the message.
        set((state) => ({
          isStreaming: false,
          streamingMsgId: null,
          thinkingText: '',
          messages: [
            ...clearPrevApproval(state.messages).map((m) =>
              m.id === approvalMsgId
                ? {
                    ...m,
                    content: accumulated,
                    streaming: false,
                    thinking: state.thinkingText || m.thinking,
                    todos: state.todos.length ? state.todos : m.todos,
                  }
                : m,
            ),
          ],
        }));
        await get().loadSessions();
      } catch (err) {
        set((state) => ({
          error: apiErrorMessage(err),
          isStreaming: false,
          streamingMsgId: null,
          thinkingText: '',
          messages: state.messages.map((m) =>
            m.id === approvalMsgId ? { ...m, error: true, streaming: false } : m,
          ),
        }));
      }
    },

    clearError: () => set({ error: null }),

    loadDelegations: async () => {
      const agentId = getAgentId();
      if (!agentId) {
        set({ delegationRecords: [] });
        return;
      }
      try {
        const resp = await apiClient.get(`/agents/${agentId}/delegations`);
        const data = resp.data as { delegations?: DelegationRecord[] };
        set({ delegationRecords: Array.isArray(data.delegations) ? data.delegations : [] });
      } catch {
        set({ delegationRecords: [] });
      }
    },
  };
});
