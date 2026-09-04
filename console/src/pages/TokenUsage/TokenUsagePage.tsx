import { useCallback, useEffect, useState } from 'react';
import {
  Card,
  Col,
  Empty,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import {
  DashboardOutlined,
  ThunderboltOutlined,
  TeamOutlined,
  ApiOutlined,
  CalendarOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import GooglePageHeader from '../../components/GooglePageHeader';
import { getChartColors } from '../../styles/chart-colors';
import { useAppStore } from '../../stores/appStore';

const { Text } = Typography;

/* ───────── Types ───────── */
interface DailyItem {
  date: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  requests: number;
}

interface AgentItem {
  agent_id: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  requests: number;
}

interface ModelItem {
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  requests: number;
}

interface UsageData {
  days: number;
  totals: { input_tokens: number; output_tokens: number; total_tokens: number; requests: number };
  daily: DailyItem[];
  by_agent: AgentItem[];
  by_model: ModelItem[];
}

function formatNum(n: number): string {
  return n.toLocaleString('zh-CN');
}

export default function TokenUsagePage() {
  const { t } = useI18n();
  const resolvedTheme = useAppStore((s) => s.resolvedTheme);
  const [agents, setAgents] = useState<{ agent_id: string }[]>([]);
  const [agentFilter, setAgentFilter] = useState<string | undefined>(undefined);
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<string>('overview');

  const chartColors = getChartColors(resolvedTheme === 'dark');
  const primaryColor = chartColors[0];

  /* ── Agent list ── */
  useEffect(() => {
    apiClient
      .get('/agents')
      .then((res) => setAgents(Array.isArray(res.data) ? res.data : []))
      .catch(() => { /* ignore */ });
  }, []);

  /* ─ Load usage ── */
  const loadUsage = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get('/token-usage', {
        params: { days, ...(agentFilter ? { agent_id: agentFilter } : {}) },
      });
      setData(res.data);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [agentFilter, days]);

  useEffect(() => {
    void loadUsage();
  }, [loadUsage]);

  const daily = data?.daily ?? [];
  const maxTotal = Math.max(1, ...daily.map((d) => d.total_tokens));

  const dailyColumns: ColumnsType<DailyItem> = [
    { title: t('tokenUsage.date'), dataIndex: 'date', width: 140 },
    {
      title: t('tokenUsage.input'),
      dataIndex: 'input_tokens',
      align: 'right',
      render: (v: number) => formatNum(v),
    },
    {
      title: t('tokenUsage.output'),
      dataIndex: 'output_tokens',
      align: 'right',
      render: (v: number) => formatNum(v),
    },
    {
      title: t('tokenUsage.total'),
      dataIndex: 'total_tokens',
      align: 'right',
      render: (v: number) => <Text strong style={{ color: primaryColor }}>{formatNum(v)}</Text>,
    },
    { title: t('tokenUsage.requests'), dataIndex: 'requests', width: 90, align: 'right' },
  ];

  const agentColumns: ColumnsType<AgentItem> = [
    {
      title: 'Agent',
      dataIndex: 'agent_id',
      render: (id: string) => <Tag style={{ fontSize: 12 }}>{id}</Tag>,
    },
    { title: t('tokenUsage.input'), dataIndex: 'input_tokens', align: 'right', render: (v: number) => formatNum(v) },
    { title: t('tokenUsage.output'), dataIndex: 'output_tokens', align: 'right', render: (v: number) => formatNum(v) },
    {
      title: t('tokenUsage.total'),
      dataIndex: 'total_tokens',
      align: 'right',
      render: (v: number) => <Text strong>{formatNum(v)}</Text>,
    },
    { title: t('tokenUsage.requests'), dataIndex: 'requests', width: 90, align: 'right' },
  ];

  const modelColumns: ColumnsType<ModelItem> = [
    {
      title: 'Model',
      dataIndex: 'model',
      render: (m: string) => <Tag style={{ fontSize: 12 }}>{m}</Tag>,
    },
    { title: t('tokenUsage.input'), dataIndex: 'input_tokens', align: 'right', render: (v: number) => formatNum(v) },
    { title: t('tokenUsage.output'), dataIndex: 'output_tokens', align: 'right', render: (v: number) => formatNum(v) },
    {
      title: t('tokenUsage.total'),
      dataIndex: 'total_tokens',
      align: 'right',
      render: (v: number) => <Text strong>{formatNum(v)}</Text>,
    },
    { title: t('tokenUsage.requests'), dataIndex: 'requests', width: 90, align: 'right' },
  ];

  const headerExtra = (
    <Space>
      <Select
        size="small"
        allowClear
        placeholder={t('tokenUsage.allAgents')}
        value={agentFilter}
        onChange={(v) => setAgentFilter(v)}
        style={{ width: 160 }}
        options={agents.map((a) => ({ value: a.agent_id, label: a.agent_id }))}
      />
      <Segmented
        size="small"
        value={days}
        onChange={(v) => setDays(Number(v))}
        options={[
          { value: 7, label: t('tokenUsage.last7Days') },
          { value: 14, label: t('tokenUsage.last14Days') },
          { value: 30, label: t('tokenUsage.last30Days') },
        ]}
      />
    </Space>
  );

  const tabItems = [
    {
      key: 'overview',
      label: (
        <span>
          <DashboardOutlined />
          {t('tokenUsage.overview') || '概览'}
        </span>
      ),
      children: (
        <>
          {/* ── Summary cards ── */}
          <Row gutter={[24, 24]} style={{ marginBottom: 24 }}>
            <Col xs={24} sm={12} lg={6}>
              <Card>
                <Statistic
                  title={t('tokenUsage.totalToken')}
                  value={data?.totals.total_tokens ?? 0}
                  valueStyle={{ color: primaryColor, fontSize: 28 }}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} lg={6}>
              <Card>
                <Statistic
                  title={t('tokenUsage.inputToken')}
                  value={data?.totals.input_tokens ?? 0}
                  valueStyle={{ fontSize: 28 }}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} lg={6}>
              <Card>
                <Statistic
                  title={t('tokenUsage.outputToken')}
                  value={data?.totals.output_tokens ?? 0}
                  valueStyle={{ fontSize: 28 }}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} lg={6}>
              <Card>
                <Statistic
                  title={t('tokenUsage.requestCount')}
                  value={data?.totals.requests ?? 0}
                  valueStyle={{ fontSize: 28 }}
                />
              </Card>
            </Col>
          </Row>

          {/* ── Daily trend chart ── */}
          <Card
            title={
              <Space>
                <ThunderboltOutlined style={{ color: primaryColor }} />
                <span>{t('tokenUsage.dailyTrend')}</span>
              </Space>
            }
          >
            {daily.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? t('tokenUsage.noData') : t('tokenUsage.noDataAvailable')} />
            ) : (
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 16, height: 280, padding: '24px 16px 0' }}>
                {daily.map((d) => (
                  <div key={d.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, minWidth: 0 }}>
                    <Text style={{ fontSize: 13, color: primaryColor, fontWeight: 500 }}>{formatNum(d.total_tokens)}</Text>
                    <div style={{
                      width: '60%',
                      maxWidth: 60,
                      height: Math.max(8, Math.round((d.total_tokens / maxTotal) * 200)),
                      background: `linear-gradient(180deg, ${primaryColor} 0%, ${primaryColor}88 100%)`,
                      borderRadius: '6px 6px 0 0',
                      transition: 'height 0.3s ease',
                    }} />
                    <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                      {d.date.slice(5)}
                    </Text>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      ),
    },
    {
      key: 'agents',
      label: (
        <span>
          <TeamOutlined />
          {t('tokenUsage.agentBreakdown')}
        </span>
      ),
      children: (
        <Card bodyStyle={{ padding: 0 }}>
          <Table<AgentItem>
            rowKey="agent_id"
            size="middle"
            loading={loading}
            columns={agentColumns}
            dataSource={data?.by_agent ?? []}
            pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 个 Agent` }}
          />
        </Card>
      ),
    },
    {
      key: 'models',
      label: (
        <span>
          <ApiOutlined />
          {t('tokenUsage.modelBreakdown')}
        </span>
      ),
      children: (
        <Card bodyStyle={{ padding: 0 }}>
          <Table<ModelItem>
            rowKey="model"
            size="middle"
            loading={loading}
            columns={modelColumns}
            dataSource={data?.by_model ?? []}
            pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 个模型` }}
          />
        </Card>
      ),
    },
    {
      key: 'daily',
      label: (
        <span>
          <CalendarOutlined />
          {t('tokenUsage.dailyDetail')}
        </span>
      ),
      children: (
        <Card bodyStyle={{ padding: 0 }}>
          <Table<DailyItem>
            rowKey="date"
            size="middle"
            loading={loading}
            columns={dailyColumns}
            dataSource={[...daily].reverse()}
            pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 天` }}
          />
        </Card>
      ),
    },
  ];

  return (
    <div>
      <GooglePageHeader
        icon={<DashboardOutlined />}
        title={t('tokenUsage.title')}
        extra={headerExtra}
      />

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={tabItems}
        style={{ marginTop: 16 }}
      />
    </div>
  );
}
