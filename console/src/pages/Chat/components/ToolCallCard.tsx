import { useState, type ReactNode } from 'react';
import { Button, Tooltip, Typography } from 'antd';
import {
  CheckSquareOutlined,
  CodeOutlined,
  DeleteOutlined,
  DownOutlined,
  EditOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  GlobalOutlined,
  RightOutlined,
  RobotOutlined,
  SearchOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import type { ToolCall } from '../../../stores/chatStore';

const { Text } = Typography;

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
  if (n.includes('write_file') || n.includes('edit_file') || n.includes('append_file') || n.includes('patch')) {
    return { icon: <FileTextOutlined />, color: '#389e0d', bg: '#f6ffed' };
  }
  if (n.includes('read_file') || n.includes('ls') || n.includes('glob') || n.includes('search') || n.includes('grep')) {
    return { icon: <SearchOutlined />, color: '#722ed1', bg: '#f9f0ff' };
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
  if (n.includes('edit') || n.includes('write')) {
    return { icon: <EditOutlined />, color: '#d46b08', bg: '#fff7e6' };
  }
  if (n.includes('read') || n.includes('list') || n.includes('dir')) {
    return { icon: <FolderOpenOutlined />, color: '#531dab', bg: '#f9f0ff' };
  }
  return { icon: <ToolOutlined />, color: '#595959', bg: '#f5f5f5' };
}

/* ───────── JSON syntax highlighting ───────── */

const JSON_RE =
  /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}[\],:]|[^\s{}[\],:"]+/g;

function highlightJson(raw: string): ReactNode[] {
  let pretty = raw;
  try {
    pretty = JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    // keep the raw text when it is not valid JSON
  }
  const tokens = pretty.match(JSON_RE) ?? [];
  const parts: ReactNode[] = [];
  let key = 0;
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    let color = '#595959'; // punctuation / structure
    if (/^".*"$/.test(token)) {
      const isKey = tokens[i + 1] === ':';
      color = isKey ? '#c41d7f' : '#389e0d'; // key / string value
    } else if (/^-?\d/.test(token)) {
      color = '#1677ff'; // number
    } else if (token === 'true' || token === 'false') {
      color = '#d48806'; // boolean
    } else if (token === 'null') {
      color = '#8c8c8c'; // null
    }
    parts.push(
      <span key={key++} style={{ color }}>
        {token}
      </span>,
    );
  }
  return parts;
}

/* ───────── Component ───────── */

export default function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const meta = toolMeta(call.name);
  const hasArgs = !!call.args && call.args.trim() !== '' && call.args.trim() !== '{}';

  return (
    <div
      style={{
        border: '1px solid #f0f0f0',
        borderLeft: `3px solid ${meta.color}`,
        borderRadius: 10,
        background: '#fff',
        marginBottom: 8,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 10px',
          cursor: hasArgs ? 'pointer' : 'default',
          background: '#fafafa',
        }}
        onClick={hasArgs ? () => setOpen((v) => !v) : undefined}
      >
        <span
          style={{
            width: 22,
            height: 22,
            borderRadius: 6,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: meta.bg,
            color: meta.color,
            fontSize: 13,
            flexShrink: 0,
          }}
        >
          {meta.icon}
        </span>
        <Text code style={{ fontSize: 12.5, color: '#333' }}>
          {call.name}
        </Text>
        {hasArgs && (
          <Text
            type="secondary"
            style={{
              fontSize: 12,
              flex: 1,
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              color: '#999',
            }}
          >
            {call.args.length > 90 ? `${call.args.slice(0, 90)}…` : call.args}
          </Text>
        )}
        {hasArgs ? (
          <Button
            type="text"
            size="small"
            icon={
              open ? (
                <DownOutlined style={{ fontSize: 11, color: '#bbb' }} />
              ) : (
                <RightOutlined style={{ fontSize: 11, color: '#bbb' }} />
              )
            }
            style={{ padding: 0, width: 22, height: 22, flexShrink: 0 }}
            onClick={(e) => {
              e.stopPropagation();
              setOpen((v) => !v);
            }}
          />
        ) : (
          <Tooltip title={call.name}>
            <span style={{ width: 22, height: 22, flexShrink: 0 }} />
          </Tooltip>
        )}
      </div>

      {open && hasArgs && (
        <pre
          style={{
            margin: 0,
            padding: '10px 14px',
            background: '#fff',
            borderTop: '1px dashed #f0f0f0',
            fontSize: 12,
            lineHeight: 1.7,
            fontFamily: 'Menlo, Consolas, monospace',
            maxHeight: 320,
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {highlightJson(call.args)}
        </pre>
      )}
    </div>
  );
}
