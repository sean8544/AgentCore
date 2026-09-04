import { BrowserRouter, Routes, Route, Navigate, useParams, useLocation } from 'react-router-dom';
import { ConfigProvider, theme, type ThemeConfig } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import { useI18n } from './i18n';
import { getBasePath } from './api/client';
import MainLayout from './layouts/MainLayout';
import { useAppStore } from './stores/appStore';
import { agentPath, useAgentStore } from './stores/agentStore';
import ChatPage from './pages/Chat/ChatPage';
import InboxPage from './pages/Inbox/InboxPage';
import ChannelsPage from './pages/Channels/ChannelsPage';
import SessionsPage from './pages/Sessions/SessionsPage';
import CronJobsPage from './pages/CronJobs/CronJobsPage';
import HeartbeatPage from './pages/Heartbeat/HeartbeatPage';
import FilesPage from './pages/Files/FilesPage';
import MemoryPage from './pages/Memory/MemoryPage';
import SkillsPage from './pages/Skills/SkillsPage';
import ToolsPage from './pages/Tools/ToolsPage';
import McpPage from './pages/Mcp/McpPage';
import AcpPage from './pages/Acp/AcpPage';
import AgentConfigPage from './pages/AgentConfig/AgentConfigPage';
import AgentStatsPage from './pages/AgentStats/AgentStatsPage';
import AgentsPage from './pages/Agents/AgentsPage';
import ModelsPage from './pages/Models/ModelsPage';
import SkillPoolPage from './pages/SkillPool/SkillPoolPage';
import EnvironmentsPage from './pages/Environments/EnvironmentsPage';
import SecurityPage from './pages/Security/SecurityPage';
import TokenUsagePage from './pages/TokenUsage/TokenUsagePage';
import BackupsPage from './pages/Backups/BackupsPage';
import VoicePage from './pages/Voice/VoicePage';
import DebugPage from './pages/Debug/DebugPage';
import SandboxPage from './pages/Sandbox/SandboxPage';
import SandboxControlPage from './pages/SandboxControl/SandboxControlPage';
import LoginPage from './pages/Login/LoginPage';

const antdLocales = { 'zh-CN': zhCN, en: enUS } as const;

/**
 * Google-themed Ant Design token overrides.
 * Uses the Google Design Library color, radius and spacing language:
 * - Primary: #4285f4
 * - Border radius: 8px
 * - Flat surfaces with thin borders
 *
 * Light and dark palettes are defined separately: every surface/text
 * token is theme-specific so the dark algorithm never inherits light
 * hardcoded values (which previously rendered black-on-black text).
 */
const googleTypography = {
  fontFamily: '"DM Sans", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
  fontFamilyCode: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
  fontSize: 15,
  sizeStep: 4,
  sizeUnit: 3.84,
};

