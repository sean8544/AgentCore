import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Button,
  Divider,
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
  GlobalOutlined,
  FolderOutlined,
  SearchOutlined,
  LinkOutlined,
  PlusOutlined,
  ToolOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useAgentId } from '../../stores/agentStore';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

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

interface McpPreset {
  server_id: string;
  name: string;
  transport: string;
  command?: string;
  url?: string;
  description?: string;
  icon?: string;
  category?: string;
}

const PRESET_ICONS: Record<string, React.ReactNode> = {
  browser: <GlobalOutlined />,
  file: <FolderOutlined />,
  search: <SearchOutlined />,
};

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

  // Preset state
  const [presets, setPresets] = useState<McpPreset[]>([]);
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);

  /* ── Load presets ── */
  const loadPresets = useCallback(async () => {
    try {
      const res = await apiClient.get(`/agents/${agentId}/mcp/presets`);
      setPresets(res.data.presets ?? []);
    } catch {
      // Presets are optional — silently ignore
    }
  }, [agentId]);

  useEffect(() => {
    void loadPresets();
  }, [loadPresets]);

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
  }, [agentId, t]);

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
      antdMessage.success(
        selectedPreset
          ? t('mcp.presetAdded', { id: values.server_id })
          : t('mcp.addSuccess', { id: values.server_id }),
      );
      setAddOpen(false);
      form.resetFields();
      setSelectedPreset(null);
      void loadServers();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return; // validation
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail || t('mcp.addFailed'));
    } finally {
      setSaving(false);
    }
  }, [agentId, form, loadServers, t, selectedPreset]);

  /* ── Select preset ── */
  const selectPreset = useCallback((preset: McpPreset | null) => {
    if (preset) {
      setSelectedPreset(preset.server_id);
      form.setFieldsValue({
        server_id: preset.server_id,
        name: preset.name,
        transport: preset.transport,
        command: preset.command || '',
        url: preset.url || '',
        enabled: true,
      });
    } else {
      setSelectedPreset(null);
      form.resetFields();
      form.setFieldsValue({ transport: 'stdio', enabled: true });
    }
  }, [form]);

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
    [agentId, t],
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
    [agentId, loadServers, t],
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
    [agentId, t],
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
    [agentId, toolsCache, t],
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
          <Text code style={{ fontSize: 11, color: 'var(--google-muted-foreground)' }}>{record.server_id}</Text>
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
    <div>
      <GooglePageHeader
        icon={<ApiOutlined />}
        title={t('mcp.title')}
        subtitle={t('mcp.subtitle', { enabled: enabledCount, total: servers.length })}
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
      />

      <GoogleCard bodyStyle={{ padding: 0 }}>
        <Table<McpServer>
          rowKey="server_id"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={servers}
          pagination={false}
          locale={{
            emptyText: (
              <Space direction="vertical" size={4} style={{ padding: 'var(--google-space-8) 0' }}>
                <ApiOutlined style={{ fontSize: 28, color: 'var(--google-muted-foreground)' }} />
                <Text type="secondary">{t('mcp.emptyHint')}</Text>
              </Space>
            ),
          }}
          expandable={{
            expandedRowRender: (record) => {
              const tools = toolsCache[record.server_id];
              if (toolsLoading === record.server_id) {
                return <Spin size="small" style={{ margin: 'var(--google-space-3) 0' }} />;
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
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  {tools.map((tool) => (
                    <div
                      key={tool.name}
                      style={{
                        display: 'flex',
                        gap: 'var(--google-space-3)',
                        padding: 'var(--google-space-3) var(--google-space-4)',
                        border: '1px solid var(--google-border)',
                        borderRadius: 'var(--google-radius-md)',
                        background: 'var(--google-background)',
                      }}
                    >
                      <ToolOutlined style={{ color: 'var(--google-primary)', marginTop: 4 }} />
                      <Space direction="vertical" size={0}>
                        <Text code style={{ fontSize: 12 }}>{tool.name}</Text>
                        {tool.description && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {tool.description.slice(0, 160)}
                          </Text>
                        )}
                      </Space>
                    </div>
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
      </GoogleCard>

      <Modal
        title={t('mcp.addModalTitle')}
        open={addOpen}
        onCancel={() => { setAddOpen(false); setSelectedPreset(null); form.resetFields(); }}
        onOk={() => void submitAdd()}
        confirmLoading={saving}
        okText={t('common.add')}
        cancelText={t('common.cancel')}
        destroyOnHidden
        width={680}
      >
        {/* ── Preset cards ── */}
        {presets.length > 0 && (
          <>
            <div style={{ marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>{t('mcp.preset')}</Text>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 16 }}>
              {presets.map((preset) => {
                const isActive = selectedPreset === preset.server_id;
                const iconNode = PRESET_ICONS[preset.category || ''] || <ApiOutlined />;
                return (
                  <div
                    key={preset.server_id}
                    onClick={() => selectPreset(preset)}
                    style={{
                      minWidth: 0,
                      border: `1.5px solid ${isActive ? 'var(--google-primary)' : 'var(--google-border)'}`,
                      borderRadius: 'var(--google-radius-md)',
                      padding: 'var(--google-space-4)',
                      cursor: 'pointer',
                      background: isActive ? 'var(--google-primary-bg, #f0f7ff)' : 'var(--google-card)',
                      transition: 'all 0.15s',
                      overflow: 'hidden',
                    }}
                  >
                    <Space direction="vertical" size={2} style={{ width: '100%' }}>
                      <Space size={4}>
                        <span style={{ fontSize: 16 }}>{preset.icon || iconNode}</span>
                        <Text strong style={{ fontSize: 12 }}>{preset.name}</Text>
                      </Space>
                      <Text
                        type="secondary"
                        style={{ fontSize: 11, lineHeight: '1.3' }}
                        ellipsis={{ tooltip: preset.description }}
                      >
                        {preset.description}
                      </Text>
                    </Space>
                  </div>
                );
              })}
              {/* Custom option */}
              <div
                onClick={() => selectPreset(null)}
                style={{
                  border: `1.5px dashed ${selectedPreset === null ? 'var(--google-primary)' : 'var(--google-border)'}`,
                  borderRadius: 'var(--google-radius-md)',
                  padding: 'var(--google-space-4)',
                  cursor: 'pointer',
                  background: selectedPreset === null ? 'var(--google-primary-bg, #f0f7ff)' : 'var(--google-card)',
                  transition: 'all 0.15s',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  minHeight: 64,
                }}
              >
                <Space direction="vertical" size={0} align="center">
                  <SettingOutlined style={{ fontSize: 16, color: 'var(--google-muted-foreground)' }} />
                  <Text type="secondary" style={{ fontSize: 11 }}>{t('mcp.presetCustom')}</Text>
                </Space>
              </div>
            </div>
            <Divider style={{ margin: 'var(--google-space-3) 0' }} />
          </>
        )}

        {/* ── Server form ── */}
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
            <Input placeholder={t('mcp.serverIdExample')} disabled={!!selectedPreset} />
          </Form.Item>
          <Form.Item label={t('mcp.displayName')} name="name">
            <Input placeholder={t('mcp.displayNameExample')} disabled={!!selectedPreset} />
          </Form.Item>
          <Form.Item label={t('mcp.transportType')} name="transport" rules={[{ required: true }]}>
            <Select
              disabled={!!selectedPreset}
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
              <Input placeholder={t('mcp.startCommandExample')} disabled={!!selectedPreset} />
            </Form.Item>
          ) : (
            <Form.Item
              label={t('mcp.serviceUrl')}
              name="url"
              rules={[{ required: true, message: t('mcp.serviceUrlRequired') }]}
            >
              <Input placeholder={t('mcp.serviceUrlExample')} disabled={!!selectedPreset} />
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
