import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button,
  Checkbox,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Tree,
  Typography,
  Upload,
  message as antdMessage,
} from 'antd';
import {
  AppstoreAddOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  FileAddOutlined,
  FileTextOutlined,
  FolderFilled,
  FolderOutlined,
  ReloadOutlined,
  RocketOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { DataNode } from 'antd/es/tree';
import { apiClient } from '../../api/client';
import { useAgentId } from '../../stores/agentStore';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import MarkdownView from '../../components/MarkdownView';
import { stripFrontmatter } from '../../utils/frontmatter';

const { Text, Paragraph } = Typography;

/* ───────── Types ───────── */
interface SkillItem {
  name: string;
  dir_name: string;
  description: string;
  file_count?: number;
}

interface SkillDetail extends SkillItem {
  content: string;
}

interface AgentItem {
  agent_id: string;
}

interface SkillFileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
}

export default function SkillPoolPage() {
  const { t } = useI18n();
  const [poolSkills, setPoolSkills] = useState<SkillItem[]>([]);
  const [poolLoading, setPoolLoading] = useState(false);

  const agentId = useAgentId();
  const [agentSkills, setAgentSkills] = useState<SkillItem[]>([]);
  const [agentSkillsLoading, setAgentSkillsLoading] = useState(false);

  /* Create / edit share one form; editingName === null means create. */
  const [formOpen, setFormOpen] = useState(false);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  /* Install modal: pick target agents. */
  const [installTarget, setInstallTarget] = useState<string | null>(null);
  const [agentOptions, setAgentOptions] = useState<AgentItem[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [installing, setInstalling] = useState(false);

  const [syncing, setSyncing] = useState<string | null>(null);
  const [form] = Form.useForm();

  /* ── File manager modal ── */
  const [fileManagerTarget, setFileManagerTarget] = useState<string | null>(null);
  const [skillFiles, setSkillFiles] = useState<SkillFileItem[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>('');
  const [fileContentLoading, setFileContentLoading] = useState(false);
  const [fileDirty, setFileDirty] = useState(false);
  const [fileSaving, setFileSaving] = useState(false);
  const [newFilePath, setNewFilePath] = useState('');
  const [showNewFileInput, setShowNewFileInput] = useState(false);
  const fileInputRef = useRef<HTMLTextAreaElement>(null);

  /* ── Load pool ── */
  const loadPool = useCallback(async () => {
    setPoolLoading(true);
    try {
      const res = await apiClient.get('/skills/pool');
      setPoolSkills(res.data.skills ?? []);
    } catch {
      setPoolSkills([]);
      antdMessage.error(t('skillPool.loadFailed'));
    } finally {
      setPoolLoading(false);
    }
  }, []);

  /* ── Load agent skills ── */
  const loadAgentSkills = useCallback(async () => {
    setAgentSkillsLoading(true);
    try {
      const res = await apiClient.get(`/skills/agents/${agentId}`);
      setAgentSkills(res.data.skills ?? []);
    } catch {
      setAgentSkills([]);
    } finally {
      setAgentSkillsLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void loadPool();
  }, [loadPool]);

  useEffect(() => {
    void loadAgentSkills();
  }, [loadAgentSkills]);

  /* ── Create skill ── */
  const openCreate = useCallback(() => {
    setEditingName(null);
    form.resetFields();
    setFormOpen(true);
  }, [form]);

  /* ── Bulk upload zip to pool ── */
  const onBulkZipUpload = useCallback(async (file: File) => {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await apiClient.post('/skills/pool/upload-zip', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const imported: Array<{ name: string; dir_name?: string }> = res.data?.imported ?? [];
      const skipped: string[] = res.data?.skipped ?? [];
      if (imported.length > 0) {
        const names = imported.map(s => s.dir_name || s.name).join(', ');
        antdMessage.success(
          t('skillPool.zipImportSuccess', { count: String(imported.length), names }),
        );
      }
      if (skipped.length > 0) {
        antdMessage.warning(
          t('skillPool.zipImportSkipped', { count: String(skipped.length), names: skipped.join(', ') }),
        );
      }
      if (imported.length === 0 && skipped.length === 0) {
        antdMessage.error(t('skillPool.zipImportNoSkill'));
      }
      void loadPool();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail || t('skillPool.zipImportFailed'));
    }
    return false;
  }, [loadPool]);

  /* ── Edit skill (prefill from pool content) ── */
  const openEdit = useCallback(
    async (name: string) => {
      try {
        const res = await apiClient.get(`/skills/pool/${name}`);
        const data: SkillDetail = res.data;
        form.setFieldsValue({
          name: data.dir_name,
          description: data.description,
          content: stripFrontmatter(data.content).trim(),
        });
        setEditingName(data.dir_name);
        setFormOpen(true);
      } catch {
        antdMessage.error(t('skillPool.contentLoadFailed'));
      }
    },
    [form],
  );

  /* ── Submit create / edit ── */
  const submitForm = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editingName) {
        await apiClient.put(`/skills/pool/${editingName}`, {
          description: values.description,
          content: values.content,
        });
        antdMessage.success(t('skillPool.saveSuccess', { name: editingName }));
      } else {
        await apiClient.post('/skills/pool', values);
        antdMessage.success(t('skillPool.createSuccess', { name: values.name }));
      }
      setFormOpen(false);
      form.resetFields();
      void loadPool();
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail || t('common.saveFailed'));
    } finally {
      setSaving(false);
    }
  }, [editingName, form, loadPool]);

  /* ── View detail ── */
  const viewDetail = useCallback(async (name: string) => {
    setDetailLoading(true);
    setDetail(null);
    try {
      const res = await apiClient.get(`/skills/pool/${name}`);
      setDetail(res.data);
    } catch {
      antdMessage.error(t('skillPool.contentLoadFailed'));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  /* ── Delete from pool ── */
  const deleteSkill = useCallback(
    async (name: string) => {
      try {
        await apiClient.delete(`/skills/pool/${name}`);
        antdMessage.success(t('skillPool.deleteSuccess', { name }));
        void loadPool();
      } catch {
        antdMessage.error(t('common.deleteFailed'));
      }
    },
    [loadPool],
  );

  /* ── Install modal ── */
  const openInstall = useCallback(async (name: string) => {
    setInstallTarget(name);
    setSelectedAgents([agentId]);
    try {
      const res = await apiClient.get('/agents');
      setAgentOptions(Array.isArray(res.data) ? res.data : []);
    } catch {
      setAgentOptions([]);
    }
  }, [agentId]);

  const confirmInstall = useCallback(async () => {
    if (!installTarget || selectedAgents.length === 0) return;
    setInstalling(true);
    try {
      const [primary, ...rest] = selectedAgents;
      await apiClient.post(`/skills/pool/${installTarget}/install/${primary}`, {
        agent_ids: rest,
      });
      antdMessage.success(
        t('skillPool.installSuccessMulti', {
          name: installTarget,
          count: selectedAgents.length,
        }),
      );
      setInstallTarget(null);
      void loadAgentSkills();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      antdMessage.error(detail || t('skillPool.installFailed'));
    } finally {
      setInstalling(false);
    }
  }, [installTarget, selectedAgents, loadAgentSkills]);

  /* ── Sync pool content to installed copies ── */
  const syncSkill = useCallback(async (name: string) => {
    setSyncing(name);
    try {
      const res = await apiClient.post(`/skills/pool/${name}/sync`);
      const synced: string[] = res.data.synced_agents ?? [];
      if (synced.length === 0) {
        antdMessage.info(t('skillPool.syncNone', { name }));
      } else {
        antdMessage.success(
          t('skillPool.syncSuccess', { name, count: synced.length }),
        );
        void loadAgentSkills();
      }
    } catch {
      antdMessage.error(t('skillPool.syncFailed'));
    } finally {
      setSyncing(null);
    }
  }, [loadAgentSkills]);

  /* ── File manager: load file tree ── */
  const loadSkillFiles = useCallback(async (skillName: string) => {
    setFilesLoading(true);
    try {
      const res = await apiClient.get(`/skills/pool/${skillName}/files`);
      setSkillFiles(res.data.files ?? []);
    } catch {
      setSkillFiles([]);
    } finally {
      setFilesLoading(false);
    }
  }, []);

  /* ── File manager: open ── */
  const openFileManager = useCallback(async (name: string) => {
    setFileManagerTarget(name);
    setSelectedFilePath(null);
    setFileContent('');
    setFileDirty(false);
    setShowNewFileInput(false);
    await loadSkillFiles(name);
  }, [loadSkillFiles]);

  /* ── File manager: read file content ── */
  const loadFileContent = useCallback(async (skillName: string, filePath: string) => {
    setFileContentLoading(true);
    try {
      const res = await apiClient.get(`/skills/pool/${skillName}/files/content`, { params: { path: filePath } });
      setFileContent(res.data.content ?? '');
      setFileDirty(false);
    } catch {
      antdMessage.error(t('skillPool.contentLoadFailed'));
    } finally {
      setFileContentLoading(false);
    }
  }, []);

  /* ── File manager: save file ── */
  const saveFileContent = useCallback(async () => {
    if (!fileManagerTarget || !selectedFilePath) return;
    setFileSaving(true);
    try {
      await apiClient.put(
        `/skills/pool/${fileManagerTarget}/files/content`,
        { content: fileContent },
        { params: { path: selectedFilePath } },
      );
      setFileDirty(false);
      antdMessage.success(t('skillPool.fileSaved'));
    } catch {
      antdMessage.error(t('skillPool.fileSaveFailed'));
    } finally {
      setFileSaving(false);
    }
  }, [fileManagerTarget, selectedFilePath, fileContent]);

  /* ── File manager: create new file ── */
  const createNewFile = useCallback(async () => {
    if (!fileManagerTarget || !newFilePath.trim()) return;
    const trimmed = newFilePath.trim();
    try {
      await apiClient.put(
        `/skills/pool/${fileManagerTarget}/files/content`,
        { content: '' },
        { params: { path: trimmed } },
      );
      antdMessage.success(t('skillPool.fileCreated', { path: trimmed }));
      setShowNewFileInput(false);
      setNewFilePath('');
      await loadSkillFiles(fileManagerTarget);
      setSelectedFilePath(trimmed);
      setFileContent('');
      setFileDirty(false);
    } catch {
      antdMessage.error(t('skillPool.fileCreateFailed'));
    }
  }, [fileManagerTarget, newFilePath, loadSkillFiles]);

  /* ── File manager: delete file ── */
  const deleteSkillFile = useCallback(async (filePath: string, isDir: boolean) => {
    if (!fileManagerTarget) return;
    try {
      await apiClient.delete(`/skills/pool/${fileManagerTarget}/files`, {
        params: { path: filePath, recursive: isDir },
      });
      antdMessage.success(t('skillPool.fileDeleted', { path: filePath }));
      if (selectedFilePath === filePath) {
        setSelectedFilePath(null);
        setFileContent('');
        setFileDirty(false);
      }
      await loadSkillFiles(fileManagerTarget);
    } catch {
      antdMessage.error(t('skillPool.fileDeleteFailed'));
    }
  }, [fileManagerTarget, selectedFilePath, loadSkillFiles]);

  /* ── File manager: upload handler ── */
  const onSkillFileUpload = useCallback(async (file: File, uploadPath = '') => {
    if (!fileManagerTarget) return false;
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await apiClient.post(`/skills/pool/${fileManagerTarget}/files/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        params: { path: uploadPath },
      });
      if (file.name.toLowerCase().endsWith('.zip')) {
        const count = res.data?.count ?? 0;
        antdMessage.success(t('skillPool.zipExtracted', { count: String(count) }));
      } else {
        antdMessage.success(t('skillPool.fileUploaded'));
      }
      await loadSkillFiles(fileManagerTarget);
    } catch {
      antdMessage.error(t('skillPool.uploadFailed'));
    }
    return false;
  }, [fileManagerTarget, loadSkillFiles]);

  /* ── Build tree data from flat file list ── */
  const buildFileTree = useCallback((files: SkillFileItem[]): DataNode[] => {
    interface TreeNode extends DataNode {
      children?: TreeNode[];
    }
    const nodeMap = new Map<string, TreeNode>();
    const roots: TreeNode[] = [];

    const sorted = [...files].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return a.path.localeCompare(b.path);
    });

    for (const f of sorted) {
      const parts = f.path.split('/');
      let parentPath = '';

      for (let i = 0; i < parts.length; i++) {
        const partPath = parts.slice(0, i + 1).join('/');
        const isLeaf = i === parts.length - 1 && !f.is_dir;

        if (!nodeMap.has(partPath)) {
          const node: TreeNode = {
            key: partPath,
            title: parts[i],
            isLeaf,
            icon: f.is_dir || !isLeaf
              ? <FolderFilled style={{ color: 'var(--google-chart-3)' }} />
              : <FileTextOutlined style={{ color: 'var(--google-muted-foreground)' }} />,
          };
          if (!isLeaf) node.children = [];
          nodeMap.set(partPath, node);

          if (parentPath && nodeMap.has(parentPath)) {
            nodeMap.get(parentPath)!.children!.push(node);
          } else if (!parentPath) {
            roots.push(node);
          }
        }

        parentPath = partPath;
      }
    }

    return roots;
  }, []);

  /* ── Tree select handler ── */
  const onFileTreeSelect = useCallback((keys: React.Key[]) => {
    if (keys.length === 0 || !fileManagerTarget) return;
    const path = keys[0] as string;
    const file = skillFiles.find(f => f.path === path);
    if (!file || file.is_dir) return;
    setSelectedFilePath(path);
    void loadFileContent(fileManagerTarget, path);
  }, [fileManagerTarget, skillFiles, loadFileContent]);

  /* ── Uninstall from agent ── */
  const uninstallSkill = useCallback(
    async (name: string) => {
      try {
        await apiClient.delete(`/skills/agents/${agentId}/${name}`);
        antdMessage.success(t('skillPool.uninstallSuccess', { agent: agentId, name }));
        void loadAgentSkills();
      } catch {
        antdMessage.error(t('skillPool.uninstallFailed'));
      }
    },
    [agentId, loadAgentSkills],
  );

  const installedNames = new Set(agentSkills.map((s) => s.dir_name));

  const agentSkillColumns: ColumnsType<SkillItem> = [
    {
      title: t('skillPool.skill'),
      dataIndex: 'name',
      width: 180,
      render: (name: string, record) => (
        <Space>
          <ThunderboltOutlined style={{ color: 'var(--google-primary)' }} />
          <Text code style={{ fontSize: 13 }}>{record.dir_name}</Text>
          {name !== record.dir_name && (
            <Text type="secondary" style={{ fontSize: 12 }}>{name}</Text>
          )}
        </Space>
      ),
    },
    { title: t('common.description'), dataIndex: 'description', ellipsis: true },
    {
      title: t('common.actions'),
      width: 90,
      render: (_: unknown, record) => (
        <Popconfirm
          title={t('skillPool.uninstallConfirm', { agent: agentId, skill: record.dir_name })}
          onConfirm={() => void uninstallSkill(record.dir_name)}
        >
          <Button size="small" type="link" danger icon={<DeleteOutlined />}>
            {t('skillPool.uninstall')}
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <GooglePageHeader
        icon={<RocketOutlined />}
        title={t('skillPool.title')}
        subtitle={t('skillPool.subtitle', { count: poolSkills.length })}
        extra={
          <Space>
            <Upload
              accept=".zip"
              showUploadList={false}
              beforeUpload={(file) => { void onBulkZipUpload(file); return false; }}
            >
              <Button
                size="small"
                icon={<CloudUploadOutlined />}
              >
                {t('skillPool.uploadZipToPool')}
              </Button>
            </Upload>
            <Button
              size="small"
              type="primary"
              icon={<AppstoreAddOutlined />}
              onClick={openCreate}
            >
              {t('skillPool.createSkill')}
            </Button>
          </Space>
        }
      />

      {/* ── Skill pool ── */}
      <GoogleCard style={{ marginBottom: 'var(--google-space-8)' }}>
        <Spin spinning={poolLoading}>
          {poolSkills.length === 0 && !poolLoading ? (
            <Empty description={t('skillPool.emptyPool')} />
          ) : (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
                gap: 'var(--google-space-6)',
              }}
            >
              {poolSkills.map((skill) => {
                const installed = installedNames.has(skill.dir_name);
                return (
                  <div
                    key={skill.dir_name}
                    style={{
                      border: `1px solid ${installed ? 'var(--google-primary)' : 'var(--google-border)'}`,
                      borderRadius: 'var(--google-radius-md)',
                      padding: 'var(--google-space-6)',
                      background: installed ? 'var(--google-secondary)' : 'var(--google-card)',
                    }}
                  >
                    <Space direction="vertical" size="small" style={{ width: '100%' }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Text code strong style={{ fontSize: 13 }}>{skill.dir_name}</Text>
                        {installed && (
                          <Tag
                            style={{
                              fontSize: 11,
                              color: 'var(--google-primary)',
                              borderColor: 'var(--google-primary)',
                              background: 'var(--google-background)',
                            }}
                          >
                            {t('skillPool.installed')}
                          </Tag>
                        )}
                      </Space>
                      <Paragraph
                        type="secondary"
                        style={{ fontSize: 12, marginBottom: 0 }}
                        ellipsis={{ rows: 2, tooltip: skill.description }}
                      >
                        {skill.description || t('skillPool.noDescription')}
                      </Paragraph>
                      <Space size="small" wrap>
                        <Button
                          size="small"
                          type="link"
                          icon={<EyeOutlined />}
                          onClick={() => void viewDetail(skill.dir_name)}
                        >
                          {t('skillPool.view')}
                        </Button>
                        <Button
                          size="small"
                          type="link"
                          icon={<EditOutlined />}
                          onClick={() => void openEdit(skill.dir_name)}
                        >
                          {t('skillPool.edit')}
                        </Button>
                        <Button
                          size="small"
                          type="link"
                          icon={<FileTextOutlined />}
                          onClick={() => void openFileManager(skill.dir_name)}
                        >
                          {t('skillPool.fileManager')}
                          {skill.file_count && skill.file_count > 1 ? (
                            <Tag style={{ fontSize: 10, marginLeft: 4, padding: '0 4px' }}>{skill.file_count}</Tag>
                          ) : null}
                        </Button>
                        <Button
                          size="small"
                          type="link"
                          icon={<DownloadOutlined />}
                          onClick={() => void openInstall(skill.dir_name)}
                        >
                          {t('skillPool.install')}
                        </Button>
                        <Button
                          size="small"
                          type="link"
                          icon={<SyncOutlined />}
                          loading={syncing === skill.dir_name}
                          onClick={() => void syncSkill(skill.dir_name)}
                        >
                          {t('skillPool.sync')}
                        </Button>
                        <Popconfirm
                          title={t('skillPool.deleteFromPool', { skill: skill.dir_name })}
                          onConfirm={() => void deleteSkill(skill.dir_name)}
                        >
                          <Button size="small" type="link" danger icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </Space>
                    </Space>
                  </div>
                );
              })}
            </div>
          )}
        </Spin>
      </GoogleCard>

      {/* ── Installed skills ── */}
      <GoogleCard
        title={t('skillPool.installedSkills')}
        extra={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('skillPool.currentAgent')}：<Text code style={{ fontSize: 12 }}>{agentId}</Text>
          </Text>
        }
        bodyStyle={{ padding: 0 }}
      >
        <Table<SkillItem>
          rowKey="dir_name"
          size="middle"
          loading={agentSkillsLoading}
          columns={agentSkillColumns}
          dataSource={agentSkills}
          pagination={false}
          locale={{
            emptyText: (
              <Text type="secondary">
                {t('skillPool.notInstalledHint', { agent: agentId })}
              </Text>
            ),
          }}
        />
      </GoogleCard>

      {/* ── Create / edit modal ── */}
      <Modal
        title={editingName ? t('skillPool.editModalTitle', { name: editingName }) : t('skillPool.createModalTitle')}
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        onOk={() => void submitForm()}
        confirmLoading={saving}
        okText={editingName ? t('common.save') : t('common.create')}
        cancelText={t('common.cancel')}
        width={640}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            label={t('skillPool.skillName')}
            name="name"
            rules={[
              { required: true, message: t('skillPool.skillNameRequired') },
              {
                pattern: /^[a-z0-9][a-z0-9-]{0,63}$/,
                message: t('skillPool.skillNamePattern'),
              },
            ]}
          >
            <Input placeholder={t('skillPool.nameExample')} disabled={editingName !== null} />
          </Form.Item>
          <Form.Item
            label={t('skillPool.skillDescription')}
            name="description"
            rules={[{ required: true, message: t('skillPool.skillDescriptionRequired') }]}
          >
            <Input.TextArea rows={2} placeholder={t('skillPool.skillDescriptionPlaceholder')} />
          </Form.Item>
          <Form.Item label={t('skillPool.skillContent')} name="content">
            <Input.TextArea rows={10} placeholder={'# 技能标题\n\n## 何时使用\n\n…'} />
          </Form.Item>
        </Form>
      </Modal>

      {/* ── Install modal (multi-agent) ── */}
      <Modal
        title={installTarget ? t('skillPool.installModalTitle', { name: installTarget }) : t('skillPool.install')}
        open={installTarget !== null}
        onCancel={() => setInstallTarget(null)}
        onOk={() => void confirmInstall()}
        confirmLoading={installing}
        okText={t('skillPool.install')}
        okButtonProps={{ disabled: selectedAgents.length === 0 }}
        cancelText={t('common.cancel')}
        width={420}
      >
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
          {t('skillPool.selectAgents')}
        </Text>
        {agentOptions.length === 0 ? (
          <Spin />
        ) : (
          <Checkbox.Group
            value={selectedAgents}
            onChange={(values) => setSelectedAgents(values as string[])}
            style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
          >
            {agentOptions.map((agent) => (
              <Checkbox key={agent.agent_id} value={agent.agent_id}>
                <Text code style={{ fontSize: 13 }}>{agent.agent_id}</Text>
              </Checkbox>
            ))}
          </Checkbox.Group>
        )}
      </Modal>

      {/* ── Detail modal (markdown rendered) ── */}
      <Modal
        title={detail ? t('skillPool.skillDetailTitle', { name: detail.dir_name }) : t('skillPool.skillDetail')}
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

      {/* ── File manager modal ── */}
      <Modal
        title={fileManagerTarget ? t('skillPool.fileManagerTitle', { name: fileManagerTarget }) : t('skillPool.fileManager')}
        open={fileManagerTarget !== null}
        onCancel={() => { setFileManagerTarget(null); setSelectedFilePath(null); setFileContent(''); setFileDirty(false); }}
        footer={null}
        width={900}
        styles={{ body: { padding: 0, height: 520, overflow: 'hidden' } }}
        destroyOnHidden
      >
        <div style={{ display: 'flex', height: 520 }}>
          {/* ── Left: file tree ── */}
          <div style={{ width: 240, borderRight: '1px solid var(--google-border)', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--google-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text strong style={{ fontSize: 12 }}>{t('skillPool.files')}</Text>
              <Space size={2}>
                <Tooltip title={t('skillPool.newFile')}>
                  <Button type="text" size="small" icon={<FileAddOutlined />} style={{ fontSize: 12 }}
                    onClick={() => setShowNewFileInput(v => !v)} />
                </Tooltip>
                <Upload
                  accept="*"
                  showUploadList={false}
                  beforeUpload={(file) => { void onSkillFileUpload(file, ''); return false; }}
                >
                  <Tooltip title={t('skillPool.uploadFile')}>
                    <Button type="text" size="small" icon={<UploadOutlined />} style={{ fontSize: 12 }} />
                  </Tooltip>
                </Upload>
                <Upload
                  accept=".zip"
                  showUploadList={false}
                  beforeUpload={(file) => { void onSkillFileUpload(file, ''); return false; }}
                >
                  <Tooltip title={t('skillPool.uploadZip')}>
                    <Button type="text" size="small" icon={<DownloadOutlined />} style={{ fontSize: 12 }} />
                  </Tooltip>
                </Upload>
                <Tooltip title={t('skillPool.refreshFiles')}>
                  <Button type="text" size="small" icon={<ReloadOutlined />} style={{ fontSize: 12 }}
                    onClick={() => fileManagerTarget && void loadSkillFiles(fileManagerTarget)} />
                </Tooltip>
              </Space>
            </div>

            {/* New file input */}
            {showNewFileInput && (
              <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--google-border)' }}>
                <Space size={4} style={{ width: '100%' }}>
                  <Input
                    size="small"
                    placeholder={t('skillPool.fileNamePlaceholder')}
                    value={newFilePath}
                    onChange={(e) => setNewFilePath(e.target.value)}
                    onPressEnter={() => void createNewFile()}
                    style={{ fontSize: 12, flex: 1 }}
                    autoFocus
                  />
                  <Button size="small" type="primary" onClick={() => void createNewFile()} style={{ fontSize: 11 }}>
                    {t('common.create')}
                  </Button>
                </Space>
              </div>
            )}

            <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}>
              {filesLoading ? (
                <div style={{ textAlign: 'center', padding: '24px' }}><Spin size="small" /></div>
              ) : skillFiles.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '24px' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>{t('skillPool.emptyPool')}</Text>
                </div>
              ) : (
                <Tree
                  showIcon
                  blockNode
                  treeData={buildFileTree(skillFiles)}
                  onSelect={(keys) => onFileTreeSelect(keys)}
                  selectedKeys={selectedFilePath ? [selectedFilePath] : []}
                  defaultExpandAll
                  icon={(props: { expanded?: boolean; isLeaf?: boolean }) =>
                    props.isLeaf ? null : props.expanded
                      ? <FolderFilled style={{ color: 'var(--google-chart-3)' }} />
                      : <FolderOutlined style={{ color: 'var(--google-chart-3)' }} />}
                  style={{ background: 'transparent', fontSize: 13 }}
                  titleRender={(node) => (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <span>{node.title as string}</span>
                      {!node.isLeaf && (
                        <Popconfirm
                          title={t('skillPool.deleteFolderConfirm', { path: node.key as string })}
                          onConfirm={(e) => { e?.stopPropagation(); void deleteSkillFile(node.key as string, true); }}
                          onCancel={(e) => e?.stopPropagation()}
                        >
                          <Button type="text" size="small" danger icon={<DeleteOutlined />}
                            style={{ fontSize: 10, padding: '0 2px', lineHeight: 1 }}
                            onClick={(e) => e.stopPropagation()} />
                        </Popconfirm>
                      )}
                      {node.isLeaf && node.key !== 'SKILL.md' && (
                        <Popconfirm
                          title={t('skillPool.deleteFileConfirm', { path: node.key as string })}
                          onConfirm={(e) => { e?.stopPropagation(); void deleteSkillFile(node.key as string, false); }}
                          onCancel={(e) => e?.stopPropagation()}
                        >
                          <Button type="text" size="small" danger icon={<DeleteOutlined />}
                            style={{ fontSize: 10, padding: '0 2px', lineHeight: 1 }}
                            onClick={(e) => e.stopPropagation()} />
                        </Popconfirm>
                      )}
                    </span>
                  )}
                />
              )}
            </div>
          </div>

          {/* ── Right: editor ── */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            {!selectedFilePath ? (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    <span style={{ color: 'var(--google-muted-foreground)', fontSize: 13 }}>
                      {t('skillPool.selectFileToEdit')}
                    </span>
                  }
                />
              </div>
            ) : (
              <>
                <div style={{ padding: '6px 12px', borderBottom: '1px solid var(--google-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Space size={8}>
                    <FileTextOutlined style={{ color: 'var(--google-muted-foreground)' }} />
                    <Text code style={{ fontSize: 12 }}>{selectedFilePath}</Text>
                    {fileDirty && <Tag color="orange" style={{ fontSize: 10 }}>modified</Tag>}
                  </Space>
                  <Button
                    size="small"
                    type="primary"
                    disabled={!fileDirty}
                    loading={fileSaving}
                    onClick={() => void saveFileContent()}
                  >
                    {t('common.save')}
                  </Button>
                </div>
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  {fileContentLoading ? (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                      <Spin />
                    </div>
                  ) : (
                    <textarea
                      ref={fileInputRef}
                      value={fileContent}
                      onChange={(e) => { setFileContent(e.target.value); setFileDirty(true); }}
                      spellCheck={false}
                      style={{
                        width: '100%',
                        height: '100%',
                        border: 'none',
                        outline: 'none',
                        resize: 'none',
                        padding: '12px',
                        fontFamily: 'var(--google-font-family-code, "JetBrains Mono", monospace)',
                        fontSize: 13,
                        lineHeight: 1.6,
                        background: 'transparent',
                        color: 'inherit',
                        tabSize: 2,
                      }}
                      onKeyDown={(e) => {
                        // Ctrl+S / Cmd+S to save
                        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                          e.preventDefault();
                          void saveFileContent();
                        }
                        // Tab inserts spaces
                        if (e.key === 'Tab') {
                          e.preventDefault();
                          const ta = e.currentTarget;
                          const start = ta.selectionStart;
                          const end = ta.selectionEnd;
                          const val = ta.value;
                          setFileContent(val.substring(0, start) + '  ' + val.substring(end));
                          setFileDirty(true);
                          requestAnimationFrame(() => {
                            ta.selectionStart = ta.selectionEnd = start + 2;
                          });
                        }
                      }}
                    />
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </Modal>
    </div>
  );
}
