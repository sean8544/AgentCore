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
  Tag,
  Typography,
} from 'antd';
import { DashboardOutlined, ThunderboltOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';

const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';

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

interface UsageData {
  days: number;
  totals: { input_tokens: number; output_tokens: number; total_tokens: number; requests: number };
  daily: DailyItem[];
  by_agent: AgentItem[];
}

function formatNum(n: number): string {
  return n.toLocaleString('zh-CN');
}

export default function TokenUsagePage() {
  const { t } = useI18n();
  const [agents, setAgents] = useState<{ agent_id: string }[]>([]);
  const [agentFilter, setAgentFilter] = useState<string | undefined>(undefined);
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(false);

  /* ── Agent list ── */
  useEffect(() => {
    apiClient
      .get('/agents')
      .then((res) => setAgents(Array.isArray(res.data) ? res.data : []))
      .catch(() => { /* ignore */ });
  }, []);

  /* ── Load usage ── */
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
      render: (v: number) => <Text strong style={{ color: ORANGE }}>{formatNum(v)}</Text>,
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

  return (
    <div style={{ padding: 24 }}>
      {/* ── Header / filters ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <Space>
          <DashboardOutlined style={{ color: ORANGE, fontSize: 16 }} />
          <Text strong style={{ fontSize: 15 }}>{t('tokenUsage.title')}</Text>
        </Space>
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
      </div>

      {/* ── Summary cards ── */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('tokenUsage.totalToken')} value={data?.totals.total_tokens ?? 0}
              valueStyle={{ color: ORANGE, fontSize: 24 }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('tokenUsage.inputToken')} value={data?.totals.input_tokens ?? 0} valueStyle={{ fontSize: 24 }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('tokenUsage.outputToken')} value={data?.totals.output_tokens ?? 0} valueStyle={{ fontSize: 24 }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title={t('tokenUsage.requestCount')} value={data?.totals.requests ?? 0} valueStyle={{ fontSize: 24 }} />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        {/* ── Daily trend (CSS bars) ── */}
        <Col span={12}>
          <Card
            title={<Space><ThunderboltOutlined style={{ color: ORANGE }} /><span>{t('tokenUsage.dailyTrend')}</span></Space>}
            size="small"
            styles={{ body: { minHeight: 220 } }}
          >
            {daily.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? t('tokenUsage.noData') : t('tokenUsage.noDataAvailable')} />
            ) : (
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, height: 200, padding: '8px 4px 0' }}>
                {daily.map((d) => (
                  <div key={d.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, minWidth: 0 }}>
                    <Text style={{ fontSize: 11, color: ORANGE }}>{formatNum(d.total_tokens)}</Text>
                    <div style={{
                      width: '70%', maxWidth: 40,
                      height: Math.max(4, Math.round((d.total_tokens / maxTotal) * 130)),
                      background: `linear-gradient(180deg, ${ORANGE}, #ffb473)`,
                      borderRadius: '4px 4px 0 0',
                    }} />
                    <Text type="secondary" style={{ fontSize: 10, transform: 'rotate(0deg)', whiteSpace: 'nowrap' }}>
                      {d.date.slice(5)}
                    </Text>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </Col>

        {/* ── Per-agent breakdown ── */}
        <Col span={12}>
          <Card title={<span>{t('tokenUsage.agentBreakdown')}</span>} size="small" styles={{ body: { padding: 0 } }}>
            <Table<AgentItem>
              rowKey="agent_id"
              size="small"
              loading={loading}
              columns={agentColumns}
              dataSource={data?.by_agent ?? []}
              pagination={false}
            />
          </Card>
        </Col>
      </Row>

      {/* ── Daily table ── */}
      <Card title={<span>{t('tokenUsage.dailyDetail')}</span>} size="small" style={{ marginTop: 16 }} styles={{ body: { padding: 0 } }}>
        <Table<DailyItem>
          rowKey="date"
          size="small"
          loading={loading}
          columns={dailyColumns}
          dataSource={[...daily].reverse()}
          pagination={false}
        />
      </Card>
    </div>
  );
}
