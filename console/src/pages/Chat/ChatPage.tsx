import React, { useState, useRef, useEffect, type CSSProperties } from 'react';
import ReactMarkdown from 'react-markdown';
import {
  Alert,
  Avatar,
  Button,
  Dropdown,
  Input,
  Spin,
  Tooltip,
  Typography,
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
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useChatStore, formatTimestamp, type Message, type ApprovalAction } from '../../stores/chatStore';
import { useAgentId } from '../../stores/agentStore';
import { useI18n } from '../../i18n';
import ChatModelSelector from './components/ChatModelSelector';
import ToolCallCard from './components/ToolCallCard';
import {
  DelegationBubble,
  DelegationRecordPanel,
  TodoList,
  TodoPanel,
} from './components/TodoPlan';

const { TextArea } = Input;
const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';
const MAX_CHARS = 10000;

/* ───────── Styles ───────── */
const styles: Record<string, CSSProperties> = {
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    margin: '-24px',
    background: '#fff',
    borderRadius: 8,
    overflow: 'hidden',
  },
  header: {
    padding: '14px 24px',
    borderBottom: '1px solid #f0f0f0',
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    flexShrink: 0,
  },
  headerTitle: {
    margin: 0,
    fontSize: 16,
    fontWeight: 600,
    color: '#1a1a1a',
  },
  messageArea: {
    flex: 1,
    overflowY: 'auto',
    padding: '24px 24px 8px',
  },
  systemCard: {
    maxWidth: 480,
    marginLeft: 'auto',
    marginBottom: 20,
    background: '#f7f7f8',
    borderRadius: 12,
    padding: '16px 20px',
    border: '1px solid #eee',
  },
  msgRow: {
    display: 'flex',
    gap: 12,
    marginBottom: 24,
    alignItems: 'flex-start',
  },
  msgBody: {
    flex: 1,
    minWidth: 0,
  },
  agentName: {
    fontWeight: 600,
    fontSize: 14,
    color: '#1a1a1a',
    marginBottom: 6,
  },
  thinkingBlock: {
    marginBottom: 8,
  },
  thinkingWrapper: {
    border: '1px solid #e9e4fa',
    borderRadius: 10,
    background: 'linear-gradient(135deg, #f7f5ff 0%, #fbfbfe 100%)',
    overflow: 'hidden',
  },
  thinkingHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    width: '100%',
    padding: '8px 12px',
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
    borderRadius: 6,
    background: '#ece6fd',
    flexShrink: 0,
  },
  thinkingTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#6d5ae0',
    letterSpacing: 0.3,
  },
  thinkingDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: '#7c5cf0',
    animation: 'chat-dot-pulse 1.2s ease-in-out infinite',
  },
  thinkingChevron: {
    marginLeft: 'auto',
    fontSize: 11,
    color: '#b3a9e0',
    transition: 'transform 0.2s ease',
  },
  thinkingBody: {
    padding: '4px 14px 12px',
    borderTop: '1px dashed #e6e0f8',
    fontSize: 13,
    lineHeight: 1.85,
    color: '#5b5580',
    fontStyle: 'italic',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  msgContent: {
    fontSize: 14,
    lineHeight: 1.75,
    color: '#333',
    wordBreak: 'break-word' as const,
  },
  msgFooter: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginTop: 6,
  },
  inputSection: {
    padding: '12px 24px 16px',
    borderTop: '1px solid #f0f0f0',
    flexShrink: 0,
    background: '#fff',
  },
  textArea: {
    resize: 'none' as const,
    border: '1px solid #e0e0e0',
    borderRadius: 10,
    padding: '10px 14px',
    fontSize: 14,
    lineHeight: 1.6,
    boxShadow: 'none',
  },
  toolBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 8,
  },
  toolLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  toolRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
};

/* ───────── Helpers ───────── */
/* ───────── Markdown Renderer ───────── */

