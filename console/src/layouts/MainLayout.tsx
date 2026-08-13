import { Layout, Menu, theme } from 'antd';
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
} from '@ant-design/icons';
import { useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useAppStore } from '../stores/appStore';
import { useAgentStore } from '../stores/agentStore';
import AgentSelector from '../components/AgentSelector';

const { Header, Sider, Content } = Layout;

const menuItems = [
  {
    key: '/chat',
    icon: <MessageOutlined />,
    label: '聊天',
  },
  {
    key: '/inbox',
    icon: <MailOutlined />,
    label: '收件箱',
  },
  {
    key: 'control-group',
    label: '控制',
    type: 'group' as const,
    children: [
      { key: '/channels', icon: <WifiOutlined />, label: '频道' },
      { key: '/sessions', icon: <MessageOutlined />, label: '会话' },
      { key: '/cron-jobs', icon: <ClockCircleOutlined />, label: '定时任务' },
      { key: '/heartbeat', icon: <HeartOutlined />, label: '心跳' },
    ],
  },
  {
    key: 'workspace-group',
    label: '工作区',
    type: 'group' as const,
    children: [
      { key: '/files', icon: <FileOutlined />, label: '文件' },
      { key: '/skills', icon: <ThunderboltOutlined />, label: '技能' },
      { key: '/tools', icon: <ToolOutlined />, label: '工具' },
      { key: '/mcp', icon: <ApiOutlined />, label: 'MCP' },
      { key: '/acp', icon: <CloudOutlined />, label: 'ACP' },
      { key: '/agent-config', icon: <SettingOutlined />, label: '运行配置' },
      { key: '/agent-stats', icon: <BarChartOutlined />, label: '智能体统计' },
    ],
  },
  {
    key: 'settings-group',
    label: '设置',
    type: 'group' as const,
    children: [
      { key: '/agents', icon: <RobotOutlined />, label: '智能体管理' },
      { key: '/models', icon: <CodeOutlined />, label: '模型' },
      { key: '/skill-pool', icon: <AppstoreOutlined />, label: '技能池' },
      { key: '/environments', icon: <EnvironmentOutlined />, label: '环境变量' },
      { key: '/security', icon: <SafetyOutlined />, label: '安全' },
      { key: '/token-usage', icon: <DashboardOutlined />, label: 'Token 消耗' },
      { key: '/backups', icon: <SaveOutlined />, label: '备份' },
      { key: '/voice', icon: <AudioOutlined />, label: '语音转写' },
      { key: '/debug', icon: <BugOutlined />, label: '调试' },
    ],
  },
];

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { sidebarCollapsed, toggleSidebar } = useAppStore();
  const refreshAgents = useAgentStore((s) => s.refreshAgents);
  const { token: { colorBgContainer, borderRadiusLG } } = theme.useToken();

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
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
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