const googleThemeLight: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#4285f4',
    colorInfo: '#4285f4',
    colorSuccess: '#34a853',
    colorWarning: '#fbbc05',
    colorError: '#ef4444',
    colorTextBase: '#0e1115',
    colorBgBase: '#ffffff',
    borderRadius: 8,
    ...googleTypography,
    colorBorder: '#ebebeb',
    colorBorderSecondary: '#e7eaef',
    colorBgContainer: '#ffffff',
    colorBgElevated: '#f9f9fa',
    colorFill: '#eff1f4',
    colorFillSecondary: '#dbeafe',
    colorFillTertiary: '#f0f6ff',
    colorText: '#0e1115',
    colorTextSecondary: '#7f8d9f',
    colorTextTertiary: '#7f8d9f',
    controlOutline: 'rgba(66, 133, 244, 0.2)',
  },
  components: {
    Layout: {
      bodyBg: '#ffffff',
      headerBg: '#ffffff',
      siderBg: '#f0f6ff',
      triggerBg: '#f0f6ff',
      triggerColor: '#0e1115',
    },
    Menu: {
      itemBg: '#f0f6ff',
      itemHoverBg: '#dbeafe',
      itemSelectedBg: '#4285f4',
      itemSelectedColor: '#ffffff',
      itemColor: '#0e1115',
      groupTitleColor: '#7f8d9f',
      itemBorderRadius: 6,
      itemMarginInline: 8,
      itemMarginBlock: 4,
      iconSize: 16,
      iconMarginInlineEnd: 10,
    },
    Button: {
      borderRadius: 8,
      borderRadiusSM: 4,
      borderRadiusLG: 8,
      defaultBg: '#ffffff',
      defaultColor: '#0e1115',
      defaultBorderColor: '#ebebeb',
      primaryShadow: 'none',
      defaultShadow: 'none',
      dangerShadow: 'none',
    },
    Card: {
      borderRadius: 8,
      colorBorderSecondary: '#ebebeb',
      headerBg: 'transparent',
    },
    Table: {
      borderRadius: 8,
      headerBg: '#f9f9fa',
      headerColor: '#0e1115',
      rowHoverBg: '#f0f6ff',
      borderColor: '#ebebeb',
    },
    Input: {
      borderRadius: 8,
      activeBorderColor: '#4285f4',
      hoverBorderColor: '#4285f4',
    },
    Select: {
      borderRadius: 8,
      optionSelectedBg: '#dbeafe',
      optionActiveBg: '#f0f6ff',
    },
    Tag: {
      borderRadius: 4,
    },
    Avatar: {
      borderRadius: 8,
    },
  },
};

const googleThemeDark: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: '#4285f4',
    colorInfo: '#4285f4',
    colorSuccess: '#34a853',
    colorWarning: '#fbbc05',
    colorError: '#ef4444',
    colorTextBase: '#e8eaed',
    colorBgBase: '#131314',
    borderRadius: 8,
    ...googleTypography,
    colorBorder: '#3c4043',
    colorBorderSecondary: '#333639',
    colorBgContainer: '#1d1e20',
    colorBgElevated: '#26272a',
    colorFill: '#333639',
    colorFillSecondary: '#2c3140',
    colorFillTertiary: '#22262d',
    colorText: '#e8eaed',
    colorTextSecondary: '#9aa0a6',
    colorTextTertiary: '#9aa0a6',
    controlOutline: 'rgba(138, 180, 248, 0.25)',
  },
  components: {
    Layout: {
      bodyBg: '#131314',
      headerBg: '#131314',
      siderBg: '#17181a',
      triggerBg: '#17181a',
      triggerColor: '#e8eaed',
    },
    Menu: {
      itemBg: 'transparent',
      itemHoverBg: '#26272a',
      itemSelectedBg: '#4285f4',
      itemSelectedColor: '#ffffff',
      itemColor: '#e8eaed',
      groupTitleColor: '#9aa0a6',
      itemBorderRadius: 6,
      itemMarginInline: 8,
      itemMarginBlock: 4,
      iconSize: 16,
      iconMarginInlineEnd: 10,
    },
    Button: {
      borderRadius: 8,
      borderRadiusSM: 4,
      borderRadiusLG: 8,
      defaultBg: '#1d1e20',
      defaultColor: '#e8eaed',
      defaultBorderColor: '#3c4043',
      primaryShadow: 'none',
      defaultShadow: 'none',
      dangerShadow: 'none',
    },
    Card: {
      borderRadius: 8,
      colorBorderSecondary: '#333639',
      headerBg: 'transparent',
    },
    Table: {
      borderRadius: 8,
      headerBg: '#26272a',
      headerColor: '#e8eaed',
      rowHoverBg: '#22262d',
      borderColor: '#333639',
    },
    Input: {
      borderRadius: 8,
      activeBorderColor: '#8ab4f8',
      hoverBorderColor: '#8ab4f8',
    },
    Select: {
      borderRadius: 8,
      optionSelectedBg: '#2c3140',
      optionActiveBg: '#22262d',
    },
    Tag: {
      borderRadius: 4,
    },
    Avatar: {
      borderRadius: 8,
    },
  },
};