function renderMarkdown(text: string) {
  return (
    <ReactMarkdown
      components={{
        // Style headings
        h1: ({ children }) => <div style={{ fontSize: 18, fontWeight: 700, margin: '12px 0 6px', color: '#1a1a1a' }}>{children}</div>,
        h2: ({ children }) => <div style={{ fontSize: 16, fontWeight: 700, margin: '8px 0 4px', color: '#1a1a1a' }}>{children}</div>,
        h3: ({ children }) => <div style={{ fontSize: 14, fontWeight: 600, margin: '6px 0 3px', color: '#1a1a1a' }}>{children}</div>,
        // Style paragraphs
        p: ({ children }) => <div style={{ margin: '2px 0' }}>{children}</div>,
        // Style lists
        ul: ({ children }) => <div style={{ paddingLeft: 16, margin: '2px 0' }}>{children}</div>,
        ol: ({ children }) => <div style={{ paddingLeft: 16, margin: '2px 0' }}>{children}</div>,
        li: ({ children }) => <div style={{ margin: '1px 0' }}>{'\u2022 '}{children}</div>,
        // Style inline code
        code: ({ children, className }) => {
          const isBlock = className?.includes('language-');
          if (isBlock) {
            return (
              <pre style={{ background: '#f6f8fa', padding: '12px 16px', borderRadius: 8, overflow: 'auto', fontSize: 13, fontFamily: 'Menlo, Consolas, monospace', margin: '8px 0' }}>
                <code>{children}</code>
              </pre>
            );
          }
          return (
            <code style={{ background: '#f3f3f5', padding: '1px 6px', borderRadius: 4, fontSize: 13, fontFamily: 'Menlo, Consolas, monospace' }}>
              {children}
            </code>
          );
        },
        // Style bold
        strong: ({ children }) => <strong>{children}</strong>,
        // Style links
        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: '#1677ff' }}>{children}</a>,
        // Style blockquotes
        blockquote: ({ children }) => <div style={{ borderLeft: '3px solid #ddd', paddingLeft: 12, margin: '6px 0', color: '#666' }}>{children}</div>,
      }}
    >
      {text}
    </ReactMarkdown>
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
    <div
      style={{
        background: '#fffbe6',
        border: '1px solid #ffe58f',
        borderRadius: 12,
        padding: '14px 18px',
        marginTop: 8,
        marginBottom: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <ExclamationCircleOutlined style={{ color: '#faad14', fontSize: 16 }} />
        <Text strong style={{ fontSize: 14, color: '#ad6800' }}>
          {t('chat.approvalNeeded')}
        </Text>
      </div>

      {actions.map((action, i) => (
        <div key={i} style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 13, color: '#555', marginBottom: 4 }}>
            <Text code style={{ fontSize: 13 }}>{action.name}</Text>
          </div>
          {action.description && (
            <div style={{ fontSize: 12, color: '#888', marginBottom: 4, whiteSpace: 'pre-wrap' }}>
              {action.description}
            </div>
          )}
          {!editing && (
            <pre
              style={{
                background: '#fff7e6',
                padding: '8px 12px',
                borderRadius: 6,
                fontSize: 12,
                fontFamily: 'Menlo, Consolas, monospace',
                margin: 0,
                maxHeight: 160,
                overflow: 'auto',
                border: '1px solid #ffe58f',
              }}
            >
              {JSON.stringify(action.args, null, 2)}
            </pre>
          )}
        </div>
      ))}

      {editing && (
        <div style={{ marginBottom: 10 }}>
          <TextArea
            value={editJson}
            onChange={(e) => setEditJson(e.target.value)}
            autoSize={{ minRows: 3, maxRows: 10 }}
            style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 12 }}
          />
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
        {allAllow('approve') && (
          <Button
            type="primary"
            size="small"
            icon={<CheckCircleOutlined />}
            onClick={onApprove}
            disabled={disabled || editing}
            style={{ background: '#52c41a', borderColor: '#52c41a', borderRadius: 6 }}
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
                style={{ borderRadius: 6 }}
              >
                {t('chat.confirmEdit')}
              </Button>
              <Button
                size="small"
                onClick={() => setEditing(false)}
                disabled={disabled}
                style={{ borderRadius: 6 }}
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
              style={{ borderRadius: 6 }}
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
            style={{ borderRadius: 6 }}
          >
            {t('chat.reject')}
          </Button>
        )}
      </div>
    </div>
  );
}

