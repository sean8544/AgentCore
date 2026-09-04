import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Space, Statistic, Table, Tag, Typography } from 'antd';
import {
  BarChartOutlined,
  CommentOutlined,
  MessageOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

/* ───────── Types ───────── */
interface AgentStat {
  agent_id: string;
  state: string | null;
  loaded: boolean;
  session_count: number;
  message_count: number;
  skills_count: number;
}

interface StatsData {
  agent_count: number;
  running_count: number;
  session_count: number;
  message_count: number;
  agents: AgentStat[];
}

// 后端状态模型只有 idle / busy（请求驱动的派生事实）；未装载的 agent 由下方 fallback 展示。
const STATE_LABELS: Record<string, { textKey: string; color: string }> = {
  busy: { textKey: 'stats.busy', color: 'processing' },
  idle: { textKey: 'stats.idle', color: 'success' },
};

export default function AgentStatsPage() {
  const { t } = useI18n();
  const [data, setData] = useState<StatsData | null>(null);
  const [loading, setLoading] = useState(false);

  const loadStats = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get('/stats');
      setData(res.data);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const columns: ColumnsType<AgentStat> = [
    {
      title: 'Agent',
      dataIndex: 'agent_id',
      render: (id: string, record) => (
        <Space>
          <RobotOutlined style={{ color: 'var(--google-primary)' }} />
          <Text strong>{id}</Text>
          {record.loaded && (
            <Tag color="warning" style={{ fontSize: 11 }}>
              {t('stats.loaded')}
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: t('common.status'),
      dataIndex: 'state',
      width: 120,
      render: (state: string | null, record) => {
        if (state && STATE_LABELS[state]) {
          const info = STATE_LABELS[state];
          return <Badge status={info.color as 'success'} text={t(info.textKey)} />;
        }
        return record.loaded
          ? <Badge status="success" text={t('stats.ready')} />
          : <Badge status="default" text={t('stats.unloaded')} />;
      },
    },
    { title: t('stats.sessionCount'), dataIndex: 'session_count', width: 110, align: 'right' },
    { title: t('stats.messageCount'), dataIndex: 'message_count', width: 110, align: 'right' },
    { title: t('stats.skillCount'), dataIndex: 'skills_count', width: 110, align: 'right' },
  ];

  return (
    <div>
      <GooglePageHeader
        icon={<BarChartOutlined />}
        title={t('stats.title')}
        extra={
          <Button
            size="small"
            icon={<ReloadOutlined spin={loading} />}
            onClick={() => void loadStats()}
          >
            {t('common.refresh')}
          </Button>
        }
      />

      {/* ── Summary cards ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: 'var(--google-space-6)',
          marginBottom: 'var(--google-space-8)',
        }}
      >
        <GoogleCard size="small" loading={loading && !data}>
          <Statistic
            title={t('stats.totalAgents')}
            value={data?.agent_count ?? 0}
            prefix={<RobotOutlined />}
            valueStyle={{ color: 'var(--google-primary)', fontSize: 28 }}
          />
        </GoogleCard>
        <GoogleCard size="small" loading={loading && !data}>
          <Statistic
            title={t('stats.runningLoaded')}
            value={data?.running_count ?? 0}
            prefix={<PlayCircleOutlined />}
            valueStyle={{ fontSize: 28 }}
          />
        </GoogleCard>
        <GoogleCard size="small" loading={loading && !data}>
          <Statistic
            title={t('stats.totalSessions')}
            value={data?.session_count ?? 0}
            prefix={<MessageOutlined />}
            valueStyle={{ fontSize: 28 }}
          />
        </GoogleCard>
        <GoogleCard size="small" loading={loading && !data}>
          <Statistic
            title={t('stats.totalMessages')}
            value={data?.message_count ?? 0}
            prefix={<CommentOutlined />}
            valueStyle={{ fontSize: 28 }}
          />
        </GoogleCard>
      </div>

      {/* ── Per-agent breakdown ── */}
      <GoogleCard
        title={<span>{t('stats.agentDetail')}</span>}
        bodyStyle={{ padding: 0 }}
        extra={
          <Button
            size="small"
            icon={<ReloadOutlined spin={loading} />}
            onClick={() => void loadStats()}
          >
            {t('common.refresh')}
          </Button>
        }
      >
        <Table<AgentStat>
          rowKey="agent_id"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={data?.agents ?? []}
          pagination={false}
        />
      </GoogleCard>
    </div>
  );
}
