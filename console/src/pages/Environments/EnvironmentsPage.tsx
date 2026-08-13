import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Typography,
  message as antdMessage,
} from 'antd';
import { DeleteOutlined, EditOutlined, KeyOutlined, PlusOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { apiClient } from '../../api/client';

const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';

/* ───────── Types ───────── */
interface EnvItem {
  key: string;
  value: string; // always masked ("***") from the backend
}

interface EnvFormValues {
  key: string;
  value: string;
}

export default function EnvironmentsPage() {
  const [envs, setEnvs] = useState<EnvItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<EnvFormValues>();

  /* ── Load ── */
  const loadEnvs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get('/envs');
      setEnvs(res.data.envs ?? []);
    } catch {
      setEnvs([]);
      antdMessage.error('环境变量加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadEnvs();
  }, [loadEnvs]);

  /* ── Add / Edit ── */
  const openAdd = () => {
    setEditingKey(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (key: string) => {
    setEditingKey(key);
    form.setFieldsValue({ key, value: '' });
    setModalOpen(true);
  };

  const handleSave = async () => {
    let values: EnvFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const key = values.key.trim();
    const value = values.value;

    if (!editingKey && value === '') {
      antdMessage.warning('新增变量必须填写值');
      return;
    }

    setSaving(true);
    try {
      // 编辑时若未输入新值，发送 "***" 让后端保留原值。
      const payload: Record<string, string> = {
        [key]: editingKey && value === '' ? '***' : value,
      };
      await apiClient.put('/envs', payload);
      antdMessage.success(editingKey ? '已更新' : '已添加');
      setModalOpen(false);
      void loadEnvs();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail ?? '保存失败');
    } finally {
      setSaving(false);
    }
  };

  /* ── Delete ── */
  const handleDelete = async (key: string) => {
    try {
      await apiClient.delete(`/envs/${encodeURIComponent(key)}`);
      antdMessage.success(`已删除 ${key}`);
      void loadEnvs();
    } catch {
      antdMessage.error('删除失败');
    }
  };

  const columns: ColumnsType<EnvItem> = [
    {
      title: '变量名',
      dataIndex: 'key',
      width: 320,
      render: (key: string) => (
        <Text code style={{ fontSize: 13 }}>{key}</Text>
      ),
    },
    {
      title: '值',
      dataIndex: 'value',
      render: () => (
        <Text type="secondary" style={{ letterSpacing: 2 }}>••••••••</Text>
      ),
    },
    {
      title: '操作',
      width: 140,
      align: 'center',
      render: (_, record) => (
        <Space size="small">
          <Button
            type="text"
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(record.key)}
          />
          <Popconfirm
            title={`删除 ${record.key}？`}
            onConfirm={() => void handleDelete(record.key)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
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
            <KeyOutlined style={{ color: ORANGE }} />
            <span>环境变量</span>
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              值始终脱敏显示 · 保存后立即生效
            </Text>
          </Space>
        }
        extra={
          <Button
            size="small"
            type="primary"
            icon={<PlusOutlined />}
            onClick={openAdd}
            style={{ background: ORANGE, borderColor: ORANGE }}
          >
            添加变量
          </Button>
        }
      >
        <Table<EnvItem>
          rowKey="key"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={envs}
          pagination={false}
          locale={{ emptyText: '暂无环境变量，点击右上角添加（如 AGENTCORE_LLM_API_KEY）' }}
        />
      </Card>

      <Modal
        title={editingKey ? `编辑 ${editingKey}` : '添加环境变量'}
        open={modalOpen}
        onOk={() => void handleSave()}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ style: { background: ORANGE, borderColor: ORANGE } }}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="key"
            label="变量名"
            rules={[
              { required: true, message: '请输入变量名' },
              { pattern: /^[A-Za-z_][A-Za-z0-9_]*$/, message: '仅支持字母、数字和下划线，且不能以数字开头' },
            ]}
          >
            <Input placeholder="例如 AGENTCORE_LLM_API_KEY" disabled={!!editingKey} />
          </Form.Item>
          <Form.Item
            name="value"
            label="值"
            extra={editingKey ? '留空表示保持原值不变' : undefined}
          >
            <Input.Password placeholder={editingKey ? '输入新值以覆盖' : '请输入变量值'} autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