function SystemCard({ message }: { message: Message }) {
  return (
    <div style={styles.systemCard}>
      <div style={styles.msgContent}>{renderMarkdown(message.content)}</div>
      <div style={{ ...styles.msgFooter, justifyContent: 'flex-end', marginTop: 10 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {message.timestamp}
        </Text>
      </div>
    </div>
  );
}

function AssistantMessage({ message, liveThinking }: { message: Message; liveThinking?: string }) {
  const [copied, setCopied] = useState(false);
  const [thinkingOpen, setThinkingOpen] = useState(true);
  const submitApproval = useChatStore((s) => s.submitApproval);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const { t } = useI18n();
  const agentId = useAgentId();
  const navigate = useNavigate();

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
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
                  <BulbOutlined style={{ color: '#7c5cf0', fontSize: 11 }} />
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
                    <span style={{ display: 'inline-block', width: 6, height: 13, background: '#7c5cf0', marginLeft: 3, verticalAlign: 'text-bottom', animation: 'chat-cursor-blink 1s steps(1) infinite' }} />
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Delegation bubble — clickable to navigate to subagent */}
        {message.delegations?.map((d, i) => (
          <DelegationBubble
            key={`del-${i}`}
            delegation={d}
            running={message.streaming}
            onClick={() => navigate(`/chat/${d.subagent}`)}
          />
        ))}

        {/* Tool calls — card with collapsible highlighted JSON args */}
        {message.toolCalls?.map((tc, i) => (
          <ToolCallCard key={`tc-${i}`} call={tc} />
        ))}

        {/* Persisted task plan (restored from history) */}
        {message.todos && message.todos.length > 0 && (
          <div style={{ border: '1px solid #f0f0f0', borderRadius: 12, background: '#fff', padding: '10px 14px', marginTop: 4, marginBottom: 10 }}>
            <TodoList todos={message.todos} />
          </div>
        )}

        {/* Content */}
        {isEmpty ? (
          <div style={{ ...styles.msgContent, color: '#999', display: 'flex', alignItems: 'center', gap: 8 }}>
            <Spin size="small" />
            {t('chat.agentThinking')}
          </div>
        ) : (
          <div style={styles.msgContent}>
            {renderMarkdown(message.content)}
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
          <div style={{ marginTop: 6, fontSize: 13, color: '#555' }}>
            {decisionIcon} {decisionLabel}
            {message.approval?.tool_name ? ` · ${message.approval.tool_name}` : ''}
          </div>
        )}
        {message.error && (
          <Text type="danger" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
            {'\u26A0\uFE0F'} {t('chat.sendFailed')}
          </Text>
        )}

        {/* Footer */}
        <div style={styles.msgFooter}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {message.timestamp}
          </Text>
          <Tooltip title={copied ? t('chat.copied') : t('chat.copy')}>
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined style={{ fontSize: 13 }} />}
              onClick={handleCopy}
              style={{ color: copied ? ORANGE : '#bbb', padding: '0 4px' }}
            />
          </Tooltip>
        </div>
      </div>
    </div>
  );
}

