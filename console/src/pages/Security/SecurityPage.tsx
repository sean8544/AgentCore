import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Form,
  Select,
  Space,
  Switch,
  Typography,
  message,
} from 'antd';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { extractErrorMessage } from '../../utils/helpers';

const { Title, Text } = Typography;

interface SecuritySettings {
  approval: {
    enabled: boolean;
    tools: string[];
  };
}

export default function SecurityPage() {
  const { t } = useI18n();
  const [form] = Form.useForm<{ enabled: boolean; tools: string[] }>();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const toolOptions = [
    { value: 'delete', label: t('security.toolDelete') },
    { value: 'write_file', label: t('security.toolWriteFile') },
    { value: 'edit_file', label: t('security.toolEditFile') },
    { value: 'execute', label: t('security.toolExecute') },
  ];

  useEffect(() => {
    setLoading(true);
    apiClient
      .get('/security/settings')
      .then((resp) => {
        const data = resp.data as SecuritySettings;
        form.setFieldsValue({
          enabled: data.approval?.enabled ?? false,
          tools: data.approval?.tools ?? [],
        });
      })
      .catch((error) =>
        message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`),
      )
      .finally(() => setLoading(false));
  }, [form, t]);

  const handleSave = async () => {
    let values: { enabled: boolean; tools: string[] };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    try {
      await apiClient.put('/security/settings', {
        approval: { enabled: values.enabled, tools: values.tools ?? [] },
      });
      message.success(t('security.saveSuccess'));
    } catch (error) {
      message.error(`${t('common.operationFailed')}：${extractErrorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <Card loading={loading}>
        <Space align="center" style={{ marginBottom: 4 }}>
          <SafetyCertificateOutlined style={{ fontSize: 20, color: '#1677ff' }} />
          <Title level={3} style={{ margin: 0 }}>
            {t('security.title')}
          </Title>
        </Space>
        <Text type="secondary">{t('security.subtitle')}</Text>

        <Form form={form} layout="vertical" style={{ marginTop: 24, maxWidth: 560 }}>
          <Form.Item label={t('security.approvalCard')}>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
              {t('security.approvalDesc')}
            </Text>
            <Form.Item name="enabled" valuePropName="checked" noStyle>
              <Switch checkedChildren="ON" unCheckedChildren="OFF" />
            </Form.Item>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('security.enableApprovalHelp')}
              </Text>
            </div>
          </Form.Item>

          <Form.Item name="tools" label={t('security.approvalTools')}>
            <Select
              mode="multiple"
              allowClear
              placeholder={t('security.approvalToolsPlaceholder')}
              options={toolOptions}
            />
          </Form.Item>

          <Alert
            type="info"
            showIcon
            message={t('security.globalNote')}
            style={{ marginBottom: 16 }}
          />

          <Button type="primary" onClick={handleSave} loading={saving}>
            {t('security.save')}
          </Button>
        </Form>
      </Card>
    </div>
  );
}
