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
import { useI18n } from '../../i18n';

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
  const { t } = useI18n();
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
      antdMessage.error(t('envs.loadFailed'));
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
      antdMessage.warning(t('envs.valueRequired'));
      return;
    }

    setSaving(true);
    try {
      // 编辑时若未输入新值，发送 "***" 让后端保留原值。
      const payload: Record<string, string> = {
        [key]: editingKey && value === '' ? '***' : value,
      };
      await apiClient.put('/envs', payload);
      antdMessage.success(editingKey ? t('envs.updated') : t('envs.addedSuccess'));
      setModalOpen(false);
      void loadEnvs();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail ?? t('envs.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  /* ── Delete ── */
  const handleDelete = async (key: string) => {
    try {
      await apiClient.delete(`/envs/${encodeURIComponent(key)}`);
      antdMessage.success(t('envs.deletedSuccess', { key }));
      void loadEnvs();
    } catch {
      antdMessage.error(t('envs.deleteFailed'));
    }
  };

  const columns: ColumnsType<EnvItem> = [
    {
      title: t('envs.variableName'),
      dataIndex: 'key',
      width: 320,
      render: (key: string) => (
        <Text code style={{ fontSize: 13 }}>{key}</Text>
      ),
    },
    {
      title: t('envs.value'),
      dataIndex: 'value',
      render: () => (
        <Text type="secondary" style={{ letterSpacing: 2 }}>••••••••</Text>
      ),
    },
    {
      title: t('common.actions'),
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
            title={t('envs.deleteConfirm', { key: record.key })}
            onConfirm={() => void handleDelete(record.key)}
            okText={t('common.delete')}
            cancelText={t('common.cancel')}
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
            <span>{t('envs.title')}</span>
            <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {t('envs.subtitle')}
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
            {t('envs.addVariable')}
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
          locale={{ emptyText: t('envs.emptyHint') }}
        />
      </Card>

      <Modal
        title={editingKey ? t('envs.editTitle', { key: editingKey }) : t('envs.addTitle')}
        open={modalOpen}
        onOk={() => void handleSave()}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        okText={t('common.save')}
        cancelText={t('common.cancel')}
        okButtonProps={{ style: { background: ORANGE, borderColor: ORANGE } }}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="key"
            label={t('envs.variableName')}
            rules={[
              { required: true, message: t('envs.nameRequired') },
              { pattern: /^[A-Za-z_][A-Za-z0-9_]*$/, message: t('envs.namePattern') },
            ]}
          >
            <Input placeholder={t('envs.nameExample')} disabled={!!editingKey} />
          </Form.Item>
          <Form.Item
            name="value"
            label={t('envs.value')}
            extra={editingKey ? t('envs.keepOriginal') : undefined}
          >
            <Input.Password placeholder={editingKey ? t('envs.enterNewValue') : t('envs.enterValue')} autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
