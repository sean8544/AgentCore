import { Badge, Layout, Menu, Select } from 'antd';
import {
  MessageOutlined,
  MailOutlined,
  WifiOutlined,
  ClockCircleOutlined,
  HeartOutlined,
  FileOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  ToolOutlined,
  ApiOutlined,
  CloudOutlined,
  CloudServerOutlined,
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
  MoonOutlined,
  SunOutlined,
} from '@ant-design/icons';
import { useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useAppStore } from '../stores/appStore';
import { useAgentStore, agentPath, AGENT_SECTIONS } from '../stores/agentStore';
import { useInboxStore } from '../stores/inboxStore';
import { useI18n, LOCALE_LABELS, type Locale } from '../i18n';
import AgentSelector from '../components/AgentSelector';

const { Header, Sider, Content } = Layout;

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { sidebarCollapsed, toggleSidebar, resolvedTheme, toggleTheme } = useAppStore();
  const refreshAgents = useAgentStore((s) => s.refreshAgents);
  const selectedAgent = useAgentStore((s) => s.selectedAgent);
  const pendingApprovalCount = useInboxStore((s) => s.pendingApprovalCount);
  const refreshPendingCount = useInboxStore((s) => s.refreshPendingCount);
  const { t, locale, setLocale } = useI18n();

  const menuItems = [
    {
      key: 'chat',
      icon: <MessageOutlined />,
      label: t('menu.chat'),
    },
    {
      key: '/inbox',
      icon: (
        <Badge
          count={pendingApprovalCount}
          dot={sidebarCollapsed && pendingApprovalCount > 0}
          size="small"
          overflowCount={99}
          title={t('menu.inbox')}
        >
          <MailOutlined />
        </Badge>
      ),
      label: t('menu.inbox'),
    },
    {
      key: 'control-group',
      label: t('menu.control'),
      type: 'group' as const,
      children: [
        { key: '/channels', icon: <WifiOutlined />, label: t('menu.channels') },
        { key: 'sessions', icon: <MessageOutlined />, label: t('menu.sessions') },
        { key: '/cron-jobs', icon: <ClockCircleOutlined />, label: t('menu.cronJobs') },
        { key: 'heartbeat', icon: <HeartOutlined />, label: t('menu.heartbeat') },
      ],
    },
    {
      key: 'workspace-group',
      label: t('menu.workspace'),
      type: 'group' as const,
      children: [
        { key: 'files', icon: <FileOutlined />, label: t('menu.files') },
        { key: 'memory', icon: <DatabaseOutlined />, label: t('menu.memory') },
        { key: 'skills', icon: <ThunderboltOutlined />, label: t('menu.skills') },
        { key: 'tools', icon: <ToolOutlined />, label: t('menu.tools') },
        { key: 'mcp', icon: <ApiOutlined />, label: 'MCP' },
        { key: '/acp', icon: <CloudOutlined />, label: 'ACP' },
        { key: 'sandbox', icon: <CloudServerOutlined />, label: t('menu.sandbox') },
        { key: 'config', icon: <SettingOutlined />, label: t('menu.agentConfig') },
        { key: 'stats', icon: <BarChartOutlined />, label: t('menu.agentStats') },
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
        { key: '/sandbox-control', icon: <CloudServerOutlined />, label: t('menu.sandboxControl') },
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

  // Inbox badge: initial fetch + periodic polling (background heartbeats /
  // cron runs can raise approvals while the user is anywhere in the app).
  useEffect(() => {
    void refreshPendingCount();
    const timer = window.setInterval(() => {
      void refreshPendingCount();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [refreshPendingCount]);

  // Highlight the active menu item: agent-scoped pages select their section
  // token (/agents/:agentId/<section>); everything else uses its top-level
  // path (/models, /agents, ...).
  const pathParts = location.pathname.split('/').filter(Boolean);
  const selectedKey =
    pathParts[0] === 'agents'
      ? pathParts.length >= 3
        ? pathParts[2]
        : '/agents'
      : `/${pathParts[0] ?? ''}`;

  // Refresh the badge right after navigation — approvals may have been
  // resolved on the previous page (e.g. the Chat approval card).
  useEffect(() => {
    void refreshPendingCount();
  }, [location.pathname, refreshPendingCount]);

  const siderWidth = sidebarCollapsed ? 80 : 220;

  return (
    <Layout style={{ height: '100%', background: 'var(--google-background)' }}>
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
          background: 'var(--google-sidebar)',
          borderRight: '1px solid var(--google-sidebar-border)',
        }}
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--google-sidebar-foreground)',
            fontSize: sidebarCollapsed ? 14 : 18,
            fontWeight: 600,
            letterSpacing: 0.5,
            borderBottom: '1px solid var(--google-sidebar-border)',
          }}
        >
          {sidebarCollapsed ? 'AC' : 'AgentCore'}
        </div>
        <AgentSelector collapsed={sidebarCollapsed} />
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => {
            if ((AGENT_SECTIONS as readonly string[]).includes(key)) {
              navigate(agentPath(key, selectedAgent));
            } else {
              navigate(key);
            }
          }}
          style={{
            borderRight: 0,
            background: 'transparent',
            padding: '8px 0',
          }}
        />
      </Sider>
      <Layout
        style={{
          marginLeft: siderWidth,
          transition: 'margin-left 0.2s ease',
          background: 'var(--google-background)',
        }}
      >
        <Header
          style={{
            padding: '0 var(--google-space-8)',
            background: 'var(--google-background)',
            display: 'flex',
            alignItems: 'center',
            borderBottom: '1px solid var(--google-border)',
            position: 'sticky',
            top: 0,
            zIndex: 10,
          }}
        >
          <span
            onClick={toggleSidebar}
            style={{
              fontSize: 18,
              cursor: 'pointer',
              color: 'var(--google-foreground)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 32,
              height: 32,
              borderRadius: 'var(--google-radius-md)',
              transition: 'background-color var(--google-transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--google-muted)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent';
            }}
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
          <span
            onClick={toggleTheme}
            style={{
              fontSize: 16,
              cursor: 'pointer',
              color: 'var(--google-foreground)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 32,
              height: 32,
              marginLeft: 8,
              borderRadius: 'var(--google-radius-md)',
              transition: 'background-color var(--google-transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--google-muted)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent';
            }}
          >
            {resolvedTheme === 'dark' ? <SunOutlined /> : <MoonOutlined />}
          </span>
        </Header>
        <Content
          style={{
            margin: 'var(--google-space-8)',
            padding: 'var(--google-space-8)',
            background: 'var(--google-card)',
            borderRadius: 'var(--google-radius-lg)',
            border: '1px solid var(--google-border)',
            minHeight: 280,
            overflow: 'auto',
          }}
        >
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
