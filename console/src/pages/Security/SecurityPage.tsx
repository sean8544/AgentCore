import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Select,
  Switch,
  Typography,
  message,
} from 'antd';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';
import { extractErrorMessage } from '../../utils/helpers';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Text } = Typography;

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
    <div>
      <GooglePageHeader
        icon={<SafetyCertificateOutlined />}
        title={t('security.title')}
        subtitle={t('security.subtitle')}
      />

      <GoogleCard loading={loading}>
        <Form form={form} layout="vertical" style={{ marginTop: 'var(--google-space-8)', maxWidth: 560 }}>
          <Form.Item label={t('security.approvalCard')}>
            <Text style={{ display: 'block', marginBottom: 'var(--google-space-3)', color: 'var(--google-muted-foreground)' }}>
              {t('security.approvalDesc')}
            </Text>
            <Form.Item name="enabled" valuePropName="checked" noStyle>
              <Switch checkedChildren="ON" unCheckedChildren="OFF" />
            </Form.Item>
            <div style={{ marginTop: 'var(--google-space-2)' }}>
              <Text style={{ fontSize: 12, color: 'var(--google-muted-foreground)' }}>
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
            style={{ marginBottom: 'var(--google-space-4)' }}
          />

          <Button type="primary" onClick={handleSave} loading={saving}>
            {t('security.save')}
          </Button>
        </Form>
      </GoogleCard>
    </div>
  );
}
