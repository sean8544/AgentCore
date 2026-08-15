import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import {
  MessageOutlined,
  RobotOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  CommentOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';

const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';

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

const STATE_LABELS: Record<string, { textKey: string; color: string }> = {
  running: { textKey: 'stats.running', color: 'success' },
  idle: { textKey: 'stats.idle', color: 'processing' },
  started: { textKey: 'stats.started', color: 'success' },
  stopped: { textKey: 'stats.stopped', color: 'default' },
  error: { textKey: 'stats.error', color: 'error' },
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
          <RobotOutlined style={{ color: ORANGE }} />
          <Text strong>{id}</Text>
          {record.loaded && (
            <Tag style={{ fontSize: 11, color: ORANGE, borderColor: '#ffd8b3', background: '#fff7ef' }}>
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
    <div style={{ padding: 24 }}>
      {/* ── Summary cards ── */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small" loading={loading && !data}>
            <Statistic
              title={t('stats.totalAgents')}
              value={data?.agent_count ?? 0}
              prefix={<RobotOutlined />}
              valueStyle={{ color: ORANGE, fontSize: 28 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" loading={loading && !data}>
            <Statistic
              title={t('stats.runningLoaded')}
              value={data?.running_count ?? 0}
              prefix={<PlayCircleOutlined />}
              valueStyle={{ fontSize: 28 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" loading={loading && !data}>
            <Statistic
              title={t('stats.totalSessions')}
              value={data?.session_count ?? 0}
              prefix={<MessageOutlined />}
              valueStyle={{ fontSize: 28 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" loading={loading && !data}>
            <Statistic
              title={t('stats.totalMessages')}
              value={data?.message_count ?? 0}
              prefix={<CommentOutlined />}
              valueStyle={{ fontSize: 28 }}
            />
          </Card>
        </Col>
      </Row>

      {/* ── Per-agent breakdown ── */}
      <Card
        title={<span>{t('stats.agentDetail')}</span>}
        styles={{ body: { padding: 0 } }}
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
      </Card>
    </div>
  );
}
