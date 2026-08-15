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
import { useI18n } from '../../i18n';

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
  const { t } = useI18n();
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
      antdMessage.error(t('mcp.loadFailed'));
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
      antdMessage.success(t('mcp.addSuccess', { id: values.server_id }));
      setAddOpen(false);
      form.resetFields();
      void loadServers();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return; // validation
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail || t('mcp.addFailed'));
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
        antdMessage.success(updated.enabled ? t('common.enabledSuccess', { name: serverId }) : t('common.disabledSuccess', { name: serverId }));
      } catch {
        antdMessage.error(t('common.operationFailed'));
      }
    },
    [agentId],
  );

  /* ── Delete ── */
  const deleteServer = useCallback(
    async (serverId: string) => {
      try {
        await apiClient.delete(`/agents/${agentId}/mcp/${serverId}`);
        antdMessage.success(t('common.deletedSuccess', { name: serverId }));
        void loadServers();
      } catch {
        antdMessage.error(t('mcp.deleteFailed'));
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
          antdMessage.success(t('mcp.testSuccess', { count: data.tool_count ?? 0 }));
        } else {
          antdMessage.warning(data.error || t('mcp.testFailed'));
        }
      } catch {
        antdMessage.error(t('mcp.testRequestFailed'));
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
        antdMessage.warning(detail || t('mcp.toolsLoadFailed'));
      } finally {
        setToolsLoading(null);
      }
    },
    [agentId, toolsCache],
  );

  const enabledCount = servers.filter((s) => s.enabled).length;

  const columns: ColumnsType<McpServer> = [
    {
      title: t('mcp.server'),
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
      title: t('mcp.transport'),
      dataIndex: 'transport',
      width: 150,
      render: (t: string) => {
        const info = TRANSPORT_LABELS[t] || { label: t, color: 'default' };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: t('mcp.endpoint'),
      key: 'endpoint',
      ellipsis: true,
      render: (_: unknown, record) => (
        <Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: true }}>
          {record.transport === 'stdio' ? record.command : record.url}
        </Text>
      ),
    },
    {
      title: t('common.status'),
      dataIndex: 'enabled',
      width: 90,
      render: (enabled: boolean) => (
        <Badge
          status={enabled ? 'success' : 'default'}
          text={<Text type="secondary" style={{ fontSize: 12 }}>{enabled ? t('common.enabled') : t('common.disabled')}</Text>}
        />
      ),
    },
    {
      title: t('common.actions'),
      key: 'actions',
      width: 260,
      render: (_: unknown, record) => (
        <Space size={4}>
          <Tooltip title={t('mcp.testConnection')}>
            <Button
              size="small"
              type="link"
              icon={<LinkOutlined />}
              loading={testing === record.server_id}
              onClick={() => void testConnection(record.server_id)}
            >
              {t('mcp.test')}
            </Button>
          </Tooltip>
          <Switch
            size="small"
            checked={record.enabled}
            onChange={() => void toggleServer(record.server_id)}
          />
          <Popconfirm
            title={t('mcp.deleteConfirm', { id: record.server_id })}
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
            <span>{t('mcp.title')}</span>
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {t('mcp.subtitle', { enabled: enabledCount, total: servers.length })}
            </Text>
          </Space>
        }
        extra={
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('mcp.currentAgent')}：<Text code style={{ fontSize: 12 }}>{agentId}</Text>
            </Text>
            <Button
              size="small"
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setAddOpen(true)}
            >
              {t('mcp.addServer')}
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
                <Text type="secondary">{t('mcp.emptyHint')}</Text>
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
                    {t('mcp.noToolsFound')}
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
        title={t('mcp.addModalTitle')}
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        onOk={() => void submitAdd()}
        confirmLoading={saving}
        okText={t('common.add')}
        cancelText={t('common.cancel')}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={{ transport: 'stdio', enabled: true }}>
          <Form.Item
            label={t('mcp.serverId')}
            name="server_id"
            rules={[
              { required: true, message: t('mcp.serverIdRequired') },
              {
                pattern: /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/,
                message: t('mcp.serverIdPattern'),
              },
            ]}
          >
            <Input placeholder={t('mcp.serverIdExample')} />
          </Form.Item>
          <Form.Item label={t('mcp.displayName')} name="name">
            <Input placeholder={t('mcp.displayNameExample')} />
          </Form.Item>
          <Form.Item label={t('mcp.transportType')} name="transport" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'stdio', label: t('mcp.stdioLocal') },
                { value: 'sse', label: t('mcp.sseHttp') },
                { value: 'streamable_http', label: 'Streamable HTTP' },
              ]}
            />
          </Form.Item>
          {transport === 'stdio' ? (
            <Form.Item
              label={t('mcp.startCommand')}
              name="command"
              rules={[{ required: true, message: t('mcp.startCommandRequired') }]}
            >
              <Input placeholder={t('mcp.startCommandExample')} />
            </Form.Item>
          ) : (
            <Form.Item
              label={t('mcp.serviceUrl')}
              name="url"
              rules={[{ required: true, message: t('mcp.serviceUrlRequired') }]}
            >
              <Input placeholder={t('mcp.serviceUrlExample')} />
            </Form.Item>
          )}
          <Form.Item label={t('mcp.enableLabel')} name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
