import { useMemo, type ReactNode } from 'react';
import {
  CheckSquareOutlined,
  CodeOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  GlobalOutlined,
  RobotOutlined,
  SearchOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import CollapsibleBadge from './CollapsibleBadge';
import type { ToolCall } from '../../../stores/chatStore';
import { useI18n } from '../../../i18n';

/* ───────── Tool → icon / colour mapping ───────── */

interface ToolMeta {
  icon: ReactNode;
  color: string;
  bg: string;
}

function toolMeta(name: string): ToolMeta {
  const n = (name || '').toLowerCase();
  if (n.includes('todo') || n.includes('plan')) {
    return { icon: <CheckSquareOutlined />, color: '#1677ff', bg: '#e6f4ff' };
  }
  if (n.includes('write_file') || n.includes('append_file') || n.includes('patch')) {
    return { icon: <FileTextOutlined />, color: '#389e0d', bg: '#f6ffed' };
  }
  if (n.includes('edit_file') || n.includes('edit')) {
    return { icon: <EditOutlined />, color: '#d46b08', bg: '#fff7e6' };
  }
  if (n.includes('read_file') || n.includes('glob') || n.includes('search') || n.includes('grep')) {
    return { icon: <SearchOutlined />, color: '#722ed1', bg: '#f9f0ff' };
  }
  if (n === 'ls' || n.includes('list') || n.includes('dir')) {
    return { icon: <FolderOpenOutlined />, color: '#531dab', bg: '#f9f0ff' };
  }
  if (n.includes('bash') || n.includes('execute') || n.includes('terminal') || n.includes('shell')) {
    return { icon: <CodeOutlined />, color: '#fa8c16', bg: '#fff7e6' };
  }
  if (n === 'task' || n.includes('delegate') || n.includes('subagent')) {
    return { icon: <RobotOutlined />, color: '#d48806', bg: '#fffbe6' };
  }
  if (n.includes('delete') || n.includes('rm')) {
    return { icon: <DeleteOutlined />, color: '#ff4d4f', bg: '#fff1f0' };
  }
  if (n.includes('http') || n.includes('request') || n.includes('fetch') || n.includes('web')) {
    return { icon: <GlobalOutlined />, color: '#08979c', bg: '#e6fffb' };
  }
  if (n.includes('write')) {
    return { icon: <FileTextOutlined />, color: '#389e0d', bg: '#f6ffed' };
  }
  if (n.includes('read')) {
    return { icon: <SearchOutlined />, color: '#722ed1', bg: '#f9f0ff' };
  }
  return { icon: <ToolOutlined />, color: '#595959', bg: '#f5f5f5' };
}

/* ───────── JSON pretty-print with lightweight syntax colouring ───────── */

const JSON_TOKEN_RE =
  /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}[\],:]|\s+|[^\s{}[\],:"]+/g;

function highlightJson(raw: string): ReactNode {
  let pretty = raw;
  try {
    pretty = JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    // keep raw when it is not valid JSON (partial streaming chunk etc.)
  }
  const tokens = pretty.match(JSON_TOKEN_RE) ?? [];
  if (!tokens.length) return pretty;
  const parts: ReactNode[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    let color = '#595959';
    if (/^".*"$/.test(token)) {
      // string: is it a key (followed by `:`)?
      let j = i + 1;
      while (j < tokens.length && /^\s+$/.test(tokens[j])) j += 1;
      const isKey = tokens[j] === ':';
      color = isKey ? '#c41d7f' : '#389e0d';
    } else if (/^-?\d/.test(token)) {
      color = '#1677ff';
    } else if (token === 'true' || token === 'false') {
      color = '#d48806';
    } else if (token === 'null') {
      color = '#8c8c8c';
    } else if (/^\s+$/.test(token)) {
      color = 'inherit';
    }
    parts.push(
      <span key={i} style={{ color }}>
        {token}
      </span>,
    );
  }
  return parts;
}

/* ───────── Component ───────── */

/**
 * Compact tool-call badge — icon + name, click to expand args.
 *
 * Always renders collapsed; the user has to click to reveal the
 * JSON arguments.  This keeps long tool-heavy turns visually light
 * while preserving full inspectability on demand.
 */
export default function ToolCallCard({ call }: { call: ToolCall }) {
  const { t } = useI18n();
  const meta = toolMeta(call.name);
  const args = call.args ?? '';

  const body = useMemo(() => {
    if (!args) return null;
    return (
      <div>
        <div
          style={{
            fontSize: 11,
            color: '#8c8c8c',
            marginBottom: 4,
            textTransform: 'uppercase',
            letterSpacing: 0.4,
          }}
        >
          {t('chat.toolArgs')}
        </div>
        <pre
          style={{
            margin: 0,
            padding: '6px 8px',
            background: '#fafafa',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            fontSize: 11.5,
            lineHeight: 1.55,
            fontFamily:
              "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
            maxHeight: 240,
            overflow: 'auto',
          }}
        >
          {highlightJson(args)}
        </pre>
      </div>
    );
  }, [args, t]);

  return (
    <CollapsibleBadge
      icon={meta.icon}
      label={call.name}
      color={meta.color}
      bg={meta.bg}
    >
      {body}
    </CollapsibleBadge>
  );
}
