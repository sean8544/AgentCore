import { Select, Tag, Space, Tooltip, Avatar, Typography } from 'antd';
import { ApiOutlined } from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAgentStore, agentPath, AGENT_SECTIONS } from '../../stores/agentStore';
import { useI18n } from '../../i18n';

const { Text } = Typography;

const stateColors: Record<string, string> = {
  idle: 'blue',
  running: 'green',
  paused: 'orange',
  stopped: 'default',
  error: 'red',
};

// Generate a deterministic color from an agent id.
const agentColor = (id: string): string => {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = id.charCodeAt(i) + ((hash << 5) - hash);
  }
  const colors = ['#1677ff', '#0e7a5f', '#d97706', '#722ed1', '#eb2f96', '#13c2c2', '#52c41a'];
  return colors[Math.abs(hash) % colors.length];
};

export default function AgentSelector({ collapsed }: { collapsed?: boolean }) {
  const { agents, selectedAgent, setSelectedAgent, loading, refreshAgents } = useAgentStore();
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  const handleAgentChange = (agentId: string) => {
    setSelectedAgent(agentId);
    // Stay on the current agent section (/agents/:agentId/<section>), only
    // swapping the agent; fall back to chat from global pages.
    const pathParts = location.pathname.split('/').filter(Boolean);
    const section =
      pathParts[0] === 'agents' && pathParts.length >= 3 ? pathParts[2] : null;
    if (section && (AGENT_SECTIONS as readonly string[]).includes(section)) {
      navigate(agentPath(section, agentId));
    } else {
      navigate(agentPath('chat', agentId));
    }
  };

  if (collapsed) {
    const initial = selectedAgent.charAt(0).toUpperCase();
    return (
      <Tooltip title={t('agentSelector.currentAgent', { agent: selectedAgent })} placement="right">
        <div style={{ display: 'flex', justifyContent: 'center', padding: '12px 0' }}>
          <Avatar
            size={32}
            style={{ background: agentColor(selectedAgent), fontSize: 14, fontWeight: 600 }}
          >
            {initial}
          </Avatar>
        </div>
      </Tooltip>
    );
  }

  return (
    <div style={{ padding: '12px 12px 4px' }}>
      <Select
        style={{ width: '100%' }}
        value={selectedAgent}
        onChange={handleAgentChange}
        loading={loading}
        onDropdownVisibleChange={(open) => {
          if (open) void refreshAgents();
        }}
        popupMatchSelectWidth={false}
        optionRender={(option) => {
          const agent = agents.find((a) => a.agent_id === option.value);
          return (
            <div style={{ padding: '4px 0', minWidth: 200 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Avatar
                  size={24}
                  style={{
                    background: agentColor(String(option.value)),
                    fontSize: 11,
                    fontWeight: 600,
                    flexShrink: 0,
                  }}
                >
                  {String(option.value).charAt(0).toUpperCase()}
                </Avatar>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{String(option.value)}</span>
                {agent?.state && (
                  <Tag
                    color={stateColors[agent.state] || 'default'}
                    style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px' }}
                  >
                    {agent.state}
                  </Tag>
                )}
                {agent?.enable_subagents && (
                  <Tag
                    color="geekblue"
                    style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px' }}
                    icon={<ApiOutlined />}
                  >
                    {t('agents.delegationTag')}
                  </Tag>
                )}
              </div>
              {agent?.description && (
                <Text
                  type="secondary"
                  style={{ fontSize: 11, display: 'block', marginLeft: 32, marginTop: 2 }}
                  ellipsis={{ tooltip: agent.description }}
                >
                  {agent.description}
                </Text>
              )}
            </div>
          );
        }}
        options={agents.map((agent) => ({
          value: agent.agent_id,
          label: (
            <Space size={6}>
              <Avatar
                size={20}
                style={{
                  background: agentColor(agent.agent_id),
                  fontSize: 10,
                  fontWeight: 600,
                }}
              >
                {agent.agent_id.charAt(0).toUpperCase()}
              </Avatar>
              <span>{agent.agent_id}</span>
              {agent.state && (
                <Tag color={stateColors[agent.state] || 'default'} style={{ marginInlineEnd: 0 }}>
                  {agent.state}
                </Tag>
              )}
            </Space>
          ),
        }))}
        placeholder={t('agentSelector.selectAgent')}
      />
    </div>
  );
}
