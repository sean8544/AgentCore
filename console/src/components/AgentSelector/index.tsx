import { Select, Tag, Space, Tooltip } from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import { useAgentStore } from '../../stores/agentStore';

const stateColors: Record<string, string> = {
  idle: 'blue',
  running: 'green',
  paused: 'orange',
  stopped: 'default',
  error: 'red',
};

export default function AgentSelector({ collapsed }: { collapsed?: boolean }) {
  const { agents, selectedAgent, setSelectedAgent, loading, refreshAgents } = useAgentStore();

  if (collapsed) {
    return (
      <Tooltip title={`当前智能体：${selectedAgent}`} placement="right">
        <div style={{ display: 'flex', justifyContent: 'center', padding: '12px 0' }}>
          <RobotOutlined style={{ fontSize: 20, color: '#1677ff' }} />
        </div>
      </Tooltip>
    );
  }

  return (
    <div style={{ padding: '12px 12px 4px' }}>
      <Select
        style={{ width: '100%' }}
        value={selectedAgent}
        onChange={setSelectedAgent}
        loading={loading}
        onDropdownVisibleChange={(open) => {
          if (open) void refreshAgents();
        }}
        options={agents.map((agent) => ({
          value: agent.agent_id,
          label: (
            <Space size={6}>
              <span>{agent.agent_id}</span>
              {agent.state && (
                <Tag color={stateColors[agent.state] || 'default'} style={{ marginInlineEnd: 0 }}>
                  {agent.state}
                </Tag>
              )}
            </Space>
          ),
        }))}
        placeholder="选择智能体"
        popupMatchSelectWidth={false}
      />
    </div>
  );
}