function UserMessage({ message }: { message: Message }) {
  return (
    <div style={{ ...styles.msgRow, flexDirection: 'row-reverse' }}>
      <Avatar
        size={36}
        style={{ background: '#1677ff', flexShrink: 0, fontSize: 14 }}
      >
        U
      </Avatar>
      <div style={{ ...styles.msgBody, display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
        <div
          style={{
            background: '#1677ff',
            color: '#fff',
            padding: '10px 16px',
            borderRadius: '12px 12px 2px 12px',
            fontSize: 14,
            lineHeight: 1.7,
            maxWidth: '70%',
            wordBreak: 'break-word',
          }}
        >
          {renderMarkdown(message.content)}
        </div>
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
  const todos = useChatStore((s) => s.todos);
  const tokenUsage = useChatStore((s) => s.tokenUsage);
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

  const handleSend = () => {
    const content = inputValue.trim();
    if (!content || isStreaming) return;
    setInputValue('');
    sendMessage(content).then(() => {
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

  const currentSession = sessions.find((s) => s.session_id === currentSessionId);
  const sessionMenuItems = [
    ...sessions.map((s) => ({
      key: s.session_id,
      label: (
        <span style={{ fontSize: 13 }}>
          <MessageOutlined style={{ marginRight: 6, color: s.session_id === currentSessionId ? ORANGE : '#999' }} />
          {s.session_id === currentSessionId ? t('chat.currentSession') : t('chat.session', { id: s.session_id.slice(0, 8) })}
          <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
            {t('chat.sessionCount', { count: s.message_count, time: formatTimestamp(s.updated_at) })}
          </Text>
        </span>
      ),
      onClick: () => void selectSession(s.session_id),
    })),
    ...(sessions.length === 0
      ? [{ key: 'empty', label: <Text type="secondary" style={{ fontSize: 13 }}>{t('chat.noSessions')}</Text>, disabled: true }]
      : []),
  ];

  return (
    <div style={styles.wrapper}>
      {/* Header */}
      <div style={styles.header}>
        <RobotOutlined style={{ fontSize: 18, color: ORANGE }} />
        <h2 style={styles.headerTitle}>{t('chat.greeting')}</h2>
        <ChatModelSelector />
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <Dropdown menu={{ items: sessionMenuItems }} trigger={['click']} placement="bottomRight" disabled={isStreaming}>
            <Button size="small" icon={<MessageOutlined />}
              style={{ borderRadius: 8, fontSize: 13 }}>
              {currentSession ? t('chat.session', { id: currentSession.session_id.slice(0, 8) }) : t('chat.selectSession')}
            </Button>
          </Dropdown>
          <Tooltip title={t('chat.newSession')}>
            <Button size="small" icon={<PlusOutlined />}
              onClick={newSession}
              disabled={isStreaming}
              style={{ borderRadius: 8, color: ORANGE, borderColor: '#ffd8b3' }} />
          </Tooltip>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} style={styles.messageArea}>
        {todos.length > 0 && <TodoPanel todos={todos} />}
        {delegationRecords.length > 0 && <DelegationRecordPanel records={delegationRecords} />}
        {messages.length === 0 && !isStreaming && (
          <div style={{ textAlign: 'center', padding: '48px 0', color: '#bbb', fontSize: 13 }}>
            <RobotOutlined style={{ fontSize: 32, color: '#e0e0e0', display: 'block', marginBottom: 12 }} />
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
            style={{ marginBottom: 16, borderRadius: 8 }}
          />
        )}
      </div>

      {/* Input */}
      <div style={styles.inputSection}>
        <TextArea
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isStreaming ? t('chat.agentReplying') : t('chat.placeholder')}
          autoSize={{ minRows: 1, maxRows: 6 }}
          maxLength={MAX_CHARS}
          disabled={isStreaming}
          style={styles.textArea}
          classNames={{ textarea: 'chat-textarea' }}
        />
        <div style={styles.toolBar}>
          <div style={styles.toolLeft}>
            {tokenUsage && (tokenUsage.input_tokens > 0 || tokenUsage.output_tokens > 0) && (
              <Text type="secondary" style={{ fontSize: 11 }}>
                {`\u{1F4CA} ${t('chat.tokenUsage', { input: tokenUsage.input_tokens, output: tokenUsage.output_tokens })}`}
              </Text>
            )}
          </div>
          <div style={styles.toolRight}>
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
                disabled={!isStreaming && !inputValue.trim()}
                style={{
                  background: isStreaming ? undefined : inputValue.trim() ? ORANGE : '#d9d9d9',
                  borderColor: isStreaming ? undefined : inputValue.trim() ? ORANGE : '#d9d9d9',
                  borderRadius: 8,
                  minWidth: 32,
                  height: 32,
                }}
              />
            </Tooltip>
          </div>
        </div>
      </div>
      <style>{`
        @keyframes chat-cursor-blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
        @keyframes chat-dot-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.45; transform: scale(0.82); } }
      `}</style>
    </div>
  );
}