/**
 * Redirect legacy flat routes (``/chat/:agentId``, ``/skills``, ...) to the
 * new nested namespace ``/agents/:agentId/<section>``. When the legacy path
 * carries no agent id, fall back to the persisted selection.
 */
function LegacyAgentRedirect({ section }: { section: string }) {
  const { agentId } = useParams<{ agentId?: string }>();
  const { search } = useLocation();
  const fallback = useAgentStore((s) => s.selectedAgent);
  return <Navigate to={`${agentPath(section, agentId || fallback)}${search}`} replace />;
}

export default function App() {
  const { locale } = useI18n();
  const resolvedTheme = useAppStore((s) => s.resolvedTheme);
  const googleTheme = resolvedTheme === 'dark' ? googleThemeDark : googleThemeLight;
  return (
    <ConfigProvider locale={antdLocales[locale]} theme={googleTheme}>
      <BrowserRouter basename={getBasePath() || '/'}>
        <Routes>
          {/* ── Auth (outside MainLayout; redirect-only when disabled) ── */}
          <Route path="/login" element={<LoginPage />} />
          <Route element={<MainLayout />}>
            {/* ── Agent-scoped pages: /agents/:agentId/<section> ── */}
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/agents/:agentId" element={<LegacyAgentRedirect section="chat" />} />
            <Route path="/agents/:agentId/chat" element={<ChatPage />} />
            <Route path="/agents/:agentId/sessions" element={<SessionsPage />} />
            <Route path="/agents/:agentId/files" element={<FilesPage />} />
            <Route path="/agents/:agentId/skills" element={<SkillsPage />} />
            <Route path="/agents/:agentId/tools" element={<ToolsPage />} />
            <Route path="/agents/:agentId/mcp" element={<McpPage />} />
            <Route path="/agents/:agentId/memory" element={<MemoryPage />} />
            <Route path="/agents/:agentId/heartbeat" element={<HeartbeatPage />} />
            <Route path="/agents/:agentId/sandbox" element={<SandboxPage />} />
            <Route path="/agents/:agentId/config" element={<AgentConfigPage />} />
            <Route path="/agents/:agentId/stats" element={<AgentStatsPage />} />

            {/* ── Global (instance-shared) pages ── */}
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/channels" element={<ChannelsPage />} />
            <Route path="/cron-jobs" element={<CronJobsPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/skill-pool" element={<SkillPoolPage />} />
            <Route path="/environments" element={<EnvironmentsPage />} />
            <Route path="/security" element={<SecurityPage />} />
            <Route path="/sandbox-control" element={<SandboxControlPage />} />
            <Route path="/token-usage" element={<TokenUsagePage />} />
            <Route path="/backups" element={<BackupsPage />} />
            <Route path="/voice" element={<VoicePage />} />
            <Route path="/debug" element={<DebugPage />} />
            <Route path="/acp" element={<AcpPage />} />

            {/* ── Legacy flat routes → nested namespace ── */}
            <Route path="/chat/:agentId?" element={<LegacyAgentRedirect section="chat" />} />
            <Route path="/sessions/:agentId?" element={<LegacyAgentRedirect section="sessions" />} />
            <Route path="/files/:agentId?" element={<LegacyAgentRedirect section="files" />} />
            <Route path="/skills" element={<LegacyAgentRedirect section="skills" />} />
            <Route path="/tools" element={<LegacyAgentRedirect section="tools" />} />
            <Route path="/mcp" element={<LegacyAgentRedirect section="mcp" />} />
            <Route path="/memory" element={<LegacyAgentRedirect section="memory" />} />
            <Route path="/heartbeat" element={<LegacyAgentRedirect section="heartbeat" />} />
            <Route path="/agent-config/:agentId?" element={<LegacyAgentRedirect section="config" />} />
            <Route path="/agent-stats/:agentId?" element={<LegacyAgentRedirect section="stats" />} />

            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}
