import React, { useState, useRef, useEffect, useCallback, type CSSProperties } from 'react';
import {
  Alert,
  Avatar,
  Button,
  Dropdown,
  Input,
  Modal,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
  type UploadProps,
  message as antdMessage,
} from 'antd';
import {
  SendOutlined,
  CopyOutlined,
  RobotOutlined,
  PlusOutlined,
  MessageOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  StopOutlined,
  BulbOutlined,
  DownOutlined,
  PaperClipOutlined,
  FileOutlined,
  CloseOutlined,
  CloudServerOutlined,
  InteractionOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useChatStore, formatTimestamp, type Message, type ApprovalAction, type Attachment } from '../../stores/chatStore';
import { useAgentId, agentPath } from '../../stores/agentStore';
import { useI18n } from '../../i18n';
import { apiClient } from '../../api/client';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import MarkdownView from '../../components/MarkdownView';
import { toWorkspaceRelPath } from '../../utils/helpers';
import ChatModelSelector from './components/ChatModelSelector';
import ToolCallCard from './components/ToolCallCard';
import A2uiCard from './components/A2uiCard';
import {
  DelegationBubble,
  DelegationRecordPanel,
  TodoList,
} from './components/TodoPlan';

const { TextArea } = Input;
const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = 'var(--google-chart-3)';
const MAX_CHARS = 10000;
/** Per-file size cap for chat attachments (mirrors backend 20 MiB). */
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

/** Compact human-readable file size, e.g. "12.3 KB". */
function formatFileSize(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/* ───────── Styles ───────── */
const styles: Record<string, CSSProperties> = {
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    background: 'var(--google-background)',
    borderRadius: 'var(--google-radius-lg)',
    overflow: 'hidden',
  },
  messageArea: {
    flex: 1,
    overflowY: 'auto',
    padding: 'var(--google-space-8) var(--google-space-8) var(--google-space-4)',
  },
  systemCard: {
    maxWidth: 480,
    marginLeft: 'auto',
    marginBottom: 'var(--google-space-6)',
    background: 'var(--google-muted)',
    borderRadius: 'var(--google-radius-lg)',
    padding: 'var(--google-space-4) var(--google-space-6)',
    border: '1px solid var(--google-border)',
  },
  msgRow: {
    display: 'flex',
    gap: 'var(--google-space-4)',
    marginBottom: 'var(--google-space-8)',
    alignItems: 'flex-start',
  },
  msgBody: {
    flex: 1,
    minWidth: 0,
  },
  agentName: {
    fontWeight: 600,
    fontSize: 14,
    color: 'var(--google-foreground)',
    marginBottom: 'var(--google-space-2)',
  },
  thinkingBlock: {
    marginBottom: 'var(--google-space-3)',
  },
  thinkingWrapper: {
    border: '1px solid var(--google-border)',
    borderRadius: 'var(--google-radius-lg)',
    background: 'var(--google-muted)',
    overflow: 'hidden',
  },
  thinkingHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--google-space-3)',
    width: '100%',
    padding: 'var(--google-space-3) var(--google-space-4)',
    cursor: 'pointer',
    background: 'transparent',
    border: 'none',
    textAlign: 'left' as const,
    fontFamily: 'inherit',
  },
  thinkingIconBox: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 20,
    height: 20,
    borderRadius: 'var(--google-radius-md)',
    background: 'color-mix(in srgb, var(--google-chart-2) 15%, var(--google-muted))',
    flexShrink: 0,
  },
  thinkingTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: 'var(--google-chart-2)',
    letterSpacing: 0.3,
  },
  thinkingDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: 'var(--google-chart-2)',
    animation: 'chat-dot-pulse 1.2s ease-in-out infinite',
  },
  thinkingChevron: {
    marginLeft: 'auto',
    fontSize: 11,
    color: 'var(--google-muted-foreground)',
    transition: 'transform 0.2s ease',
  },
  thinkingBody: {
    padding: 'var(--google-space-2) var(--google-space-4) var(--google-space-4)',
    borderTop: '1px solid var(--google-border)',
    fontSize: 13,
    lineHeight: 1.85,
    color: 'var(--google-foreground)',
    fontStyle: 'italic',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  msgContent: {
    fontSize: 14,
    lineHeight: 1.75,
    color: 'var(--google-foreground)',
    wordBreak: 'break-word' as const,
  },
  msgFooter: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--google-space-3)',
    marginTop: 'var(--google-space-2)',
  },
  inputSection: {
    padding: 'var(--google-space-4) var(--google-space-8) var(--google-space-6)',
    borderTop: '1px solid var(--google-border)',
    flexShrink: 0,
    background: 'var(--google-background)',
  },
  textArea: {
    resize: 'none' as const,
    border: '1px solid var(--google-input)',
    borderRadius: 'var(--google-radius-lg)',
    padding: 'var(--google-space-3) var(--google-space-4)',
    fontSize: 14,
    lineHeight: 1.6,
    boxShadow: 'none',
  },
  toolBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 'var(--google-space-3)',
  },
  toolLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--google-space-2)',
  },
  toolRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--google-space-4)',
  },
};

