import React, { useState, useRef, useEffect, type CSSProperties } from 'react';
import {
  Alert,
  Avatar,
  Button,
  Dropdown,
  Input,
  Select,
  Spin,
  Tooltip,
  Collapse,
  Typography,
  message as antdMessage,
} from 'antd';
import {
  SendOutlined,
  AudioOutlined,
  PaperClipOutlined,
  CopyOutlined,
  FileTextOutlined,
  RobotOutlined,
  PlusOutlined,
  MessageOutlined,
} from '@ant-design/icons';
import { useChatStore, formatTimestamp, type Message } from '../../stores/chatStore';
import ChatModelSelector from './components/ChatModelSelector';

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
  toolCallRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 12px',
    background: '#f9f9fb',
    borderRadius: 8,
    marginBottom: 10,
    fontSize: 13,
    color: '#555',
    border: '1px solid #f0f0f0',
    width: 'fit-content',
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
function renderMarkdown(text: string) {
  // Simple markdown-ish renderer: bold, inline code, lists, headings
  const lines = text.split('\n');
  const elements: React.JSX.Element[] = [];
  let key = 0;

  for (const line of lines) {
    // heading
    if (line.startsWith('## ')) {
      elements.push(
        <div key={key++} style={{ fontSize: 16, fontWeight: 700, margin: '8px 0 4px', color: '#1a1a1a' }}>
          {inlineFormat(line.slice(3))}
        </div>,
      );
      continue;
    }
    // list item
    if (line.startsWith('- ')) {
      elements.push(
        <div key={key++} style={{ paddingLeft: 16, margin: '2px 0' }}>
          {'\u2022 '}
          {inlineFormat(line.slice(2))}
        </div>,
      );
      continue;
    }
    // blank line
    if (line.trim() === '') {
      elements.push(<div key={key++} style={{ height: 8 }} />);
      continue;
    }
    // normal paragraph
    elements.push(
      <div key={key++} style={{ margin: '2px 0' }}>
        {inlineFormat(line)}
      </div>,
    );
  }
  return <>{elements}</>;
}

