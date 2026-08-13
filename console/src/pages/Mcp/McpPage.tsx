import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message as antdMessage,
} from 'antd';
import {
  ApiOutlined,
  DeleteOutlined,
  LinkOutlined,
  PlusOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useAgentId } from '../../stores/agentStore';

const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';

const TRANSPORT_LABELS: Record<string, { label: string; color: string }> = {
  stdio: { label: 'STDIO', color: 'blue' },
  sse: { label: 'SSE', color: 'purple' },
  streamable_http: { label: 'Streamable HTTP', color: 'cyan' },
  http: { label: 'HTTP', color: 'cyan' },
};

/* ───────── Types ───────── */
interface McpServer {
  server_id: string;
  name?: string;
  transport: string;
  command?: string;
  url?: string;
  enabled: boolean;
}

interface McpTool {
  name: string;
  description?: string;
}

export default function McpPage() {
  const agentId = useAgentId();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [toolsCache, setToolsCache] = useState<Record<string, McpTool[]>>({});
  const [toolsLoading, setToolsLoading] = useState<string | null>(null);
  const [form] = Form.useForm();
  const transport = Form.useWatch('transport', form);

  /* ── Load servers ── */
  const loadServers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get(`/agents/${agentId}/mcp`);
      setServers(res.data.servers ?? []);
    } catch {
      setServers([]);
      antdMessage.error('MCP 服务器列表加载失败');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    setToolsCache({});
    void loadServers();
  }, [loadServers]);

  /* ── Add server ── */
  const submitAdd = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      await apiClient.post(`/agents/${agentId}/mcp`, values);
      antdMessage.success(`已添加 MCP 服务器 ${values.server_id}`);
      setAddOpen(false);
      form.resetFields();
      void loadServers();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return; // validation
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail || '添加失败，请重试');
    } finally {
      setSaving(false);
    }
  }, [agentId, form, loadServers]);

  /* ── Toggle enabled ── */
  const toggleServer = useCallback(
    async (serverId: string) => {
      try {
        const res = await apiClient.patch(`/agents/${agentId}/mcp/${serverId}/toggle`);
        const updated: McpServer = res.data.server;
        setServers((prev) => prev.map((s) => (s.server_id === serverId ? updated : s)));
        antdMessage.success(updated.enabled ? `已启用 ${serverId}` : `已禁用 ${serverId}`);
      } catch {
        antdMessage.error('操作失败，请重试');
      }
    },
    [agentId],
  );

  /* ── Delete ── */
  const deleteServer = useCallback(
    async (serverId: string) => {
      try {
        await apiClient.delete(`/agents/${agentId}/mcp/${serverId}`);
        antdMessage.success(`已删除 ${serverId}`);
        void loadServers();
      } catch {
        antdMessage.error('删除失败，请重试');
      }
    },
    [agentId, loadServers],
  );

  /* ── Test connection ── */
  const testConnection = useCallback(
    async (serverId: string) => {
      setTesting(serverId);
      try {
        const res = await apiClient.post(`/agents/${agentId}/mcp/${serverId}/test`);
        const data = res.data;
        if (data.ok) {
          setToolsCache((prev) => ({ ...prev, [serverId]: data.tools ?? [] }));
          antdMessage.success(`连接成功，发现 ${data.tool_count ?? 0} 个工具`);
        } else {
          antdMessage.warning(data.error || '连接失败');
        }
      } catch {
        antdMessage.error('连接测试请求失败');
      } finally {
        setTesting(null);
      }
    },
    [agentId],
  );

  /* ── Load tools for expanded row ── */
  const loadTools = useCallback(
    async (serverId: string) => {
      if (toolsCache[serverId] !== undefined) return;
      setToolsLoading(serverId);
      try {
        const res = await apiClient.get(`/agents/${agentId}/mcp/${serverId}/tools`);
        setToolsCache((prev) => ({ ...prev, [serverId]: res.data.tools ?? [] }));
      } catch (err: unknown) {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        setToolsCache((prev) => ({ ...prev, [serverId]: [] }));
        antdMessage.warning(detail || '无法获取工具列表');
      } finally {
        setToolsLoading(null);
      }
    },
    [agentId, toolsCache],
  );

  const enabledCount = servers.filter((s) => s.enabled).length;

  const columns: ColumnsType<McpServer> = [
    {
      title: '服务器',
      dataIndex: 'name',
      width: 220,
      render: (_: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong style={{ fontSize: 13 }}>{record.name || record.server_id}</Text>
          <Text code style={{ fontSize: 11, color: '#999' }}>{record.server_id}</Text>
        </Space>
      ),
    },
    {
      title: '传输方式',
      dataIndex: 'transport',
      width: 150,
      render: (t: string) => {
        const info = TRANSPORT_LABELS[t] || { label: t, color: 'default' };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: '端点',
      key: 'endpoint',
      ellipsis: true,
      render: (_: unknown, record) => (
        <Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: true }}>
          {record.transport === 'stdio' ? record.command : record.url}
        </Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      width: 90,
      render: (enabled: boolean) => (
        <Badge
          status={enabled ? 'success' : 'default'}
          text={<Text type="secondary" style={{ fontSize: 12 }}>{enabled ? '已启用' : '已禁用'}</Text>}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 260,
      render: (_: unknown, record) => (
        <Space size={4}>
          <Tooltip title="测试连接并刷新工具列表">
            <Button
              size="small"
              type="link"
              icon={<LinkOutlined />}
              loading={testing === record.server_id}
              onClick={() => void testConnection(record.server_id)}
            >
              测试
            </Button>
          </Tooltip>
          <Switch
            size="small"
            checked={record.enabled}
            onChange={() => void toggleServer(record.server_id)}
          />
          <Popconfirm
            title={`删除 MCP 服务器 ${record.server_id}？`}
            onConfirm={() => void deleteServer(record.server_id)}
          >
            <Button size="small" type="link" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Card
        styles={{ body: { padding: 0 } }}
        title={
          <Space>
            <ApiOutlined style={{ color: ORANGE }} />
            <span>MCP 服务器</span>
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {enabledCount}/{servers.length} 已启用 · 已启用服务器的工具在下次对话注入
            </Text>
          </Space>
        }
        extra={
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>
              当前 Agent：<Text code style={{ fontSize: 12 }}>{agentId}</Text>
            </Text>
            <Button
              size="small"
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setAddOpen(true)}
            >
              添加服务器
            </Button>
          </Space>
        }
      >
        <Table<McpServer>
          rowKey="server_id"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={servers}
          pagination={false}
          locale={{
            emptyText: (
              <Space direction="vertical" size={4} style={{ padding: '16px 0' }}>
                <ApiOutlined style={{ fontSize: 28, color: '#ddd' }} />
                <Text type="secondary">尚未配置 MCP 服务器，点击右上角「添加服务器」开始</Text>
              </Space>
            ),
          }}
          expandable={{
            expandedRowRender: (record) => {
              const tools = toolsCache[record.server_id];
              if (toolsLoading === record.server_id) {
                return <Spin size="small" style={{ margin: '8px 0' }} />;
              }
              if (!tools) return null;
              if (!tools.length) {
                return (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    未获取到工具（服务器未连接或依赖未安装）
                  </Text>
                );
              }
              return (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  {tools.map((tool) => (
                    <Space key={tool.name} align="start" size={8}>
                      <ToolOutlined style={{ color: ORANGE, marginTop: 4 }} />
                      <Space direction="vertical" size={0}>
                        <Text code style={{ fontSize: 12 }}>{tool.name}</Text>
                        {tool.description && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {tool.description.slice(0, 160)}
                          </Text>
                        )}
                      </Space>
                    </Space>
                  ))}
                </Space>
              );
            },
            rowExpandable: () => true,
            onExpand: (expanded, record) => {
              if (expanded) void loadTools(record.server_id);
            },
          }}
        />
      </Card>

      <Modal
        title="添加 MCP 服务器"
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        onOk={() => void submitAdd()}
        confirmLoading={saving}
        okText="添加"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={{ transport: 'stdio', enabled: true }}>
          <Form.Item
            label="服务器 ID"
            name="server_id"
            rules={[
              { required: true, message: '请输入服务器 ID' },
              {
                pattern: /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/,
                message: '仅支持字母、数字、下划线和连字符',
              },
            ]}
          >
            <Input placeholder="例如 filesystem" />
          </Form.Item>
          <Form.Item label="显示名称" name="name">
            <Input placeholder="例如 文件系统服务" />
          </Form.Item>
          <Form.Item label="传输方式" name="transport" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'stdio', label: 'STDIO（本地进程）' },
                { value: 'sse', label: 'SSE（HTTP 流）' },
                { value: 'streamable_http', label: 'Streamable HTTP' },
              ]}
            />
          </Form.Item>
          {transport === 'stdio' ? (
            <Form.Item
              label="启动命令"
              name="command"
              rules={[{ required: true, message: '请输入启动命令' }]}
            >
              <Input placeholder="例如 npx -y @modelcontextprotocol/server-filesystem /path" />
            </Form.Item>
          ) : (
            <Form.Item
              label="服务地址"
              name="url"
              rules={[{ required: true, message: '请输入服务地址' }]}
            >
              <Input placeholder="例如 http://localhost:8000/mcp" />
            </Form.Item>
          )}
          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
