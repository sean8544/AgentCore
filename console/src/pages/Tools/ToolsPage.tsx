import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Card,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message as antdMessage,
} from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useAgentId } from '../../stores/agentStore';

const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';

/* ───────── Types ───────── */
interface ToolItem {
  name: string;
  enabled: boolean;
  builtin: boolean;
  description?: string;
}

export default function ToolsPage() {
  const agentId = useAgentId();
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [toggling, setToggling] = useState<string | null>(null);

  /* ── Load tools ── */
  const loadTools = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get(`/agents/${agentId}/tools`);
      setTools(res.data.tools ?? []);
    } catch {
      setTools([]);
      antdMessage.error('工具列表加载失败');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void loadTools();
  }, [loadTools]);

  /* ── Toggle ── */
  const toggleTool = useCallback(
    async (name: string, enabled: boolean) => {
      setToggling(name);
      try {
        await apiClient.put(`/agents/${agentId}/tools/${name}`, { enabled });
        setTools((prev) => prev.map((t) => (t.name === name ? { ...t, enabled } : t)));
        antdMessage.success(enabled ? `已启用 ${name}` : `已禁用 ${name}`);
      } catch {
        antdMessage.error('操作失败，请重试');
      } finally {
        setToggling(null);
      }
    },
    [agentId],
  );

  const enabledCount = tools.filter((t) => t.enabled).length;

  const columns: ColumnsType<ToolItem> = [
    {
      title: '工具',
      dataIndex: 'name',
      width: 220,
      render: (name: string, record) => (
        <Space>
          <Text code style={{ fontSize: 13 }}>{name}</Text>
          {record.builtin && (
            <Tag style={{ fontSize: 11, color: ORANGE, borderColor: '#ffd8b3', background: '#fff7ef' }}>
              内置
            </Tag>
          )}
        </Space>
      ),
    },
    { title: '说明', dataIndex: 'description', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 100,
      render: (enabled: boolean) => (
        <Badge
          status={enabled ? 'success' : 'default'}
          text={<Text type="secondary" style={{ fontSize: 12 }}>{enabled ? '已启用' : '已禁用'}</Text>}
        />
      ),
    },
    {
      title: '开关',
      width: 80,
      align: 'center',
      render: (_, record) => (
        <Switch
          size="small"
          checked={record.enabled}
          loading={toggling === record.name}
          onChange={(checked) => void toggleTool(record.name, checked)}
        />
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Card
        styles={{ body: { padding: 0 } }}
        title={
          <Space>
            <ThunderboltOutlined style={{ color: ORANGE }} />
            <span>工具管理</span>
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {enabledCount}/{tools.length} 已启用 · 变更在下次对话生效
            </Text>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            当前 Agent：<Text code style={{ fontSize: 12 }}>{agentId}</Text>
          </Text>
        }
      >
        <Table<ToolItem>
          rowKey="name"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={tools}
          pagination={false}
        />
      </Card>
    </div>
  );
}