function inlineFormat(text: string): (string | React.JSX.Element)[] {
  const parts: (string | React.JSX.Element)[] = [];
  // handle **bold** and `code`
  const regex = /(\*\*(.+?)\*\*)|(`(.+?)`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let k = 0;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    if (match[2]) {
      parts.push(<strong key={`b${k++}`}>{match[2]}</strong>);
    } else if (match[4]) {
      parts.push(
        <code
          key={`c${k++}`}
          style={{
            background: '#f3f3f5',
            padding: '1px 6px',
            borderRadius: 4,
            fontSize: 13,
            fontFamily: 'Menlo, Consolas, monospace',
          }}
        >
          {match[4]}
        </code>,
      );
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts.length ? parts : [text];
}

/* ───────── Components ───────── */

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

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const thinking = message.thinking || liveThinking;
  const isEmpty = !message.content && message.streaming;

  return (
    <div style={styles.msgRow}>
      <Avatar
        size={36}
        style={{ background: ORANGE, flexShrink: 0 }}
        icon={<RobotOutlined />}
      />
      <div style={styles.msgBody}>
        <div style={styles.agentName}>QwenPaw</div>

        {/* Thinking */}
        {thinking && (
          <div style={styles.thinkingBlock}>
            <Collapse
              size="small"
              items={[
                {
                  key: '1',
                  label: (
                    <span style={{ fontSize: 13, color: '#888' }}>
                      {'\u{1F4AD}'} Thinking
                    </span>
                  ),
                  children: (
                    <Text type="secondary" style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>
                      {thinking}
                    </Text>
                  ),
                },
              ]}
              style={{ background: '#fafafa', borderRadius: 8 }}
            />
          </div>
        )}

        {/* Tool calls */}
        {message.toolCalls?.map((tc, i) => (
          <div key={i} style={styles.toolCallRow}>
            <FileTextOutlined style={{ color: '#999' }} />
            <span>{'\u{1F527}'} {tc.name}</span>
            {tc.args && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {tc.args}
              </Text>
            )}
          </div>
        ))}

        {/* Content */}
        {isEmpty ? (
          <div style={{ ...styles.msgContent, color: '#999', display: 'flex', alignItems: 'center', gap: 8 }}>
            <Spin size="small" />
            Agent {'\u6B63\u5728\u601D\u8003'}...
          </div>
        ) : (
          <div style={styles.msgContent}>
            {renderMarkdown(message.content)}
            {message.streaming && (
              <span style={{ display: 'inline-block', width: 7, height: 14, background: ORANGE, marginLeft: 3, verticalAlign: 'text-bottom', animation: 'chat-cursor-blink 1s steps(1) infinite' }} />
            )}
          </div>
        )}
        {message.error && (
          <Text type="danger" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
            {'\u26A0\uFE0F'} {'\u53D1\u9001\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5'}
          </Text>
        )}

        {/* Footer */}
        <div style={styles.msgFooter}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {message.timestamp}
          </Text>
          <Tooltip title={copied ? '\u5DF2\u590D\u5236' : '\u590D\u5236'}>
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
          {message.content}
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
  const messages = useChatStore((s) => s.messages);
  const sessions = useChatStore((s) => s.sessions);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const error = useChatStore((s) => s.error);
  const thinkingText = useChatStore((s) => s.thinkingText);
  const streamingMsgId = useChatStore((s) => s.streamingMsgId);
  const loadSessions = useChatStore((s) => s.loadSessions);
  const selectSession = useChatStore((s) => s.selectSession);
  const newSession = useChatStore((s) => s.newSession);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const clearError = useChatStore((s) => s.clearError);

  const [inputValue, setInputValue] = useState('');
  const [mode, setMode] = useState('auto');
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, thinkingText]);

  const handleSend = () => {
    const content = inputValue.trim();
    if (!content || isStreaming) return;
    setInputValue('');
    sendMessage(content).then(() => {
      const latestError = useChatStore.getState().error;
      if (latestError) {
        antdMessage.error(latestError);
        clearError();
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
          {s.session_id === currentSessionId ? '\u5F53\u524D\u4F1A\u8BDD' : `\u4F1A\u8BDD ${s.session_id.slice(0, 8)}`}
          <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
            {s.message_count} \u6761 · {formatTimestamp(s.updated_at)}
          </Text>
        </span>
      ),
      onClick: () => void selectSession(s.session_id),
    })),
    ...(sessions.length === 0
      ? [{ key: 'empty', label: <Text type="secondary" style={{ fontSize: 13 }}>{'\u6682\u65E0\u5386\u53F2\u4F1A\u8BDD'}</Text>, disabled: true }]
      : []),
  ];

  return (
    <div style={styles.wrapper}>
      {/* Header */}
      <div style={styles.header}>
        <RobotOutlined style={{ fontSize: 18, color: ORANGE }} />
        <h2 style={styles.headerTitle}>{'\u95EE\u5019'}</h2>
        <ChatModelSelector />
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <Dropdown menu={{ items: sessionMenuItems }} trigger={['click']} placement="bottomRight" disabled={isStreaming}>
            <Button size="small" icon={<MessageOutlined />}
              style={{ borderRadius: 8, fontSize: 13 }}>
              {currentSession ? `\u4F1A\u8BDD ${currentSession.session_id.slice(0, 8)}` : '\u9009\u62E9\u4F1A\u8BDD'}
            </Button>
          </Dropdown>
          <Tooltip title={'\u65B0\u5EFA\u4F1A\u8BDD'}>
            <Button size="small" icon={<PlusOutlined />}
              onClick={newSession}
              disabled={isStreaming}
              style={{ borderRadius: 8, color: ORANGE, borderColor: '#ffd8b3' }} />
          </Tooltip>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} style={styles.messageArea}>
        {messages.length === 0 && !isStreaming && (
          <div style={{ textAlign: 'center', padding: '48px 0', color: '#bbb', fontSize: 13 }}>
            <RobotOutlined style={{ fontSize: 32, color: '#e0e0e0', display: 'block', marginBottom: 12 }} />
            {'\u53D1\u9001\u4E00\u6761\u6D88\u606F\uFF0C\u5F00\u59CB\u65B0\u7684\u5BF9\u8BDD'}
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
          placeholder={isStreaming ? 'Agent \u6B63\u5728\u56DE\u590D\u4E2D...' : "\u2191\u2193 \u6D4F\u89C8\u6D88\u606F \u00B7 '/' \u5FEB\u6377\u6307\u4EE4\uFF08\u5BA1\u6279\u65F6 '/approve' \u6216 '/deny'\uFF09"}
          autoSize={{ minRows: 1, maxRows: 6 }}
          maxLength={MAX_CHARS}
          disabled={isStreaming}
          style={styles.textArea}
          classNames={{ textarea: 'chat-textarea' }}
        />
        <div style={styles.toolBar}>
          <div style={styles.toolLeft}>
            <Tooltip title={'\u8BED\u97F3\u8F93\u5165'}>
              <Button type="text" size="small" icon={<AudioOutlined style={{ fontSize: 17, color: '#999' }} />} />
            </Tooltip>
            <Tooltip title={'\u9644\u52A0\u6587\u4EF6'}>
              <Button type="text" size="small" icon={<PaperClipOutlined style={{ fontSize: 17, color: '#999' }} />} />
            </Tooltip>
          </div>
          <div style={styles.toolRight}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {inputValue.length}/{MAX_CHARS}
            </Text>
            <Select
              value={mode}
              onChange={setMode}
              size="small"
              style={{ width: 108 }}
              options={[
                { value: 'auto', label: '\u81EA\u52A8\u6A21\u5F0F' },
                { value: 'manual', label: '\u624B\u52A8\u6A21\u5F0F' },
              ]}
            />
            <Button
              type="primary"
              size="small"
              icon={<SendOutlined style={{ fontSize: 14, rotate: '-45deg' } as CSSProperties} />}
              onClick={handleSend}
              disabled={!inputValue.trim() || isStreaming}
              loading={isStreaming}
              style={{
                background: inputValue.trim() && !isStreaming ? ORANGE : '#d9d9d9',
                borderColor: inputValue.trim() && !isStreaming ? ORANGE : '#d9d9d9',
                borderRadius: 8,
                minWidth: 32,
                height: 32,
              }}
            />
          </div>
        </div>
      </div>
      <style>{'@keyframes chat-cursor-blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }'}</style>
    </div>
  );
}
