import { useCallback, useEffect, useState } from 'react';
import { Collapse, Empty, Spin, Tag, Timeline, Typography } from 'antd';
import { apiClient } from '../../api/client';
import { useI18n } from '../../i18n';

const { Text } = Typography;

export interface TraceToolCall {
  id?: string;
  name: string;
  args?: Record<string, unknown>;
}

export interface TraceEvent {
  kind: string; // human | ai | tool | system | ...
  name?: string | null;
  content?: string;
  tool_calls?: TraceToolCall[];
  tool_call_id?: string | null;
  usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number };
}

export interface TraceStep {
  step: number;
  source: string;
  ts: string | null;
  events: TraceEvent[];
}

function formatTime(iso: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

const eventStyle: Record<string, { color: string; label: string }> = {
  human: { color: 'blue', label: 'User' },
  ai: { color: 'green', label: 'Assistant' },
  tool: { color: 'purple', label: 'Tool' },
  system: { color: 'orange', label: 'System' },
};

/** One timeline node: a single step with its message events. */
function StepNode({ step }: { step: TraceStep }) {
  const { t } = useI18n();

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <Text strong style={{ fontSize: 13 }}>
          {t('sessions.traceStep', { step: step.step })}
        </Text>
        <Tag color={step.source === 'input' ? 'blue' : step.source === 'update' ? 'gold' : 'geekblue'}>
          {step.source}
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(step.ts)}</Text>
      </div>
      {step.events.length === 0 ? (
        <Text type="secondary" style={{ fontSize: 12 }}>{t('sessions.traceNoEvents')}</Text>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {step.events.map((ev, i) => {
            const style = eventStyle[ev.kind] ?? { color: 'default', label: ev.kind };
            const calls = ev.tool_calls ?? [];
            return (
              <div
                key={i}
                style={{
                  padding: '8px 12px',
                  borderRadius: 8,
                  border: '1px solid var(--google-border)',
                  background: 'color-mix(in srgb, var(--google-muted) 40%, transparent)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, gap: 8, flexWrap: 'wrap' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <Tag color={style.color} style={{ marginInlineEnd: 0 }}>{style.label}</Tag>
                    {ev.kind === 'tool' && ev.name && (
                      <Text code style={{ fontSize: 12 }}>{ev.name}</Text>
                    )}
                    {calls.length > 0 && (
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {calls.map((c, ci) => (
                          <Tag key={ci} color="purple" style={{ marginInlineEnd: 0 }}>
                            {t('sessions.toolCall', { name: c.name })}
                          </Tag>
                        ))}
                      </div>
                    )}
                  </div>
                  {ev.usage && (ev.usage.input_tokens || ev.usage.output_tokens) && (
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {t('sessions.traceUsage', {
                        input: ev.usage.input_tokens ?? 0,
                        output: ev.usage.output_tokens ?? 0,
                      })}
                    </Text>
                  )}
                </div>
                {ev.content ? (
                  <Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12.5, lineHeight: 1.7, display: 'block' }}>
                    {ev.content}
                  </Text>
                ) : (
                  <Text type="secondary" style={{ fontSize: 12 }}>{t('sessions.traceNoContent')}</Text>
                )}
                {calls.length > 0 && (
                  <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {calls.map((c, ci) => (
                      <Collapse
                        key={ci}
                        size="small"
                        ghost
                        items={[
                          {
                            key: String(ci),
                            label: <Text style={{ fontSize: 12 }}>{c.name}</Text>,
                            children: (
                              <pre
                                style={{
                                  margin: 0,
                                  fontSize: 11.5,
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-word',
                                  fontFamily: 'var(--google-font-mono, monospace)',
                                }}
                              >
                                {JSON.stringify(c.args ?? {}, null, 2)}
                              </pre>
                            ),
                          },
                        ]}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** LangSmith-style per-step execution timeline for a session. */
export default function TraceTimeline({ sessionId }: { sessionId: string }) {
  const { t } = useI18n();
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await apiClient.get<{ steps: TraceStep[] }>(
        `/chat/sessions/${sessionId}/trace`,
      );
      setSteps(data?.steps ?? []);
    } catch (err) {
      console.error('Failed to load session trace', err);
      setSteps([]);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 'var(--google-space-16)' }}>
        <Spin />
      </div>
    );
  }
  if (steps.length === 0) {
    return <Empty description={t('sessions.traceEmpty')} style={{ padding: 'var(--google-space-8)' }} />;
  }

  return (
    <>
      <style>{`
        .ant-timeline .ant-steps-item-header {
          flex: 0 0 60px !important;
        }
        .ant-timeline .ant-steps-item-rail {
          left: 72px !important;
        }
        .ant-timeline .ant-timeline-item-icon {
          left: 72px !important;
        }
      `}</style>
      <Timeline
        mode="left"
        items={steps.map((s) => ({
          color: s.source === 'input' ? 'blue' : 'green',
          label: <Text type="secondary" style={{ fontSize: 12 }}>Step {s.step}</Text>,
          children: <StepNode step={s} />,
        }))}
        style={{ marginTop: 'var(--google-space-4)' }}
      />
    </>
  );
}