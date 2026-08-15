import { Layout, Menu, Select, theme } from 'antd';
import {
  MessageOutlined,
  MailOutlined,
  WifiOutlined,
  ClockCircleOutlined,
  HeartOutlined,
  FileOutlined,
  ThunderboltOutlined,
  ToolOutlined,
  ApiOutlined,
  CloudOutlined,
  SettingOutlined,
  BarChartOutlined,
  RobotOutlined,
  CodeOutlined,
  AppstoreOutlined,
  EnvironmentOutlined,
  SafetyOutlined,
  DashboardOutlined,
  SaveOutlined,
  AudioOutlined,
  BugOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  GlobalOutlined,
} from '@ant-design/icons';
import { useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useAppStore } from '../stores/appStore';
import { useAgentStore } from '../stores/agentStore';
import { useI18n, LOCALE_LABELS, type Locale } from '../i18n';
import AgentSelector from '../components/AgentSelector';

const { Header, Sider, Content } = Layout;

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { sidebarCollapsed, toggleSidebar } = useAppStore();
  const refreshAgents = useAgentStore((s) => s.refreshAgents);
  const selectedAgent = useAgentStore((s) => s.selectedAgent);
  const { token: { colorBgContainer, borderRadiusLG } } = theme.useToken();
  const { t, locale, setLocale } = useI18n();

  const menuItems = [
    {
      key: '/chat',
      icon: <MessageOutlined />,
      label: t('menu.chat'),
    },
    {
      key: '/inbox',
      icon: <MailOutlined />,
      label: t('menu.inbox'),
    },
    {
      key: 'control-group',
      label: t('menu.control'),
      type: 'group' as const,
      children: [
        { key: '/channels', icon: <WifiOutlined />, label: t('menu.channels') },
        { key: '/sessions', icon: <MessageOutlined />, label: t('menu.sessions') },
        { key: '/cron-jobs', icon: <ClockCircleOutlined />, label: t('menu.cronJobs') },
        { key: '/heartbeat', icon: <HeartOutlined />, label: t('menu.heartbeat') },
      ],
    },
    {
      key: 'workspace-group',
      label: t('menu.workspace'),
      type: 'group' as const,
      children: [
        { key: '/files', icon: <FileOutlined />, label: t('menu.files') },
        { key: '/skills', icon: <ThunderboltOutlined />, label: t('menu.skills') },
        { key: '/tools', icon: <ToolOutlined />, label: t('menu.tools') },
        { key: '/mcp', icon: <ApiOutlined />, label: 'MCP' },
        { key: '/acp', icon: <CloudOutlined />, label: 'ACP' },
        { key: '/agent-config', icon: <SettingOutlined />, label: t('menu.agentConfig') },
        { key: '/agent-stats', icon: <BarChartOutlined />, label: t('menu.agentStats') },
      ],
    },
    {
      key: 'settings-group',
      label: t('menu.settings'),
      type: 'group' as const,
      children: [
        { key: '/agents', icon: <RobotOutlined />, label: t('menu.agents') },
        { key: '/models', icon: <CodeOutlined />, label: t('menu.models') },
        { key: '/skill-pool', icon: <AppstoreOutlined />, label: t('menu.skillPool') },
        { key: '/environments', icon: <EnvironmentOutlined />, label: t('menu.environments') },
        { key: '/security', icon: <SafetyOutlined />, label: t('menu.security') },
        { key: '/token-usage', icon: <DashboardOutlined />, label: t('menu.tokenUsage') },
        { key: '/backups', icon: <SaveOutlined />, label: t('menu.backups') },
        { key: '/voice', icon: <AudioOutlined />, label: t('menu.voice') },
        { key: '/debug', icon: <BugOutlined />, label: t('menu.debug') },
      ],
    },
  ];

  useEffect(() => {
    void refreshAgents();
  }, [refreshAgents]);

  return (
    <Layout style={{ height: '100%' }}>
      <Sider
        width={220}
        trigger={null}
        collapsible
        collapsed={sidebarCollapsed}
        style={{
          overflow: 'auto',
          height: '100vh',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
        }}
      >
        <div style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
          fontSize: sidebarCollapsed ? 14 : 18,
          fontWeight: 600,
          letterSpacing: 1,
          borderBottom: '1px solid rgba(255,255,255,0.1)',
        }}>
          {sidebarCollapsed ? 'AC' : 'AgentCore'}
        </div>
        <AgentSelector collapsed={sidebarCollapsed} />
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[`/${location.pathname.split('/').filter(Boolean)[0] ?? ''}`]}
          items={menuItems}
          onClick={({ key }) => {
            // For routes that support agentId, append the current agent.
            const agentRoutes = ['/chat', '/files', '/agent-config', '/agent-stats', '/sessions'];
            if (agentRoutes.includes(key)) {
              navigate(`${key}/${selectedAgent}`);
            } else {
              navigate(key);
            }
          }}
          style={{ borderRight: 0 }}
        />
      </Sider>
      <Layout style={{ marginLeft: sidebarCollapsed ? 80 : 220, transition: 'margin-left 0.2s' }}>
        <Header style={{
          padding: '0 24px',
          background: colorBgContainer,
          display: 'flex',
          alignItems: 'center',
          borderBottom: '1px solid #f0f0f0',
        }}>
          <span
            onClick={toggleSidebar}
            style={{ fontSize: 18, cursor: 'pointer' }}
          >
            {sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          </span>
          <Select
            value={locale}
            onChange={(val) => setLocale(val as Locale)}
            size="small"
            style={{ width: 100, marginLeft: 16 }}
            suffixIcon={<GlobalOutlined />}
            options={Object.entries(LOCALE_LABELS).map(([value, label]) => ({
              value,
              label,
            }))}
          />
        </Header>
        <Content style={{
          margin: 24,
          padding: 24,
          background: colorBgContainer,
          borderRadius: borderRadiusLG,
          minHeight: 280,
          overflow: 'auto',
        }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
