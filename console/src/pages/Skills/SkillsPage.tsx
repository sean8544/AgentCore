import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Empty,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  message as antdMessage,
} from 'antd';
import {
  AppstoreOutlined,
  DeleteOutlined,
  EyeOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { apiClient } from '../../api/client';
import { useAgentId } from '../../stores/agentStore';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import MarkdownView from '../../components/MarkdownView';
import { stripFrontmatter } from '../../utils/frontmatter';

const { Text, Paragraph } = Typography;

/* ───────── Types ───────── */
interface InstalledSkill {
  name: string;
  dir_name: string;
  description: string;
  enabled: boolean;
}

interface InstalledSkillDetail extends InstalledSkill {
  content: string;
}

/* ───────── Skill card ───────── */
function SkillCard({
  skill,
  toggling,
  onToggle,
  onView,
  onUninstall,
}: {
  skill: InstalledSkill;
  toggling: boolean;
  onToggle: (next: boolean) => void;
  onView: () => void;
  onUninstall: () => void;
}) {
  const { t } = useI18n();
  const enabled = skill.enabled;
  return (
    <div
      style={{
        border: `1px solid ${enabled ? 'var(--google-border)' : 'var(--google-border)'}`,
        borderStyle: enabled ? 'solid' : 'dashed',
        borderRadius: 'var(--google-radius-md)',
        padding: 'var(--google-space-6)',
        background: enabled ? 'var(--google-card)' : 'var(--google-muted)',
        opacity: enabled ? 1 : 0.85,
      }}
    >
      <Space direction="vertical" size="small" style={{ width: '100%' }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space size="small">
            <ThunderboltOutlined
              style={{ color: enabled ? 'var(--google-primary)' : 'var(--google-muted-foreground)' }}
            />
            <Text code strong style={{ fontSize: 13 }}>{skill.dir_name}</Text>
          </Space>
          <Switch
            size="small"
            checked={enabled}
            loading={toggling}
            onChange={(checked) => onToggle(checked)}
          />
        </Space>
        <Paragraph
          type="secondary"
          style={{ fontSize: 12, marginBottom: 0 }}
          ellipsis={{ rows: 2, tooltip: skill.description }}
        >
          {skill.description || t('skills.noDescription')}
        </Paragraph>
        <Space size="small">
          <Button size="small" type="link" icon={<EyeOutlined />} onClick={onView}>
            {t('skills.view')}
          </Button>
          <Popconfirm
            title={t('skills.uninstallConfirm', { skill: skill.dir_name })}
            onConfirm={onUninstall}
          >
            <Button size="small" type="link" danger icon={<DeleteOutlined />}>
              {t('skills.uninstall')}
            </Button>
          </Popconfirm>
        </Space>
      </Space>
    </div>
  );
}

/* ───────── Page ───────── */
export default function SkillsPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const agentId = useAgentId();

  const [skills, setSkills] = useState<InstalledSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [togglingName, setTogglingName] = useState<string | null>(null);

  const [detail, setDetail] = useState<InstalledSkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  /* ── Load installed skills ── */
  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiClient.get(`/skills/agents/${agentId}`);
      setSkills(res.data.skills ?? []);
    } catch {
      setSkills([]);
      antdMessage.error(t('skills.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  /* ── Enable / disable ── */
  const toggleSkill = useCallback(
    async (name: string, enabled: boolean) => {
      setTogglingName(name);
      try {
        await apiClient.put(`/skills/agents/${agentId}/${name}`, { enabled });
        antdMessage.success(
          enabled
            ? t('skills.enableSuccess', { name })
            : t('skills.disableSuccess', { name }),
        );
        void loadSkills();
      } catch (err: unknown) {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        antdMessage.error(detail || t('skills.toggleFailed'));
      } finally {
        setTogglingName(null);
      }
    },
    [agentId, loadSkills],
  );

  /* ── Uninstall ── */
  const uninstallSkill = useCallback(
    async (name: string) => {
      try {
        await apiClient.delete(`/skills/agents/${agentId}/${name}`);
        antdMessage.success(t('skills.uninstallSuccess', { name }));
        void loadSkills();
      } catch {
        antdMessage.error(t('skills.uninstallFailed'));
      }
    },
    [agentId, loadSkills],
  );

  /* ── View detail (installed copy) ── */
  const viewDetail = useCallback(
    async (name: string) => {
      setDetailLoading(true);
      setDetail(null);
      try {
        const res = await apiClient.get(`/skills/agents/${agentId}/${name}`);
        setDetail(res.data);
      } catch {
        antdMessage.error(t('skills.contentLoadFailed'));
      } finally {
        setDetailLoading(false);
      }
    },
    [agentId],
  );

  const enabledSkills = useMemo(() => skills.filter((s) => s.enabled), [skills]);
  const disabledSkills = useMemo(() => skills.filter((s) => !s.enabled), [skills]);

  const gridStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
    gap: 'var(--google-space-6)',
  };

  return (
    <div>
      <GooglePageHeader
        icon={<ThunderboltOutlined />}
        title={t('skills.title')}
        subtitle={t('skills.subtitle', { agent: agentId, count: skills.length })}
        extra={
          <Space>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => void loadSkills()}
            >
              {t('common.refresh')}
            </Button>
            <Button
              size="small"
              type="primary"
              icon={<AppstoreOutlined />}
              onClick={() => navigate('/skill-pool')}
            >
              {t('skills.goToPool')}
            </Button>
          </Space>
        }
      />

      <Spin spinning={loading}>
        {skills.length === 0 && !loading ? (
          <GoogleCard>
            <Empty
              description={
                <Space direction="vertical" size={4}>
                  <Text>{t('skills.empty')}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('skills.emptyHint')}
                  </Text>
                </Space>
              }
            >
              <Button type="primary" onClick={() => navigate('/skill-pool')}>
                {t('skills.goToPool')}
              </Button>
            </Empty>
          </GoogleCard>
        ) : (
          <>
            {/* ── Enabled skills ── */}
            <GoogleCard
              title={
                <Space>
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: 'var(--google-success, #34a853)',
                      display: 'inline-block',
                    }}
                  />
                  {t('skills.enabledSection')}
                  <Tag style={{ fontSize: 11 }}>{enabledSkills.length}</Tag>
                </Space>
              }
              style={{ marginBottom: 'var(--google-space-8)' }}
            >
              {enabledSkills.length === 0 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('skills.noEnabled')}
                </Text>
              ) : (
                <div style={gridStyle}>
                  {enabledSkills.map((skill) => (
                    <SkillCard
                      key={skill.dir_name}
                      skill={skill}
                      toggling={togglingName === skill.dir_name}
                      onToggle={(next) => void toggleSkill(skill.dir_name, next)}
                      onView={() => void viewDetail(skill.dir_name)}
                      onUninstall={() => void uninstallSkill(skill.dir_name)}
                    />
                  ))}
                </div>
              )}
            </GoogleCard>

            {/* ── Disabled skills ── */}
            {disabledSkills.length > 0 && (
              <GoogleCard
                title={
                  <Space>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: 'var(--google-muted-foreground)',
                        display: 'inline-block',
                      }}
                    />
                    {t('skills.disabledSection')}
                    <Tag style={{ fontSize: 11 }}>{disabledSkills.length}</Tag>
                  </Space>
                }
              >
                <div style={gridStyle}>
                  {disabledSkills.map((skill) => (
                    <SkillCard
                      key={skill.dir_name}
                      skill={skill}
                      toggling={togglingName === skill.dir_name}
                      onToggle={(next) => void toggleSkill(skill.dir_name, next)}
                      onView={() => void viewDetail(skill.dir_name)}
                      onUninstall={() => void uninstallSkill(skill.dir_name)}
                    />
                  ))}
                </div>
              </GoogleCard>
            )}
          </>
        )}
      </Spin>

      {/* ── Detail modal (markdown rendered) ── */}
      <Modal
        title={detail ? t('skills.detailTitle', { name: detail.dir_name }) : t('skills.detail')}
        open={detailLoading || detail !== null}
        onCancel={() => setDetail(null)}
        footer={null}
        width={640}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 'var(--google-space-8)' }}>
            <Spin />
          </div>
        ) : detail ? (
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>{detail.description}</Text>
            <div
              style={{
                border: '1px solid var(--google-border)',
                borderRadius: 'var(--google-radius-md)',
                padding: 'var(--google-space-4)',
                maxHeight: 420,
                overflow: 'auto',
                fontSize: 13,
              }}
            >
              <MarkdownView text={stripFrontmatter(detail.content)} />
            </div>
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}
