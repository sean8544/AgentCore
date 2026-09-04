import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
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
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

/* ───────── Types ───────── */
interface ToolItem {
  name: string;
  enabled: boolean;
  builtin: boolean;
  description?: string;
}

export default function ToolsPage() {
  const { t } = useI18n();
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
      antdMessage.error(t('tools.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [agentId, t]);

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
        antdMessage.success(enabled ? t('common.enabledSuccess', { name }) : t('common.disabledSuccess', { name }));
      } catch {
        antdMessage.error(t('common.operationFailed'));
      } finally {
        setToggling(null);
      }
    },
    [agentId, t],
  );

  const enabledCount = tools.filter((t) => t.enabled).length;

  const columns: ColumnsType<ToolItem> = [
    {
      title: t('tools.tool'),
      dataIndex: 'name',
      width: 220,
      render: (name: string, record) => (
        <Space>
          <Text code style={{ fontSize: 13 }}>{name}</Text>
          {record.builtin ? (
            <Tag color="warning" style={{ fontSize: 11 }}>
              {t('tools.builtin')}
            </Tag>
          ) : (
            <Tag color="processing" style={{ fontSize: 11 }}>
              {t('tools.capability')}
            </Tag>
          )}
        </Space>
      ),
    },
    { title: t('common.description'), dataIndex: 'description', ellipsis: true },
    {
      title: t('common.status'),
      dataIndex: 'enabled',
      width: 100,
      render: (enabled: boolean) => (
        <Badge
          status={enabled ? 'success' : 'default'}
          text={<Text type="secondary" style={{ fontSize: 12 }}>{enabled ? t('common.enabled') : t('common.disabled')}</Text>}
        />
      ),
    },
    {
      title: t('common.actions'),
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
    <div>
      <GooglePageHeader
        icon={<ThunderboltOutlined />}
        title={t('tools.title')}
        subtitle={t('tools.subtitle', { enabled: enabledCount, total: tools.length })}
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('tools.currentAgent')}：<Text code style={{ fontSize: 12 }}>{agentId}</Text>
          </Text>
        }
      />

      <GoogleCard bodyStyle={{ padding: 0 }}>
        <Table<ToolItem>
          rowKey="name"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={tools}
          pagination={false}
        />
      </GoogleCard>
    </div>
  );
}
