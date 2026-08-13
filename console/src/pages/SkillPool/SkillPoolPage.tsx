import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message as antdMessage,
} from 'antd';
import {
  AppstoreAddOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  RocketOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';
import { useAgentId } from '../../stores/agentStore';

const { Text, Paragraph } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';

/* ───────── Types ───────── */
interface SkillItem {
  name: string;
  dir_name: string;
  description: string;
}

interface SkillDetail extends SkillItem {
  content: string;
}

export default function SkillPoolPage() {
  const [poolSkills, setPoolSkills] = useState<SkillItem[]>([]);
  const [poolLoading, setPoolLoading] = useState(false);

  const agentId = useAgentId();
  const [agentSkills, setAgentSkills] = useState<SkillItem[]>([]);
  const [agentSkillsLoading, setAgentSkillsLoading] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [installing, setInstalling] = useState<string | null>(null);
  const [form] = Form.useForm();

  /* ── Load pool ── */
  const loadPool = useCallback(async () => {
    setPoolLoading(true);
    try {
      const res = await apiClient.get('/skills/pool');
      setPoolSkills(res.data.skills ?? []);
    } catch {
      setPoolSkills([]);
      antdMessage.error('技能池加载失败');
    } finally {
      setPoolLoading(false);
    }
  }, []);

  /* ── Load agent skills ── */
  const loadAgentSkills = useCallback(async () => {
    setAgentSkillsLoading(true);
    try {
      const res = await apiClient.get(`/skills/agents/${agentId}`);
      setAgentSkills(res.data.skills ?? []);
    } catch {
      setAgentSkills([]);
    } finally {
      setAgentSkillsLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void loadPool();
  }, [loadPool]);

  useEffect(() => {
    void loadAgentSkills();
  }, [loadAgentSkills]);

  /* ── Create skill ── */
  const submitCreate = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      await apiClient.post('/skills/pool', values);
      antdMessage.success(`技能 ${values.name} 已创建`);
      setCreateOpen(false);
      form.resetFields();
      void loadPool();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail || '创建失败，请重试');
    } finally {
      setSaving(false);
    }
  }, [form, loadPool]);

  /* ── View detail ── */
  const viewDetail = useCallback(async (name: string) => {
    setDetailLoading(true);
    setDetail(null);
    try {
      const res = await apiClient.get(`/skills/pool/${name}`);
      setDetail(res.data);
    } catch {
      antdMessage.error('技能内容加载失败');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  /* ── Delete from pool ── */
  const deleteSkill = useCallback(
    async (name: string) => {
      try {
        await apiClient.delete(`/skills/pool/${name}`);
        antdMessage.success(`已删除技能 ${name}`);
        void loadPool();
      } catch {
        antdMessage.error('删除失败，请重试');
      }
    },
    [loadPool],
  );

  /* ── Install to agent ── */
  const installSkill = useCallback(
    async (name: string) => {
      setInstalling(name);
      try {
        await apiClient.post(`/skills/pool/${name}/install/${agentId}`);
        antdMessage.success(`已安装 ${name} 到 ${agentId}`);
        void loadAgentSkills();
      } catch (err: unknown) {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        antdMessage.error(detail || '安装失败，请重试');
      } finally {
        setInstalling(null);
      }
    },
    [agentId, loadAgentSkills],
  );

  /* ── Uninstall from agent ── */
  const uninstallSkill = useCallback(
    async (name: string) => {
      try {
        await apiClient.delete(`/skills/agents/${agentId}/${name}`);
        antdMessage.success(`已从 ${agentId} 卸载 ${name}`);
        void loadAgentSkills();
      } catch {
        antdMessage.error('卸载失败，请重试');
      }
    },
    [agentId, loadAgentSkills],
  );

  const installedNames = new Set(agentSkills.map((s) => s.dir_name));

  const agentSkillColumns: ColumnsType<SkillItem> = [
    {
      title: '技能',
      dataIndex: 'name',
      width: 180,
      render: (name: string, record) => (
        <Space>
          <ThunderboltOutlined style={{ color: ORANGE }} />
          <Text code style={{ fontSize: 13 }}>{record.dir_name}</Text>
          {name !== record.dir_name && (
            <Text type="secondary" style={{ fontSize: 12 }}>{name}</Text>
          )}
        </Space>
      ),
    },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    {
      title: '操作',
      width: 90,
      render: (_: unknown, record) => (
        <Popconfirm
          title={`从 ${agentId} 卸载 ${record.dir_name}？`}
          onConfirm={() => void uninstallSkill(record.dir_name)}
        >
          <Button size="small" type="link" danger icon={<DeleteOutlined />}>
            卸载
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ── Skill pool ── */}
      <Card
        title={
          <Space>
            <RocketOutlined style={{ color: ORANGE }} />
            <span>全局技能池</span>
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {poolSkills.length} 个技能 · 安装后复制进 Agent 的 skills/ 目录
            </Text>
          </Space>
        }
        extra={
          <Button
            size="small"
            type="primary"
            icon={<AppstoreAddOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            创建技能
          </Button>
        }
      >
        <Spin spinning={poolLoading}>
          {poolSkills.length === 0 && !poolLoading ? (
            <Empty description="技能池为空" />
          ) : (
            <Row gutter={[16, 16]}>
              {poolSkills.map((skill) => (
                <Col xs={24} sm={12} lg={8} key={skill.dir_name}>
                  <Card
                    size="small"
                    hoverable
                    styles={{ body: { padding: 16 } }}
                    style={{
                      borderColor: installedNames.has(skill.dir_name) ? '#ffd8b3' : undefined,
                      background: installedNames.has(skill.dir_name) ? '#fffaf4' : undefined,
                    }}
                  >
                    <Space direction="vertical" size={6} style={{ width: '100%' }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Text code strong style={{ fontSize: 13 }}>{skill.dir_name}</Text>
                        {installedNames.has(skill.dir_name) && (
                          <Tag style={{ fontSize: 11, color: ORANGE, borderColor: '#ffd8b3', background: '#fff7ef' }}>
                            已安装
                          </Tag>
                        )}
                      </Space>
                      <Paragraph
                        type="secondary"
                        style={{ fontSize: 12, marginBottom: 0 }}
                        ellipsis={{ rows: 2, tooltip: skill.description }}
                      >
                        {skill.description || '暂无描述'}
                      </Paragraph>
                      <Space size={4}>
                        <Button
                          size="small"
                          type="link"
                          icon={<EyeOutlined />}
                          onClick={() => void viewDetail(skill.dir_name)}
                        >
                          查看
                        </Button>
                        <Button
                          size="small"
                          type="link"
                          icon={<DownloadOutlined />}
                          loading={installing === skill.dir_name}
                          onClick={() => void installSkill(skill.dir_name)}
                        >
                          安装到 {agentId}
                        </Button>
                        <Popconfirm
                          title={`从技能池删除 ${skill.dir_name}？已安装的副本不受影响。`}
                          onConfirm={() => void deleteSkill(skill.dir_name)}
                        >
                          <Button size="small" type="link" danger icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </Space>
                    </Space>
                  </Card>
                </Col>
              ))}
            </Row>
          )}
        </Spin>
      </Card>

      {/* ── Installed skills ── */}
      <Card
        styles={{ body: { padding: 0 } }}
        title={
          <Space>
            <ThunderboltOutlined style={{ color: ORANGE }} />
            <span>已安装技能</span>
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {agentSkills.length} 个技能
            </Text>
          </Space>
        }
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            当前 Agent：<Text code style={{ fontSize: 12 }}>{agentId}</Text>
          </Text>
        }
      >
        <Table<SkillItem>
          rowKey="dir_name"
          size="middle"
          loading={agentSkillsLoading}
          columns={agentSkillColumns}
          dataSource={agentSkills}
          pagination={false}
          locale={{
            emptyText: (
              <Text type="secondary">
                {agentId} 尚未安装技能，从上方技能池选择安装
              </Text>
            ),
          }}
        />
      </Card>

      {/* ── Create modal ── */}
      <Modal
        title="创建技能"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void submitCreate()}
        confirmLoading={saving}
        okText="创建"
        cancelText="取消"
        width={640}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            label="技能名"
            name="name"
            rules={[
              { required: true, message: '请输入技能名' },
              {
                pattern: /^[a-z0-9][a-z0-9-]{0,63}$/,
                message: '仅支持小写字母、数字和连字符',
              },
            ]}
          >
            <Input placeholder="例如 code-review" />
          </Form.Item>
          <Form.Item
            label="描述"
            name="description"
            rules={[{ required: true, message: '请输入技能描述（用于触发判断）' }]}
          >
            <Input.TextArea rows={2} placeholder="简述技能的用途与触发场景" />
          </Form.Item>
          <Form.Item label="技能内容（Markdown）" name="content">
            <Input.TextArea rows={8} placeholder={'# 技能标题\n\n## 何时使用\n\n…'} />
          </Form.Item>
        </Form>
      </Modal>

      {/* ── Detail modal ── */}
      <Modal
        title={detail ? `技能：${detail.dir_name}` : '技能详情'}
        open={detailLoading || detail !== null}
        onCancel={() => setDetail(null)}
        footer={null}
        width={640}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            <Spin />
          </div>
        ) : detail ? (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>{detail.description}</Text>
            <pre
              style={{
                background: '#fafafa',
                border: '1px solid #f0f0f0',
                borderRadius: 6,
                padding: 12,
                fontSize: 12,
                maxHeight: 400,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {detail.content}
            </pre>
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}
