import { useCallback, useEffect, useMemo, useState } from 'react';
import { Popover, Spin, Typography, message as antdMessage } from 'antd';
import { ApiOutlined, CheckOutlined, DownOutlined } from '@ant-design/icons';
import { apiClient } from '../../../api/client';
import { useAgentId } from '../../../stores/agentStore';

const { Text } = Typography;

const ORANGE = '#FF7F16';

/* ───────── Types ───────── */
interface ModelItem {
  provider: string;
  name: string;
  base_url?: string;
  api_key_env?: string;
  /** agents currently using this model (backend may expose used_by as well) */
  agents?: string[];
  used_by?: string[];
}

const usersOf = (m: ModelItem): string[] => m.agents ?? m.used_by ?? [];
const keyOf = (m: ModelItem) => `${m.provider}|${m.name}|${m.base_url ?? ''}`;

/**
 * Lightweight model selector for the chat header.
 * Lists the catalog from GET /api/models; picking a model calls
 * PUT /api/agents/{agent}/model and applies on the next chat turn.
 */
export default function ChatModelSelector() {
  const agentId = useAgentId();
  const [models, setModels] = useState<ModelItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const loadModels = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get('/models');
      setModels(Array.isArray(res.data.models) ? res.data.models : []);
    } catch {
      setModels([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadModels();
  }, [loadModels, agentId]);

  /* Current model of this agent, inferred from the catalog. */
  const currentModel = useMemo(
    () => models.find((m) => usersOf(m).includes(agentId)),
    [models, agentId],
  );

  const switchModel = useCallback(
    async (model: ModelItem) => {
      const key = keyOf(model);
      setSaving(key);
      try {
        await apiClient.put(`/agents/${agentId}/model`, {
          provider: model.provider,
          name: model.name,
          ...(model.base_url ? { base_url: model.base_url } : {}),
          ...(model.api_key_env ? { api_key_env: model.api_key_env } : {}),
        });
        antdMessage.success('模型已切换，下一轮生效');
        setModels((prev) =>
          prev.map((m) => {
            const users = usersOf(m).filter((id) => id !== agentId);
            if (keyOf(m) === key) users.push(agentId);
            const next: ModelItem = { ...m };
            if (m.agents !== undefined) next.agents = users;
            if (m.used_by !== undefined) next.used_by = users;
            return next;
          }),
        );
        setOpen(false);
      } catch (err) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        antdMessage.error(detail || '模型切换失败，请重试');
      } finally {
        setSaving(null);
      }
    },
    [agentId],
  );

  const content = (
    <div style={{ width: 280 }}>
      <div style={{ marginBottom: 8 }}>
        <Text strong style={{ fontSize: 13 }}>模型目录</Text>
        <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
          {agentId}
        </Text>
      </div>
      {loading ? (
        <div style={{ textAlign: 'center', padding: '12px 0' }}>
          <Spin size="small" />
        </div>
      ) : models.length === 0 ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          暂无已配置的模型，请先在「模型管理」或创建 Agent 时配置
        </Text>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {models.map((m) => {
            const key = keyOf(m);
            const active = currentModel != null && keyOf(currentModel) === key;
            const busy = saving === key;
            return (
              <div
                key={key}
                onClick={() => {
                  if (!active && saving === null) void switchModel(m);
                }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 10px', borderRadius: 8, cursor: 'pointer',
                  background: active ? '#fff3e8' : 'transparent',
                  border: active ? '1px solid #ffd8b3' : '1px solid transparent',
                  opacity: saving !== null && !busy ? 0.6 : 1,
                }}
              >
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                  background: active ? ORANGE : '#d9d9d9',
                }} />
                <span style={{ minWidth: 0 }}>
                  <span style={{
                    display: 'block', fontSize: 13,
                    fontWeight: active ? 600 : 400, color: active ? ORANGE : '#333',
                  }}>
                    {m.name}
                  </span>
                  {m.base_url && (
                    <Text type="secondary" style={{ fontSize: 11 }} ellipsis>
                      {m.provider} · {m.base_url}
                    </Text>
                  )}
                </span>
                <span style={{ marginLeft: 'auto', flexShrink: 0 }}>
                  {busy
                    ? <Spin size="small" />
                    : active && <CheckOutlined style={{ color: ORANGE, fontSize: 12 }} />}
                </span>
              </div>
            );
          })}
        </div>
      )}
      <div style={{
        marginTop: 8, paddingTop: 6, borderTop: '1px solid #f0f0f0',
        fontSize: 11, color: '#999',
      }}>
        切换后下一轮对话生效
      </div>
    </div>
  );

  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="bottomRight"
      destroyTooltipOnHide
    >
      <button
        type="button"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '3px 10px', borderRadius: 8, cursor: 'pointer',
          border: '1px solid #ffd8b3', background: '#fffaf4',
          color: '#333', fontSize: 12, lineHeight: '22px',
          maxWidth: 220,
        }}
      >
        <ApiOutlined style={{ color: ORANGE, fontSize: 12 }} />
        <span style={{
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          fontWeight: 500,
        }}>
          {loading ? '加载中…' : currentModel ? currentModel.name : '未配置模型'}
        </span>
        <DownOutlined style={{ fontSize: 9, color: '#999' }} />
      </button>
    </Popover>
  );
}