/* ───────── Helpers ───────── */
/* ───────── Markdown Renderer ───────── */

/**
 * Map workspace-file markdown links onto the Files page so clicking a
 * generated report (e.g. `report.md`) opens the file manager in a new
 * browser tab with that file revealed and opened.
 */
function useFileLinkResolver() {
  const agentId = useAgentId();
  return useCallback(
    (href: string) => {
      const path = toWorkspaceRelPath(href);
      return path ? `${agentPath('files', agentId)}?open=${encodeURIComponent(path)}` : null;
    },
    [agentId],
  );
}

/* ───────── Components ───────── */

function ApprovalCard({
  actions,
  onApprove,
  onEdit,
  onReject,
  disabled,
}: {
  actions: ApprovalAction[];
  onApprove: () => void;
  onEdit: (editedArgs: Record<string, unknown>) => void;
  onReject: () => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [editJson, setEditJson] = useState('');

  const startEdit = () => {
    setEditJson(JSON.stringify(actions[0]?.args ?? {}, null, 2));
    setEditing(true);
  };

  // A decision button renders only when every pending action permits it —
  // the backend applies a single decision batch to all actions at once,
  // and the API rejects decisions outside each action's policy anyway.
  const allAllow = (d: string) =>
    actions.every((a) => !a.allowed_decisions || a.allowed_decisions.includes(d));

  const submitEdit = () => {
    try {
      const parsed = JSON.parse(editJson);
      onEdit(parsed);
      setEditing(false);
    } catch {
      antdMessage.warning(t('chat.jsonFormatError'));
    }
  };

  return (
    <GoogleCard
      style={{
        marginTop: 'var(--google-space-3)',
        marginBottom: 'var(--google-space-3)',
      }}
      bodyStyle={{
        background: 'var(--google-popover)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--google-space-3)', marginBottom: 'var(--google-space-4)' }}>
        <ExclamationCircleOutlined style={{ color: 'var(--google-chart-3)', fontSize: 16 }} />
        <Text strong style={{ fontSize: 14, color: 'var(--google-foreground)' }}>
          {t('chat.approvalNeeded')}
        </Text>
      </div>

      {actions.map((action, i) => (
        <div key={i} style={{ marginBottom: 'var(--google-space-4)' }}>
          <div style={{ fontSize: 13, color: 'var(--google-foreground)', marginBottom: 'var(--google-space-2)' }}>
            <Text code style={{ fontSize: 13 }}>{action.name}</Text>
          </div>
          {action.description && (
            <div style={{ fontSize: 12, color: 'var(--google-muted-foreground)', marginBottom: 'var(--google-space-2)', whiteSpace: 'pre-wrap' }}>
              {action.description}
            </div>
          )}
          {!editing && (
            <pre
              style={{
                background: 'var(--google-muted)',
                padding: 'var(--google-space-3) var(--google-space-4)',
                borderRadius: 'var(--google-radius-md)',
                fontSize: 12,
                fontFamily: 'var(--google-font-mono)',
                margin: 0,
                maxHeight: 160,
                overflow: 'auto',
                border: '1px solid var(--google-border)',
              }}
            >
              {JSON.stringify(action.args, null, 2)}
            </pre>
          )}
        </div>
      ))}

      {editing && (
        <div style={{ marginBottom: 'var(--google-space-4)' }}>
          <TextArea
            value={editJson}
            onChange={(e) => setEditJson(e.target.value)}
            autoSize={{ minRows: 3, maxRows: 10 }}
            style={{ fontFamily: 'var(--google-font-mono)', fontSize: 12 }}
          />
        </div>
      )}

      <div style={{ display: 'flex', gap: 'var(--google-space-3)', marginTop: 'var(--google-space-3)' }}>
        {allAllow('approve') && (
          <Button
            type="primary"
            size="small"
            icon={<CheckCircleOutlined />}
            onClick={onApprove}
            disabled={disabled || editing}
            style={{ background: 'var(--google-chart-5)', borderColor: 'var(--google-chart-5)', borderRadius: 'var(--google-radius-md)' }}
          >
            {t('chat.approve')}
          </Button>
        )}
        {allAllow('edit') &&
          (editing ? (
            <>
              <Button
                size="small"
                icon={<CheckCircleOutlined />}
                onClick={submitEdit}
                disabled={disabled}
                style={{ borderRadius: 'var(--google-radius-md)' }}
              >
                {t('chat.confirmEdit')}
              </Button>
              <Button
                size="small"
                onClick={() => setEditing(false)}
                disabled={disabled}
                style={{ borderRadius: 'var(--google-radius-md)' }}
              >
                {t('common.cancel')}
              </Button>
            </>
          ) : (
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={startEdit}
              disabled={disabled}
              style={{ borderRadius: 'var(--google-radius-md)' }}
            >
              {t('chat.approveEdit')}
            </Button>
          ))}
        {allAllow('reject') && (
          <Button
            size="small"
            danger
            icon={<CloseCircleOutlined />}
            onClick={onReject}
            disabled={disabled}
            style={{ borderRadius: 'var(--google-radius-md)' }}
          >
            {t('chat.reject')}
          </Button>
        )}
      </div>
    </GoogleCard>
  );
}

function SystemCard({ message }: { message: Message }) {
  const resolveFileLink = useFileLinkResolver();
  return (
    <div style={styles.systemCard}>
      <div style={styles.msgContent}><MarkdownView text={message.content} resolveFileLink={resolveFileLink} /></div>
      <div style={{ ...styles.msgFooter, justifyContent: 'flex-end', marginTop: 'var(--google-space-3)' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {message.timestamp}
        </Text>
      </div>
    </div>
  );
}

function AssistantMessage({ message, liveThinking }: { message: Message; liveThinking?: string }) {
  const [copied, setCopied] = useState(false);
  // Thinking block: open during streaming, collapsed by default for completed/historical messages.
  const [thinkingOpen, setThinkingOpen] = useState(!!message.streaming);
  const prevStreamingRef = useRef(!!message.streaming);
  // Auto-collapse thinking when streaming ends.
  useEffect(() => {
    if (prevStreamingRef.current && !message.streaming) {
      setThinkingOpen(false);
    }
    prevStreamingRef.current = !!message.streaming;
  }, [message.streaming]);
  const submitApproval = useChatStore((s) => s.submitApproval);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const { t } = useI18n();
  const agentId = useAgentId();
  const navigate = useNavigate();
  const resolveFileLink = useFileLinkResolver();

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  // A2UI action loop: user interactions on agent-rendered surfaces are
  // fed back to the conversation as a new user message so the agent can
  // continue from the submitted data (transport-agnostic return channel).
  const handleA2uiAction = (action: {
    name: string;
    surfaceId: string;
    context: Record<string, unknown>;
  }) => {
    if (isStreaming) return;
    // Single-line compact form; UserMessage renders it as a friendly
    // receipt bubble instead of raw text (see parseA2uiEcho).
    const contextJson = JSON.stringify(action.context ?? {});
    const text = `[A2UI ${t('chat.a2uiActionLabel')}] event=${action.name} surface=${action.surfaceId} ${contextJson}`;
    void sendMessage(text);
  };

  const thinking = message.thinking || liveThinking;
  // Live thinking arrives via SSE while the turn streams; historical
  // thinking is a persisted snapshot of the finished turn.
  const isLiveThinking = !message.thinking && !!liveThinking;
  const isEmpty = !message.content && message.streaming;
  // A decided approval shows the decision badge instead of an actionable
  // card (history may carry both fields on the same message).
  const hasApproval = !!message.approvalRequest?.actions?.length && !message.approval;
  const decision = message.approval?.decision;
  const decisionIcon = decision === 'approve' ? '✅' : decision === 'reject' ? '❌' : '✏️';
  const decisionLabel =
    decision === 'approve'
      ? t('chat.approvalApproved')
      : decision === 'reject'
        ? t('chat.approvalRejected')
        : decision === 'edit'
          ? t('chat.approvalEdited')
          : '';

  return (
    <div style={styles.msgRow}>
      <Avatar
        size={36}
        style={{ background: ORANGE, flexShrink: 0 }}
        icon={<RobotOutlined />}
      />
      <div style={styles.msgBody}>
        <div style={styles.agentName}>{agentId}</div>

        {/* Thinking — collapsible reasoning block */}
        {thinking && (
          <div style={styles.thinkingBlock}>
            <div style={styles.thinkingWrapper}>
              <button
                type="button"
                style={styles.thinkingHeader}
                onClick={() => setThinkingOpen((v) => !v)}
                aria-expanded={thinkingOpen}
              >
                <span style={styles.thinkingIconBox}>
                  <BulbOutlined style={{ color: 'var(--google-chart-2)', fontSize: 11 }} />
                </span>
                <span style={styles.thinkingTitle}>{t('chat.thinking')}</span>
                {isLiveThinking && <span style={styles.thinkingDot} />}
                <DownOutlined
                  style={{ ...styles.thinkingChevron, transform: thinkingOpen ? 'rotate(180deg)' : undefined }}
                />
              </button>
              {thinkingOpen && (
                <div style={styles.thinkingBody}>
                  {thinking}
                  {isLiveThinking && (
                    <span style={{ display: 'inline-block', width: 6, height: 13, background: 'var(--google-chart-2)', marginLeft: 3, verticalAlign: 'text-bottom', animation: 'chat-cursor-blink 1s steps(1) infinite' }} />
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Delegation bubble — collapsible; body has an explicit "open session" button */}
        {message.delegations?.map((d, i) => (
          <DelegationBubble
            key={`del-${i}`}
            delegation={d}
            running={message.streaming}
            onOpenSession={() => navigate(`/agents/${d.subagent}/chat`)}
          />
        ))}

        {/* Tool calls — collapsible badge, click to see JSON args */}
        {message.toolCalls?.map((tc, i) => (
          <ToolCallCard key={`tc-${i}`} call={tc} />
        ))}

        {/* Persisted task plan (restored from history) */}
        {message.todos && message.todos.length > 0 && (
          <GoogleCard style={{ marginTop: 'var(--google-space-2)', marginBottom: 'var(--google-space-6)', padding: 'var(--google-space-3) var(--google-space-4)' }} bodyStyle={{ padding: 0, background: 'transparent' }}>
            <TodoList todos={message.todos} />
          </GoogleCard>
        )}

        {/* A2UI interactive surfaces projected by send_a2ui */}
        {message.a2uiSurfaces?.map((s, i) => (
          <A2uiCard
            key={`a2ui-${s.surface_id}-${i}`}
            payload={s}
            disabled={isStreaming}
            onAction={handleA2uiAction}
          />
        ))}

        {/* Content */}
        {isEmpty ? (
          <div style={{ ...styles.msgContent, color: 'var(--google-muted-foreground)', display: 'flex', alignItems: 'center', gap: 'var(--google-space-3)' }}>
            <Spin size="small" />
            {t('chat.agentThinking')}
          </div>
        ) : (
          <div style={styles.msgContent}>
            <MarkdownView text={message.content} resolveFileLink={resolveFileLink} />
            {message.streaming && (
              <span style={{ display: 'inline-block', width: 7, height: 14, background: ORANGE, marginLeft: 3, verticalAlign: 'text-bottom', animation: 'chat-cursor-blink 1s steps(1) infinite' }} />
            )}
          </div>
        )}

        {/* HITL Approval Card (Task 1.5 + 1.6) */}
        {hasApproval && (
          <ApprovalCard
            actions={message.approvalRequest!.actions}
            disabled={isStreaming}
            onApprove={() => submitApproval('approve')}
            onEdit={(editedArgs) => submitApproval('edit', editedArgs)}
            onReject={() => submitApproval('reject', undefined, '')}
          />
        )}
        {/* Approval decision record (restored from history) */}
        {!hasApproval && decision && (
          <div style={{ marginTop: 'var(--google-space-2)', fontSize: 13, color: 'var(--google-foreground)' }}>
            {decisionIcon} {decisionLabel}
            {message.approval?.tool_name ? ` · ${message.approval.tool_name}` : ''}
          </div>
        )}
        {message.error && (
          <Text type="danger" style={{ fontSize: 12, display: 'block', marginTop: 'var(--google-space-2)' }}>
            {'\u26A0\uFE0F'} {t('chat.sendFailed')}
          </Text>
        )}

        {/* Footer */}
        <div style={styles.msgFooter}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {message.timestamp}
            {message.tokenUsage && (message.tokenUsage.input_tokens > 0 || message.tokenUsage.output_tokens > 0) && (
              <span style={{ marginLeft: 12, color: 'var(--google-muted-foreground)' }}>
                {'\u{1F4CA}'} {t('chat.tokenUsage', { input: message.tokenUsage.input_tokens, output: message.tokenUsage.output_tokens })}
              </span>
            )}
          </Text>
          <Tooltip title={copied ? t('chat.copied') : t('chat.copy')}>
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined style={{ fontSize: 13 }} />}
              onClick={handleCopy}
              style={{ color: copied ? 'var(--google-primary)' : 'var(--google-muted-foreground)', padding: '0 4px' }}
            />
          </Tooltip>
        </div>
      </div>
    </div>
  );
}

/* Parsed `[A2UI …] event=… surface=… {json}` action-echo user message. */
interface A2uiEcho {
  label: string;
  event: string;
  surfaceId: string;
  context: Record<string, unknown> | null;
  raw: string;
}

/**
 * Recognizes the transport text produced by handleA2uiAction (both the
 * legacy multi-line and the current single-line form) so history renders
 * as a friendly receipt instead of raw debug text.
 */
function parseA2uiEcho(content: string): A2uiEcho | null {
  const m = /^\[A2UI ([^\]]+)\] event=(\S+)\s+surface=(\S+)\s*([\s\S]*)$/.exec(content.trim());
  if (!m) return null;
  let context: Record<string, unknown> | null = null;
  try {
    const parsed: unknown = JSON.parse(m[4]);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      context = parsed as Record<string, unknown>;
    }
  } catch {
    context = null;
  }
  return { label: m[1], event: m[2], surfaceId: m[3], context, raw: m[4] };
}

function formatEchoValue(v: unknown, emptyLabel: string): string {
  if (Array.isArray(v)) return v.length ? v.map((x) => String(x)).join(', ') : emptyLabel;
  if (v === null || v === undefined || v === '') return emptyLabel;
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/** Friendly receipt bubble replacing the raw `[A2UI …]` echo text. */
function A2uiEchoBubble({ echo }: { echo: A2uiEcho }) {
  const { t } = useI18n();
  const entries = echo.context ? Object.entries(echo.context) : [];
  const empty = t('chat.a2uiEchoEmpty');
  return (
    <div
      style={{
        background: 'color-mix(in srgb, var(--google-primary) 8%, var(--google-card))',
        border: '1px solid color-mix(in srgb, var(--google-primary) 35%, var(--google-border))',
        color: 'var(--google-foreground)',
        padding: '8px 14px',
        borderRadius: 'var(--google-radius-lg) var(--google-radius-lg) 2px var(--google-radius-lg)',
        maxWidth: '70%',
        fontSize: 13,
        lineHeight: 1.6,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12,
          color: 'var(--google-muted-foreground)',
          marginBottom: entries.length || echo.context === null ? 6 : 0,
        }}
      >
        <InteractionOutlined style={{ color: 'var(--google-primary)', fontSize: 12 }} />
        <span>
          {echo.label} · <Text code style={{ fontSize: 11.5 }}>{echo.event}</Text>
        </span>
      </div>
      {echo.context === null ? (
        <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--google-muted-foreground)' }}>{echo.raw}</div>
      ) : (
        entries.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {entries.map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <span style={{ color: 'var(--google-muted-foreground)', fontSize: 12, flexShrink: 0 }}>{k}</span>
                <span style={{ fontWeight: 500, wordBreak: 'break-word' }}>{formatEchoValue(v, empty)}</span>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}

function UserMessage({ message }: { message: Message }) {
  const resolveFileLink = useFileLinkResolver();
  const echo = parseA2uiEcho(message.content);
  return (
    <div style={{ ...styles.msgRow, flexDirection: 'row-reverse' }}>
      <Avatar
        size={36}
        style={{ background: 'var(--google-primary)', flexShrink: 0, fontSize: 14 }}
      >
        U
      </Avatar>
      <div style={{ ...styles.msgBody, display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
        {echo ? (
          <A2uiEchoBubble echo={echo} />
        ) : (
        <div
          style={{
            background: 'var(--google-primary)',
            color: 'var(--google-primary-foreground)',
            padding: 'var(--google-space-3) var(--google-space-4)',
            borderRadius: 'var(--google-radius-lg) var(--google-radius-lg) 2px var(--google-radius-lg)',
            fontSize: 14,
            lineHeight: 1.7,
            maxWidth: '70%',
            wordBreak: 'break-word',
          }}
        >
          {message.content ? <MarkdownView text={message.content} resolveFileLink={resolveFileLink} /> : null}
          {/* Attachment chips — files uploaded alongside this message */}
          {message.attachments && message.attachments.length > 0 && (
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 6,
                marginTop: message.content ? 'var(--google-space-2)' : 0,
              }}
            >
              {message.attachments.map((a, i) => (
                <Tooltip key={`${a.path}-${i}`} title={a.path}>
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 4,
                      background: 'rgba(255, 255, 255, 0.16)',
                      padding: '2px 8px',
                      borderRadius: 'var(--google-radius-sm)',
                      fontSize: 12,
                      maxWidth: 220,
                    }}
                  >
                    <PaperClipOutlined style={{ fontSize: 11, flexShrink: 0 }} />
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {a.name}
                    </span>
                    <span style={{ opacity: 0.75, flexShrink: 0 }}>({formatFileSize(a.size)})</span>
                  </span>
                </Tooltip>
              ))}
            </div>
          )}
        </div>
        )}
        <div style={{ ...styles.msgFooter, justifyContent: 'flex-end' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {message.timestamp}
          </Text>
        </div>
      </div>
    </div>
  );
}

/* ───────── Main Page ───────── */
export default function ChatPage() {
  const agentId = useAgentId();
  const messages = useChatStore((s) => s.messages);
  const sessions = useChatStore((s) => s.sessions);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const error = useChatStore((s) => s.error);
  const thinkingText = useChatStore((s) => s.thinkingText);
  const streamingMsgId = useChatStore((s) => s.streamingMsgId);
  const delegationRecords = useChatStore((s) => s.delegationRecords);
  const loadSessions = useChatStore((s) => s.loadSessions);
  const loadDelegations = useChatStore((s) => s.loadDelegations);
  const selectSession = useChatStore((s) => s.selectSession);
  const newSession = useChatStore((s) => s.newSession);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const stopStreaming = useChatStore((s) => s.stopStreaming);
  const clearError = useChatStore((s) => s.clearError);
  const { t } = useI18n();

  const [inputValue, setInputValue] = useState('');
  /** Files uploaded but not yet sent — attached to the next message. */
  const [pendingFiles, setPendingFiles] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<any>(null);

  // Session rename state
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  const [renameSessionId, setRenameSessionId] = useState<string>('');
  const [renameTitle, setRenameTitle] = useState('');
  const renameSession = useChatStore((s) => s.renameSession);

  // Sandbox status indicator
  const [sandboxStatus, setSandboxStatus] = useState<{ status: string; strategy: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const fetchSandbox = async () => {
      try {
        const resp = await apiClient.get<{ status: string; strategy: string }>(
          `/sandbox/sessions/${agentId}`,
        );
        if (!cancelled) setSandboxStatus(resp.data);
      } catch {
        if (!cancelled) setSandboxStatus(null);
      }
    };
    void fetchSandbox();
    const timer = window.setInterval(() => void fetchSandbox(), 15000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [agentId]);
  const scrollRef = useRef<HTMLDivElement>(null);
  /** Track whether the user has scrolled up to avoid forced auto-scroll. */
  const userScrolledUpRef = useRef(false);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  // Load delegation records whenever the current agent changes — a subagent
  // sees who delegated what to it right in its own chat page.
  useEffect(() => {
    void loadDelegations();
  }, [agentId, loadDelegations]);

  // Smart auto-scroll: only scroll to bottom if user is near the bottom.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // If user scrolled up more than 120px from bottom, don't force scroll.
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (isNearBottom || !userScrolledUpRef.current) {
      userScrolledUpRef.current = false;
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }
  }, [messages, thinkingText]);

  // Detect when user manually scrolls up.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      if (!isNearBottom) userScrolledUpRef.current = true;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Pending attachments belong to the current composition only — drop them
  // when switching sessions so they never leak into another conversation.
  useEffect(() => {
    setPendingFiles([]);
  }, [currentSessionId]);

  // Auto-focus the input when streaming ends so the user can keep typing.
  useEffect(() => {
    if (!isStreaming) {
      // Delay slightly to let the DOM settle after streaming finishes.
      const timer = setTimeout(() => inputRef.current?.focus(), 100);
      return () => clearTimeout(timer);
    }
  }, [isStreaming]);

  // Reload sessions after streaming ends so AI-generated titles appear.
  const prevStreamingRef = useRef(isStreaming);
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      // AI title generation takes a few seconds — wait then refresh.
      const timer = setTimeout(() => { void loadSessions(); }, 5000);
      return () => clearTimeout(timer);
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, loadSessions]);

  /* ── Chat attachment upload (mirrors QwenPaw /console/upload flow) ── */
  const uploadProps: UploadProps = {
    showUploadList: false,
    multiple: true,
    disabled: isStreaming,
    customRequest: async ({ file, onSuccess, onError }) => {
      const f = file as File;
      if (f.size > MAX_UPLOAD_BYTES) {
        antdMessage.error(t('chat.attachTooLarge'));
        onError?.(new Error('File too large'));
        return;
      }
      const form = new FormData();
      form.append('file', f);
      setUploading(true);
      try {
        const resp = await apiClient.post('/chat/upload', form, {
          params: { agent_id: agentId },
        });
        const data = resp.data as { name: string; path: string; size: number };
        setPendingFiles((prev) => [...prev, data]);
        onSuccess?.({}, new XMLHttpRequest());
      } catch (err) {
        antdMessage.error(t('chat.attachUploadFailed'));
        onError?.(err as Error);
      } finally {
        setUploading(false);
      }
    },
  };

  const handleSend = () => {
    const content = inputValue.trim();
    if ((!content && pendingFiles.length === 0) || isStreaming || uploading) return;
    const files = pendingFiles;
    setInputValue('');
    setPendingFiles([]);
    sendMessage(content, files.length ? files : undefined).then(() => {
      const latestError = useChatStore.getState().error;
      if (latestError) {
        antdMessage.error(latestError);
        // 保留顶部 Alert 错误横幅（含具体原因），由用户手动关闭。
      }
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleRenameClick = (sessionId: string, currentTitle: string) => {
    setRenameSessionId(sessionId);
    setRenameTitle(currentTitle || '');
    setRenameModalOpen(true);
  };

  const handleRenameConfirm = async () => {
    try {
      await renameSession(renameSessionId, renameTitle);
      antdMessage.success(t('chat.sessionRenamed') || '会话已重命名');
      setRenameModalOpen(false);
    } catch {
      // Error already handled in store
    }
  };

  const currentSession = sessions.find((s) => s.session_id === currentSessionId);
  const sessionMenuItems = [
    ...sessions.map((s) => ({
      key: s.session_id,
      label: (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minWidth: 280 }}>
          <span style={{ fontSize: 13, flex: 1 }}>
            <MessageOutlined style={{ marginRight: 6, color: s.session_id === currentSessionId ? 'var(--google-primary)' : 'var(--google-muted-foreground)' }} />
            {s.title || t('chat.session', { id: s.session_id.slice(0, 8) })}
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              {t('chat.sessionCount', { count: s.message_count, time: formatTimestamp(s.updated_at) })}
            </Text>
          </span>
          <Button
            type="text"
            size="small"
            icon={<EditOutlined style={{ fontSize: 12 }} />}
            onClick={(e) => {
              e.stopPropagation();
              handleRenameClick(s.session_id, s.title || '');
            }}
            style={{ color: 'var(--google-muted-foreground)' }}
          />
        </div>
      ),
      onClick: () => void selectSession(s.session_id),
    })),
    ...(sessions.length === 0
      ? [{ key: 'empty', label: <Text type="secondary" style={{ fontSize: 13 }}>{t('chat.noSessions')}</Text>, disabled: true }]
      : []),
  ];

  const sandboxTag = sandboxStatus ? (
    <Tooltip title={`Sandbox: ${sandboxStatus.status} (${sandboxStatus.strategy})`}>
      <Tag
        color={sandboxStatus.status === 'running' ? 'green' : sandboxStatus.status === 'paused' ? 'gold' : 'default'}
        icon={<CloudServerOutlined />}
        style={{ marginInlineEnd: 0, fontSize: 11 }}
      >
        {sandboxStatus.status === 'running' ? 'Sandbox' : sandboxStatus.status}
      </Tag>
    </Tooltip>
  ) : null;

  const headerExtra = (
    <Space>
      {sandboxTag}
      <ChatModelSelector />
      <Dropdown menu={{ items: sessionMenuItems }} trigger={['click']} placement="bottomRight" disabled={isStreaming}>
        <Button size="small" icon={<MessageOutlined />}
          style={{ borderRadius: 'var(--google-radius-md)', fontSize: 13 }}>
          {currentSession ? t('chat.session', { id: currentSession.session_id.slice(0, 8) }) : t('chat.selectSession')}
        </Button>
      </Dropdown>
      <Tooltip title={t('chat.newSession')}>
        <Button size="small" icon={<PlusOutlined />}
          onClick={newSession}
          disabled={isStreaming}
          style={{ borderRadius: 'var(--google-radius-md)', color: 'var(--google-primary)', borderColor: 'var(--google-primary)' }} />
      </Tooltip>
    </Space>
  );

  return (
    <div style={styles.wrapper}>
      {/* Header */}
      <GooglePageHeader
        icon={<RobotOutlined />}
        title={currentSession?.title || t('chat.greeting')}
        extra={headerExtra}
      />

      {/* Messages */}
      <div ref={scrollRef} style={styles.messageArea}>
        {delegationRecords.length > 0 && <DelegationRecordPanel records={delegationRecords} />}
        {messages.length === 0 && !isStreaming && (
          <div style={{ textAlign: 'center', padding: 'var(--google-space-16) 0', color: 'var(--google-muted-foreground)', fontSize: 13 }}>
            <RobotOutlined style={{ fontSize: 32, color: 'var(--google-border)', display: 'block', marginBottom: 'var(--google-space-4)' }} />
            {t('chat.sendMessage')}
          </div>
        )}
        {messages.map((msg) => {
          if (msg.role === 'system') return <SystemCard key={msg.id} message={msg} />;
          if (msg.role === 'assistant')
            return (
              <AssistantMessage
                key={msg.id}
                message={msg}
                liveThinking={msg.id === streamingMsgId ? thinkingText : undefined}
              />
            );
          return <UserMessage key={msg.id} message={msg} />;
        })}
        {error && (
          <Alert
            type="error"
            showIcon
            closable
            message={error}
            onClose={clearError}
            style={{ marginBottom: 'var(--google-space-4)', borderRadius: 'var(--google-radius-md)' }}
          />
        )}
      </div>

      {/* Input */}
      <div style={styles.inputSection}>
        {/* Pending attachments — uploaded, attached to the next message */}
        {pendingFiles.length > 0 && (
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 8,
              marginBottom: 'var(--google-space-3)',
            }}
          >
            {pendingFiles.map((f, i) => (
              <span
                key={`${f.path}-${i}`}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '3px 8px',
                  background: 'var(--google-muted)',
                  border: '1px solid var(--google-border)',
                  borderRadius: 'var(--google-radius-md)',
                  fontSize: 12,
                  color: 'var(--google-foreground)',
                  maxWidth: 260,
                }}
              >
                <FileOutlined style={{ fontSize: 12, color: 'var(--google-muted-foreground)', flexShrink: 0 }} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {f.name}
                </span>
                <span style={{ color: 'var(--google-muted-foreground)', flexShrink: 0 }}>
                  {formatFileSize(f.size)}
                </span>
                <CloseOutlined
                  style={{ fontSize: 10, color: 'var(--google-muted-foreground)', cursor: 'pointer', flexShrink: 0 }}
                  onClick={() => setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))}
                />
              </span>
            ))}
          </div>
        )}
        <TextArea
          ref={inputRef}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isStreaming ? t('chat.agentReplying') : t('chat.placeholder')}
          autoSize={{ minRows: 1, maxRows: 6 }}
          maxLength={MAX_CHARS}
          disabled={isStreaming}
          autoFocus
          style={styles.textArea}
          classNames={{ textarea: 'chat-textarea' }}
        />
        <div style={styles.toolBar}>
          <div style={styles.toolLeft}>
            <Upload {...uploadProps}>
              <Tooltip title={t('chat.attachFile')}>
                <Button
                  type="text"
                  size="small"
                  icon={<PaperClipOutlined style={{ fontSize: 14 }} />}
                  loading={uploading}
                  disabled={isStreaming}
                  style={{ color: 'var(--google-muted-foreground)', minWidth: 32, height: 32 }}
                />
              </Tooltip>
            </Upload>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {inputValue.length}/{MAX_CHARS}
            </Text>
            <Tooltip title={isStreaming ? t('chat.stopTip') : undefined}>
              <Button
                type="primary"
                size="small"
                danger={isStreaming}
                icon={
                  isStreaming ? (
                    <StopOutlined style={{ fontSize: 15 }} />
                  ) : (
                    <SendOutlined style={{ fontSize: 14, rotate: '-45deg' } as CSSProperties} />
                  )
                }
                onClick={isStreaming ? stopStreaming : handleSend}
                disabled={!isStreaming && (uploading || (!inputValue.trim() && pendingFiles.length === 0))}
                style={{
                background: isStreaming ? undefined : (inputValue.trim() || pendingFiles.length > 0) ? 'var(--google-primary)' : 'var(--google-input)',
                borderColor: isStreaming ? undefined : (inputValue.trim() || pendingFiles.length > 0) ? 'var(--google-primary)' : 'var(--google-input)',
                borderRadius: 'var(--google-radius-md)',
                minWidth: 32,
                height: 32,
              }}
              />
            </Tooltip>
          </div>
        </div>
      </div>

      {/* Session Rename Modal */}
      <Modal
        title={t('chat.renameSession') || '重命名会话'}
        open={renameModalOpen}
        onOk={handleRenameConfirm}
        onCancel={() => setRenameModalOpen(false)}
        okText={t('common.confirm') || '确认'}
        cancelText={t('common.cancel') || '取消'}
      >
        <Input
          value={renameTitle}
          onChange={(e) => setRenameTitle(e.target.value)}
          placeholder={t('chat.sessionNamePlaceholder') || '输入会话名称'}
          maxLength={50}
          onPressEnter={handleRenameConfirm}
          autoFocus
        />
      </Modal>

      <style>{`
        @keyframes chat-cursor-blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
        @keyframes chat-dot-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.45; transform: scale(0.82); } }
      `}</style>
    </div>
  );
}
