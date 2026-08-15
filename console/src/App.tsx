import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import { useI18n } from './i18n';
import MainLayout from './layouts/MainLayout';
import ChatPage from './pages/Chat/ChatPage';
import InboxPage from './pages/Inbox/InboxPage';
import ChannelsPage from './pages/Channels/ChannelsPage';
import SessionsPage from './pages/Sessions/SessionsPage';
import CronJobsPage from './pages/CronJobs/CronJobsPage';
import HeartbeatPage from './pages/Heartbeat/HeartbeatPage';
import FilesPage from './pages/Files/FilesPage';
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

const antdLocales = { 'zh-CN': zhCN, en: enUS } as const;

export default function App() {
  const { locale } = useI18n();
  return (
    <ConfigProvider locale={antdLocales[locale]}>
      <BrowserRouter>
        <Routes>
          <Route element={<MainLayout />}>
            <Route path="/chat/:agentId?" element={<ChatPage />} />
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/channels" element={<ChannelsPage />} />
            <Route path="/sessions/:agentId?" element={<SessionsPage />} />
            <Route path="/cron-jobs" element={<CronJobsPage />} />
            <Route path="/heartbeat" element={<HeartbeatPage />} />
            <Route path="/files/:agentId?" element={<FilesPage />} />
            <Route path="/skills" element={<SkillsPage />} />
            <Route path="/tools" element={<ToolsPage />} />
            <Route path="/mcp" element={<McpPage />} />
            <Route path="/acp" element={<AcpPage />} />
            <Route path="/agent-config/:agentId?" element={<AgentConfigPage />} />
            <Route path="/agent-stats/:agentId?" element={<AgentStatsPage />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/skill-pool" element={<SkillPoolPage />} />
            <Route path="/environments" element={<EnvironmentsPage />} />
            <Route path="/security" element={<SecurityPage />} />
            <Route path="/token-usage" element={<TokenUsagePage />} />
            <Route path="/backups" element={<BackupsPage />} />
            <Route path="/voice" element={<VoicePage />} />
            <Route path="/debug" element={<DebugPage />} />
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}
