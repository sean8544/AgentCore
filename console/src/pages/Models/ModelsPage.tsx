import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message as antdMessage,
} from 'antd';
import { ApiOutlined, CheckCircleOutlined, CloseCircleOutlined, ExperimentOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useAgentStore } from '../../stores/agentStore';

const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';

/* ───────── Types ───────── */
interface ModelItem {
  provider: string;
  name: string;
  base_url: string;
  api_key_env: string;
  api_key_set: boolean;
  agents: string[];
}

type TestState = 'idle' | 'testing' | 'ok' | 'failed' | 'warning';

export default function ModelsPage() {
  const agents = useAgentStore((s) => s.agents);
  const refreshAgents = useAgentStore((s) => s.refreshAgents);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});
  const [testErrors, setTestErrors] = useState<Record<string, string>>({});
  const [assigningKey, setAssigningKey] = useState<string | null>(null);

  const keyOf = (m: ModelItem) => `${m.provider}|${m.name}|${m.base_url}`;

  /* ── Load ── */
  const loadModels = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get('/models');
      setModels(res.data.models ?? []);
    } catch {
      setModels([]);
      antdMessage.error('模型列表加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadModels();
    void refreshAgents();
  }, [loadModels, refreshAgents]);

  /* ── Agent options for assignment ── */
  const agentOptions = useMemo(() => {
    const ids = new Set<string>(agents.map((a) => a.agent_id));
    models.forEach((m) => (m.agents ?? []).forEach((id) => ids.add(id)));
    return Array.from(ids).sort().map((id) => ({ value: id, label: id }));
  }, [agents, models]);

  /* ── Assign model to an agent ── */
  const assignToAgent = useCallback(
    async (model: ModelItem, agentId: string) => {
      const key = `${keyOf(model)}->${agentId}`;
      setAssigningKey(key);
      try {
        await apiClient.put(`/agents/${agentId}/model`, {
          provider: model.provider,
          name: model.name,
          ...(model.base_url ? { base_url: model.base_url } : {}),
          ...(model.api_key_env ? { api_key_env: model.api_key_env } : {}),
        });
        antdMessage.success(`已将 ${model.name} 分配给 ${agentId}，下一轮对话生效`);
        await Promise.all([loadModels(), refreshAgents()]);
      } catch (err) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        antdMessage.error(detail || '分配失败，请重试');
      } finally {
        setAssigningKey(null);
      }
    },
    [loadModels, refreshAgents],
  );

  /* ── Connection test ── */
  const testModel = useCallback(async (model: ModelItem) => {
    const key = keyOf(model);
    setTestStates((prev) => ({ ...prev, [key]: 'testing' }));
    try {
      const res = await apiClient.post('/models/test', {
        provider: model.provider,
        name: model.name,
        base_url: model.base_url,
        api_key_env: model.api_key_env,
      });
      const result: string = res.data.result;
      setTestStates((prev) => ({ ...prev, [key]: (result as TestState) ?? 'ok' }));
      setTestErrors((prev) => ({ ...prev, [key]: res.data.error ?? '' }));
      if (result === 'ok') {
        antdMessage.success(`${model.name} 连接正常`);
      } else if (result === 'warning') {
        antdMessage.warning(res.data.error ?? '端点可用但未找到模型');
      } else {
        antdMessage.error(res.data.error ?? '连接失败');
      }
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setTestStates((prev) => ({ ...prev, [key]: 'failed' }));
      setTestErrors((prev) => ({ ...prev, [key]: detail ?? '请求失败' }));
      antdMessage.error(detail ?? '连接测试失败');
    }
  }, []);

  const columns: ColumnsType<ModelItem> = [
    {
      title: '模型',
      dataIndex: 'name',
      render: (name: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong style={{ fontSize: 13 }}>{name}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>{record.provider}</Text>
        </Space>
      ),
    },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      ellipsis: true,
      render: (url: string) => <Text code style={{ fontSize: 12 }}>{url || '—'}</Text>,
    },
    {
      title: 'API Key',
      dataIndex: 'api_key_set',
      width: 120,
      render: (set: boolean, record) => (
        <Tooltip title={record.api_key_env ? `环境变量：${record.api_key_env}` : undefined}>
          {set
            ? <Badge status="success" text={<Text style={{ fontSize: 12 }}>已配置</Text>} />
            : <Badge status="error" text={<Text style={{ fontSize: 12 }}>未配置</Text>} />}
        </Tooltip>
      ),
    },
    {
      title: '使用的 Agent',
      dataIndex: 'agents',
      render: (agents: string[]) => (
        <Space size={4} wrap>
          {(agents ?? []).map((id) => (
            <Tag key={id} style={{ fontSize: 11, marginInlineEnd: 0 }}>{id}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '分配给 Agent',
      width: 200,
      render: (_, record) => (
        <Select
          size="small"
          placeholder="选择 Agent"
          style={{ width: 170 }}
          showSearch
          options={agentOptions}
          loading={assigningKey?.startsWith(`${keyOf(record)}->`) ?? false}
          onChange={(agentId: string) => void assignToAgent(record, agentId)}
        />
      ),
    },
    {
      title: '连接测试',
      width: 180,
      align: 'center',
      render: (_, record) => {
        const key = keyOf(record);
        const state = testStates[key] ?? 'idle';
        return (
          <Space size={6}>
            <Button
              size="small"
              icon={<ExperimentOutlined />}
              loading={state === 'testing'}
              onClick={() => void testModel(record)}
            >
              测试
            </Button>
            {state === 'ok' && <CheckCircleOutlined style={{ color: '#52c41a' }} />}
            {state === 'warning' && (
              <Tooltip title={testErrors[key]}>
                <CloseCircleOutlined style={{ color: '#faad14' }} />
              </Tooltip>
            )}
            {state === 'failed' && (
              <Tooltip title={testErrors[key]}>
                <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
              </Tooltip>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Card
        styles={{ body: { padding: 0 } }}
        title={
          <Space>
            <ApiOutlined style={{ color: ORANGE }} />
            <span>模型管理</span>
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              汇总所有 Agent 使用的模型配置
            </Text>
          </Space>
        }
      >
        <Table<ModelItem>
          rowKey={(m) => keyOf(m)}
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={models}
          pagination={false}
          locale={{ emptyText: '暂无模型配置' }}
        />
      </Card>
    </div>
  );
}
