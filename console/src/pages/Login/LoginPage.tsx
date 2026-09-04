import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Card, Form, Input, Typography, message } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { useI18n } from '../../i18n';
import { apiClient, setAuthToken } from '../../api/client';

interface AuthStatus {
  enabled: boolean;
  registered: boolean;
}

/**
 * Single-user opt-in auth page.
 *
 * Renders nothing when auth is disabled (immediate redirect to the app);
 * shows a one-time registration form when enabled but no account exists,
 * otherwise a plain login form.  Successful submit stores the bearer
 * token (picked up by the axios/fetch interceptors) and enters the app.
 */
export default function LoginPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<{ username: string; password: string; confirm?: string }>();

  useEffect(() => {
    apiClient
      .get<AuthStatus>('/auth/status')
      .then((resp) => {
        setStatus(resp.data);
        if (!resp.data.enabled) {
          // Auth disabled — the app is open, nothing to sign into.
          navigate('/', { replace: true });
        }
      })
      .catch(() => navigate('/', { replace: true }));
  }, [navigate]);

  const submit = async (values: { username: string; password: string; confirm?: string }) => {
    if (!status) return;
    const isRegister = !status.registered;
    if (isRegister) {
      if (values.password.length < 8) {
        message.error(t('auth.passwordTooShort'));
        return;
      }
      if (values.password !== values.confirm) {
        message.error(t('auth.passwordMismatch'));
        return;
      }
    }
    setLoading(true);
    try {
      const endpoint = isRegister ? '/auth/register' : '/auth/login';
      const resp = await apiClient.post<{ token: string; username: string }>(endpoint, {
        username: values.username,
        password: values.password,
      });
      setAuthToken(resp.data.token);
      navigate('/', { replace: true });
    } catch (err) {
      const errStatus = (err as { response?: { status?: number } })?.response?.status;
      if (errStatus === 401) message.error(t('auth.invalidCredentials'));
      else if (errStatus === 409) message.error(t('auth.accountExists'));
      else if (errStatus === 429) message.error(t('auth.tooManyAttempts'));
      else message.error(t(isRegister ? 'auth.registerFailed' : 'auth.loginFailed'));
    } finally {
      setLoading(false);
    }
  };

  const isRegister = status !== null && !status.registered;

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(180deg, #f0f6ff 0%, #ffffff 60%)',
        padding: 16,
      }}
    >
      <Card style={{ width: 380, borderColor: '#ebebeb' }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            {isRegister ? t('auth.registerTitle') : t('auth.title')}
          </Typography.Title>
          {isRegister && (
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {t('auth.registerHint')}
            </Typography.Text>
          )}
        </div>

        <Form form={form} layout="vertical" onFinish={submit} autoComplete="off">
          <Form.Item
            name="username"
            label={t('auth.username')}
            rules={[{ required: true, message: t('auth.usernamePlaceholder') }]}
          >
            <Input
              prefix={<UserOutlined />}
              placeholder={t('auth.usernamePlaceholder')}
              maxLength={32}
              autoFocus
            />
          </Form.Item>
          <Form.Item
            name="password"
            label={t('auth.password')}
            rules={[{ required: true, message: t('auth.passwordPlaceholder') }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder={t('auth.passwordPlaceholder')}
              maxLength={128}
            />
          </Form.Item>
          {isRegister && (
            <Form.Item
              name="confirm"
              label={t('auth.confirmPassword')}
              rules={[{ required: true, message: t('auth.confirmPasswordPlaceholder') }]}
            >
              <Input.Password placeholder={t('auth.confirmPasswordPlaceholder')} maxLength={128} />
            </Form.Item>
          )}
          <Form.Item style={{ marginBottom: 8 }}>
            <Button type="primary" htmlType="submit" block loading={loading}>
              {isRegister ? t('auth.register') : t('auth.login')}
            </Button>
          </Form.Item>
        </Form>

        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
          {t('auth.forgotHint')}
        </Typography.Text>
      </Card>
    </div>
  );
}
