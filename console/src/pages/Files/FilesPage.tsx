import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Button,
  Dropdown,
  Empty,
  Input,
  Popconfirm,
  Segmented,
  Spin,
  Tabs,
  Tag,
  Tooltip,
  Tree,
  Typography,
  Upload,
  message as antdMessage,
} from 'antd';
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CloudServerOutlined,
  ControlOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  FileMarkdownOutlined,
  FileOutlined,
  FolderFilled,
  FolderOutlined,
  DownloadOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  SyncOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import type { DataNode, EventDataNode } from 'antd/es/tree';
import type { UploadProps } from 'antd';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import MarkdownView from '../../components/MarkdownView';
import { apiClient } from '../../api/client';
import { useAgentId } from '../../stores/agentStore';
import { useI18n } from '../../i18n';

const { Text } = Typography;

/* ───────── Constants ───────── */
const KERNEL_FILES = ['agent.md', 'profile.md', 'soul.md', 'bootstrap.md'] as const;
const KERNEL_LABEL_KEYS: Record<string, string> = {
  'agent.md': 'files.agentIdentity',
  'profile.md': 'files.profileConfig',
  'soul.md': 'files.corePersona',
  'bootstrap.md': 'files.bootstrap',
};

/* Sync badge colours for mirrored workspace files (backend attaches
   sync_state to tree items inside that root). */
const SYNC_BADGE: Record<string, { color: string; i18n: string }> = {
  synced: { color: '#34a853', i18n: 'files.syncStateSynced' },
  local_modified: { color: '#f9ab00', i18n: 'files.syncStateLocalModified' },
  remote_modified: { color: '#4285f4', i18n: 'files.syncStateRemoteModified' },
  remote_only: { color: '#4285f4', i18n: 'files.syncStateRemoteOnly' },
  local_only: { color: '#9aa0a6', i18n: 'files.syncStateLocalOnly' },
  conflict: { color: 'var(--google-destructive)', i18n: 'files.syncStateConflict' },
  mixed: { color: '#f9ab00', i18n: 'files.syncStateMixed' },
};

/* ───────── Types ───────── */
interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number | null;
  sync_state?: string;
}

interface OpenTab {
  path: string;
  name: string;
  isKernel: boolean;
}

/* ───────── Helpers ───────── */
function formatSize(size?: number | null): string {
  if (size == null) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function toTreeNodes(
  items: FileItem[],
  renderAction?: (item: FileItem) => React.ReactNode,
  renderBadge?: (item: FileItem) => React.ReactNode,
): DataNode[] {
  return items.map((item) => ({
    key: item.path,
    title: item.is_dir ? (
      <span className="files-tree-dir">
        {renderBadge?.(item)}
        <span className="files-tree-name">{item.name}</span>
        {renderAction?.(item)}
      </span>
    ) : (
      <span className="files-tree-file">
        {renderBadge?.(item)}
        <span className="files-tree-name">{item.name}</span>
        {item.size != null && (
          <Text className="files-tree-size">{formatSize(item.size)}</Text>
        )}
        {renderAction?.(item)}
      </span>
    ),
    isLeaf: !item.is_dir,
    icon: item.is_dir ? undefined : <FileOutlined style={{ color: 'var(--google-muted-foreground)' }} />,
  }));
}

/* ───────── Main Page ───────── */
export default function FilesPage() {
  const agentId = useAgentId();
  const { t } = useI18n();
  const [treeData, setTreeData] = useState<DataNode[]>([]);
  const [treeLoading, setTreeLoading] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
  /** root-level file names from the latest tree listing (kernel-file existence check) */
  const [rootNames, setRootNames] = useState<string[]>([]);

  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activeKey, setActiveKey] = useState<string>('');
  const [contents, setContents] = useState<Record<string, string>>({});
  const [originals, setOriginals] = useState<Record<string, string>>({});
  const [fileLoading, setFileLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  /** Per-tab editor view mode; markdown files default to rendered preview. */
  const [viewModes, setViewModes] = useState<Record<string, 'edit' | 'preview'>>({});

  /* Deep link: /files/:agentId?open=<workspace-rel-path> opens that file. */
  const [searchParams] = useSearchParams();
  const openParam = searchParams.get('open');
  const revealedRef = useRef(false);

  const contentsRef = useRef(contents);
  contentsRef.current = contents;

  /* Stable tree-node builder; the delete action renderer is wired through a
     ref to avoid a reloadTree ↔ deleteEntry dependency cycle. */
  const renderTreeActionRef = useRef<(item: FileItem) => React.ReactNode>(() => null);
  /* Sync badge shown before mirrored workspace file names. */
  const renderSyncBadge = useCallback(
    (item: FileItem) => {
      const state = item.sync_state;
      if (!state) return null;
      const meta = SYNC_BADGE[state];
      if (!meta) return null;
      return (
        <Tooltip title={t(meta.i18n)}>
          <span className="files-sync-dot" style={{ background: meta.color }} />
        </Tooltip>
      );
    },
    [t],
  );
  const toNodes = useCallback(
    (items: FileItem[]) =>
      toTreeNodes(items, (item) => renderTreeActionRef.current(item), renderSyncBadge),
    [renderSyncBadge],
  );

  /* ── Load directory listing ── */
  const loadDir = useCallback(
    async (path = ''): Promise<FileItem[]> => {
      const res = await apiClient.get(`/agents/${agentId}/files/tree`, { params: { path } });
      return (res.data.items ?? []) as FileItem[];
    },
    [agentId],
  );

  const reloadTree = useCallback(async () => {
    setTreeLoading(true);
    try {
      const items = await loadDir('');
      setTreeData(toNodes(items));
      setRootNames(items.map((i) => i.name));
    } catch {
      setTreeData([]);
    } finally {
      setTreeLoading(false);
    }
  }, [loadDir, toNodes]);

  /* ── Sandbox sync ── */
  interface SyncStatusData {
    enabled: boolean;
    container_alive?: boolean;
    container_root?: string;
    last_sync_at?: string | null;
    states?: Record<string, string>;
    summary?: { synced: number; pending_push: number; pending_pull: number; conflict: number };
  }
  const [syncStatus, setSyncStatus] = useState<SyncStatusData | null>(null);
  const [syncing, setSyncing] = useState(false);

  const loadSyncStatus = useCallback(async () => {
    try {
      const res = await apiClient.get(`/agents/${agentId}/files/sync/status`);
      setSyncStatus((res.data as SyncStatusData)?.enabled ? (res.data as SyncStatusData) : null);
    } catch {
      setSyncStatus(null);
    }
  }, [agentId]);

  const runSync = useCallback(
    async (direction: 'push' | 'pull' | 'both') => {
      setSyncing(true);
      try {
        const res = await apiClient.post(`/agents/${agentId}/files/sync`, { direction });
        const r = res.data ?? {};
        const parts: string[] = [];
        if ((r.pushed ?? []).length) parts.push(t('files.syncPushedCount', { count: r.pushed.length }));
        if ((r.pulled ?? []).length) parts.push(t('files.syncPulledCount', { count: r.pulled.length }));
        if ((r.conflicts ?? []).length) parts.push(t('files.syncConflictsCount', { count: r.conflicts.length }));
        if ((r.errors ?? []).length) {
          antdMessage.warning(t('files.syncErrorsCount', { count: r.errors.length }));
        }
        antdMessage.success(
          parts.length ? t('files.syncDone', { detail: parts.join(' · ') }) : t('files.syncNothing'),
        );
        void reloadTree();
        void loadSyncStatus();
      } catch (err) {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        antdMessage.error(detail || t('files.syncFailed'));
      } finally {
        setSyncing(false);
      }
    },
    [agentId, reloadTree, loadSyncStatus, t],
  );

  /* Reset everything when switching agents. */
  useEffect(() => {
    setTabs([]);
    setActiveKey('');
    setContents({});
    setOriginals({});
    setExpandedKeys([]);
    setRootNames([]);
    setViewModes({});
    revealedRef.current = false;
    void reloadTree();
    void loadSyncStatus();
  }, [agentId, reloadTree, loadSyncStatus]);

  /* ── Lazy-load subdirectories ── */
  const onLoadData = useCallback(
    async (node: EventDataNode<DataNode>) => {
      if (node.children?.length) return;
      try {
        const items = await loadDir(String(node.key));
        setTreeData((origin) => updateTreeChildren(origin, String(node.key), toNodes(items)));
      } catch {
        antdMessage.error(t('files.loadFailed'));
      }
    },
    [loadDir, toNodes],
  );

  /* ── Open a file in the editor ── */
  const openFile = useCallback(
    async (path: string, name: string, isKernel: boolean) => {
      if (!path) return;
      setActiveKey(path);
      if (tabs.some((t) => t.path === path)) return;
      setTabs((prev) => [...prev, { path, name, isKernel }]);
      if (contents[path] !== undefined) return;

      setFileLoading(true);
      try {
        const url = isKernel
          ? `/agents/${agentId}/kernel/${path}`
          : `/agents/${agentId}/files/content`;
        const res = await apiClient.get(url, isKernel ? undefined : { params: { path } });
        const content: string = res.data.content ?? '';
        setContents((prev) => ({ ...prev, [path]: content }));
        setOriginals((prev) => ({ ...prev, [path]: content }));
      } catch (err) {
        const resp = (err as { response?: { status?: number; data?: { detail?: string } } })?.response;
        const detail = resp?.data?.detail;
        if (resp?.status === 404) {
          antdMessage.warning(t('files.fileNotFound'));
          void reloadTree();
        } else {
          antdMessage.error(detail === 'Binary file not supported' ? t('files.binaryNotSupported') : t('files.readFailed'));
        }
        setTabs((prev) => prev.filter((t) => t.path !== path));
        setActiveKey('');
      } finally {
        setFileLoading(false);
      }
    },
    [agentId, contents, tabs, reloadTree],
  );

  /* ── Deep link: expand ancestors and open the ?open= target file ── */
  const revealFile = useCallback(
    async (rawPath: string) => {
      const path = rawPath.replace(/\\/g, '/').replace(/^\/+/, '').replace(/^\.\//, '');
      if (!path) return;
      const segments = path.split('/');
      const ancestors: string[] = [];
      for (let i = 1; i < segments.length; i++) ancestors.push(segments.slice(0, i).join('/'));
      for (const dir of ancestors) {
        try {
          const items = await loadDir(dir);
          setTreeData((origin) => updateTreeChildren(origin, dir, toNodes(items)));
        } catch {
          /* ancestor missing — openFile below will surface the error */
        }
      }
      if (ancestors.length) {
        setExpandedKeys((prev) => Array.from(new Set([...prev, ...ancestors])));
      }
      const name = segments[segments.length - 1];
      void openFile(path, name, (KERNEL_FILES as readonly string[]).includes(path));
    },
    [loadDir, openFile, toNodes],
  );

  useEffect(() => {
    if (!openParam || treeLoading || revealedRef.current) return;
    revealedRef.current = true;
    void revealFile(openParam);
  }, [openParam, treeLoading, revealFile]);

  const onTreeSelect = useCallback(
    (_keys: React.Key[], info: { node: EventDataNode<DataNode> }) => {
      const node = info.node;
      if (!node.isLeaf) return;
      const path = String(node.key);
      const name = path.split('/').pop() ?? path;
      void openFile(path, name, (KERNEL_FILES as readonly string[]).includes(path));
    },
    [openFile],
  );

  /* ── Save active file ── */
  const saveFile = useCallback(
    async (path?: string) => {
      const target = path ?? activeKey;
      if (!target) return;
      const tab = tabs.find((t) => t.path === target);
      const content = contentsRef.current[target];
      if (content === undefined) return;
      if (content === originals[target]) {
        antdMessage.info(t('files.noChanges'));
        return;
      }
      setSaving(true);
      try {
        await apiClient.put(
          `/agents/${agentId}/files/content`,
          { content },
          { params: { path: target } },
        );
        setOriginals((prev) => ({ ...prev, [target]: content }));
        if (tab?.isKernel) {
          antdMessage.success(t('files.savedSystemPrompt'));
        } else {
          antdMessage.success(t('files.savedSuccess'));
        }
      } catch {
        antdMessage.error(t('common.saveFailed'));
      } finally {
        setSaving(false);
      }
    },
    [activeKey, agentId, originals, tabs],
  );

  /* ── Delete a workspace file or directory ── */
  const deleteEntry = useCallback(
    async (path: string, isDir: boolean) => {
      const name = path.split('/').pop() ?? path;
      try {
        await apiClient.delete(`/agents/${agentId}/files`, {
          params: { path, ...(isDir ? { recursive: true } : {}) },
        });
        antdMessage.success(t('common.deletedSuccess', { name }));
        // Close tabs covered by the deletion (the file itself, or anything
        // inside a recursively removed directory).
        const covered = (p: string) => p === path || (isDir && p.startsWith(`${path}/`));
        setTabs((prev) => prev.filter((tab) => !covered(tab.path)));
        setActiveKey((prev) => (covered(prev) ? '' : prev));
        // Refresh the parent listing so the node disappears from the tree.
        const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
        if (!parent) {
          const items = await loadDir('');
          setTreeData(toNodes(items));
          setRootNames(items.map((i) => i.name));
        } else {
          try {
            const items = await loadDir(parent);
            setTreeData((origin) => updateTreeChildren(origin, parent, toNodes(items)));
          } catch {
            const items = await loadDir('');
            setTreeData(toNodes(items));
          }
        }
      } catch {
        antdMessage.error(t('common.deleteFailed'));
      }
    },
    [agentId, loadDir, toNodes, t],
  );

  /* Hover-revealed delete action rendered inside tree node titles. */
  renderTreeActionRef.current = (item: FileItem) => (
    <Popconfirm
      title={t('files.delete')}
      description={t(item.is_dir ? 'files.deleteDirConfirm' : 'files.deleteFileConfirm', { name: item.name })}
      okText={t('common.delete')}
      cancelText={t('common.cancel')}
      okButtonProps={{ danger: true }}
      onConfirm={() => void deleteEntry(item.path, item.is_dir)}
    >
      <span className="files-tree-del" onClick={(e) => e.stopPropagation()}>
        <DeleteOutlined />
      </span>
    </Popconfirm>
  );

  const handleEditorKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void saveFile();
      }
    },
    [saveFile],
  );

  /* ── Tabs ── */
  const onTabEdit = useCallback(
    (key: React.MouseEvent | React.KeyboardEvent | string, action: 'add' | 'remove') => {
      if (action !== 'remove' || typeof key !== 'string') return;
      setTabs((prev) => {
        const idx = prev.findIndex((t) => t.path === key);
        const next = prev.filter((t) => t.path !== key);
        if (activeKey === key) {
          const fallback = next[Math.max(0, idx - 1)];
          setActiveKey(fallback ? fallback.path : '');
        }
        return next;
      });
    },
    [activeKey],
  );

  /* ── Upload ── */
  const activeTab = tabs.find((t) => t.path === activeKey);
  const uploadDir = activeTab && !activeTab.isKernel
    ? activeTab.path.includes('/') ? activeTab.path.slice(0, activeTab.path.lastIndexOf('/')) : ''
    : '';

  const uploadProps: UploadProps = {
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      const form = new FormData();
      form.append('file', file);
      try {
        await apiClient.post(
          `/agents/${agentId}/files/upload`,
          form,
          { params: { path: uploadDir } },
        );
        antdMessage.success(t('files.uploadSuccess', { name: (file as File).name }));
        onSuccess?.({}, new XMLHttpRequest());
        // Refresh root listing (subdir refresh happens on re-expand).
        void reloadTree();
      } catch (err) {
        antdMessage.error(t('files.uploadFailed'));
        onError?.(err as Error);
      }
    },
  };

  /* ── Render ── */
  const dirty = activeKey !== '' && contents[activeKey] !== originals[activeKey];
  const activeIsKernel = (KERNEL_FILES as readonly string[]).includes(activeKey);
  const isMarkdown = (name: string) => name.toLowerCase().endsWith('.md');
  const modeFor = (tab: OpenTab): 'edit' | 'preview' =>
    viewModes[tab.path] ?? (isMarkdown(tab.name) ? 'preview' : 'edit');
  /* Kernel files that actually exist on disk; before the first listing loads,
     show them all (tolerate missing listing). */
  const visibleKernelFiles = rootNames.length
    ? KERNEL_FILES.filter((name) => rootNames.includes(name))
    : KERNEL_FILES;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <GooglePageHeader
        icon={<RobotOutlined />}
        title={t('files.title')}
        extra={
          <Text code style={{ fontSize: 11 }}>
            {agentId}
          </Text>
        }
      />

      <GoogleCard
        bodyStyle={{ padding: 0, flex: 1, display: 'flex', flexDirection: 'column' }}
        style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
      >
        <div style={{
          display: 'flex', flex: 1, minHeight: 0,
          background: 'var(--google-card)',
          borderRadius: 'var(--google-radius-lg)', overflow: 'hidden',
        }}>
          {/* ── Left: file navigator ── */}
          <div style={{
            width: 280, flexShrink: 0, borderRight: '1px solid var(--google-border)',
            display: 'flex', flexDirection: 'column', background: 'var(--google-muted)',
          }}>
            <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--google-space-4) var(--google-space-4) var(--google-space-8)' }}>
              {/* Kernel files */}
              <div style={{
                fontSize: 12, fontWeight: 600, color: 'var(--google-muted-foreground)', letterSpacing: 0.5,
                margin: 'var(--google-space-1) var(--google-space-1) var(--google-space-3)',
              }}>
                {t('files.kernelFiles')}
              </div>
              {visibleKernelFiles.map((name) => {
                const active = activeKey === name;
                return (
                  <div
                    key={name}
                    onClick={() => void openFile(name, name, true)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 'var(--google-space-3)',
                      padding: 'var(--google-space-2) var(--google-space-4)', borderRadius: 'var(--google-radius-lg)', cursor: 'pointer',
                      marginBottom: 'var(--google-space-1)', fontSize: 13,
                      background: active ? 'rgba(66, 133, 244, 0.08)' : 'transparent',
                      color: active ? 'var(--google-primary)' : 'var(--google-foreground)',
                      border: active ? '1px solid rgba(66, 133, 244, 0.25)' : '1px solid transparent',
                    }}
                  >
                    <ControlOutlined style={{ color: active ? 'var(--google-primary)' : 'var(--google-chart-3)' }} />
                    <span style={{ fontWeight: active ? 600 : 400 }}>{name}</span>
                    <Text style={{ fontSize: 11, marginLeft: 'auto', color: 'var(--google-muted-foreground)' }}>
                      {t(KERNEL_LABEL_KEYS[name] ?? '')}
                    </Text>
                  </div>
                );
              })}

              {/* Sandbox sync panel (only for sandbox-enabled agents) */}
              {syncStatus && (
                <div style={{
                  margin: 'var(--google-space-6) var(--google-space-1) 0',
                  padding: 'var(--google-space-3)',
                  borderRadius: 'var(--google-radius-lg)',
                  border: '1px solid var(--google-border)',
                  background: 'var(--google-card)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <CloudServerOutlined style={{
                      color: syncStatus.container_alive ? '#34a853' : 'var(--google-muted-foreground)',
                    }} />
                    <Text style={{ fontSize: 12, fontWeight: 600 }}>{t('files.syncTitle')}</Text>
                    <Text style={{ fontSize: 11, color: 'var(--google-muted-foreground)', marginLeft: 'auto' }}>
                      {syncStatus.container_alive ? t('files.syncContainerRunning') : t('files.syncContainerStopped')}
                    </Text>
                  </div>
                  {syncStatus.summary && (
                    <div style={{ fontSize: 11, color: 'var(--google-muted-foreground)', margin: '4px 0 0' }}>
                      {t('files.syncSummary', {
                        synced: syncStatus.summary.synced,
                        push: syncStatus.summary.pending_push,
                        pull: syncStatus.summary.pending_pull,
                        conflict: syncStatus.summary.conflict,
                      })}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 6, marginTop: 6, alignItems: 'center' }}>
                    <Dropdown
                      menu={{
                        items: [
                          { key: 'push', label: t('files.syncPush'), icon: <ArrowUpOutlined /> },
                          { key: 'pull', label: t('files.syncPull'), icon: <ArrowDownOutlined /> },
                          { key: 'both', label: t('files.syncBoth'), icon: <SyncOutlined /> },
                        ],
                        onClick: ({ key }) => void runSync(key as 'push' | 'pull' | 'both'),
                      }}
                    >
                      <Button size="small" icon={<SyncOutlined spin={syncing} />} loading={syncing}>
                        {t('files.syncAction')}
                      </Button>
                    </Dropdown>
                    <Tooltip title={t('files.syncRefresh')}>
                      <Button size="small" type="text" icon={<ReloadOutlined style={{ fontSize: 13 }} />}
                        onClick={() => void loadSyncStatus()}
                        style={{ color: 'var(--google-muted-foreground)' }} />
                    </Tooltip>
                  </div>
                </div>
              )}

              {/* Workspace files */}
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                margin: 'var(--google-space-6) var(--google-space-1) var(--google-space-3)',
              }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--google-muted-foreground)', letterSpacing: 0.5 }}>
                  {t('files.workspaceFiles')}
                </span>
                <span style={{ display: 'flex', gap: 'var(--google-space-1)' }}>
                  <Upload {...uploadProps}>
                    <Tooltip title={t('files.uploadTo', { dir: uploadDir || t('files.rootDir') })}>
                      <Button type="text" size="small" icon={<UploadOutlined style={{ fontSize: 13 }} />}
                        style={{ color: 'var(--google-muted-foreground)' }} />
                    </Tooltip>
                  </Upload>
                  <Tooltip title={t('common.refresh')}>
                    <Button type="text" size="small" icon={<ReloadOutlined style={{ fontSize: 13 }} />}
                      onClick={() => void reloadTree()} style={{ color: 'var(--google-muted-foreground)' }} />
                  </Tooltip>
                </span>
              </div>
              {treeLoading ? (
                <div style={{ textAlign: 'center', padding: 'var(--google-space-8)' }}><Spin size="small" /></div>
              ) : treeData.length === 0 ? (
                <Text style={{ fontSize: 12, padding: '0 var(--google-space-1)', color: 'var(--google-muted-foreground)' }}>
                  {t('files.workspaceEmpty')}
                </Text>
              ) : (
                <Tree
                  className="files-tree"
                  showIcon
                  blockNode
                  selectable
                  treeData={treeData}
                  loadData={onLoadData}
                  onSelect={onTreeSelect}
                  expandedKeys={expandedKeys}
                  onExpand={(keys) => setExpandedKeys(keys)}
                  selectedKeys={activeTab && !activeTab.isKernel ? [activeKey] : []}
                  icon={(props: { expanded?: boolean; isLeaf?: boolean }) =>
                    props.isLeaf ? null : props.expanded
                      ? <FolderFilled style={{ color: 'var(--google-chart-3)' }} />
                      : <FolderOutlined style={{ color: 'var(--google-chart-3)' }} />}
                  style={{ background: 'transparent', fontSize: 13 }}
                />
              )}
            </div>
          </div>

          {/* ── Right: editor ── */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            {tabs.length === 0 ? (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    <span style={{ color: 'var(--google-muted-foreground)', fontSize: 13 }}>
                      {t('files.selectFileToEdit')}
                    </span>
                  }
                />
              </div>
            ) : (
              <Tabs
                className="files-tabs"
                type="editable-card"
                hideAdd
                activeKey={activeKey}
                onChange={setActiveKey}
                onEdit={onTabEdit}
                tabBarStyle={{ marginBottom: 0, padding: '0 var(--google-space-4)' }}
                items={tabs.map((tab) => ({
                  key: tab.path,
                  label: (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--google-space-2)' }}>
                      {tab.isKernel
                        ? <ControlOutlined style={{ color: 'var(--google-primary)', fontSize: 13 }} />
                        : <FileMarkdownOutlined style={{ color: 'var(--google-muted-foreground)', fontSize: 13 }} />}
                      {tab.name}
                      {contents[tab.path] !== originals[tab.path] && (
                        <span style={{
                          width: 6, height: 6, borderRadius: '50%', background: 'var(--google-primary)',
                          display: 'inline-block',
                        }} />
                      )}
                    </span>
                  ),
                  children: (
                    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
                      {/* Editor toolbar */}
                      <div style={{
                        display: 'flex', alignItems: 'center', gap: 'var(--google-space-4)',
                        padding: 'var(--google-space-3) var(--google-space-6)', borderBottom: '1px solid var(--google-border)',
                        background: 'var(--google-muted)',
                      }}>
                        <FileOutlined style={{ color: 'var(--google-muted-foreground)' }} />
                        <Text code style={{ fontSize: 12 }}>{tab.path}</Text>
                        {tab.isKernel && (
                          <Tag style={{
                            marginInlineEnd: 0, fontSize: 11,
                            color: 'var(--google-primary)',
                            background: 'rgba(66, 133, 244, 0.08)',
                            borderColor: 'rgba(66, 133, 244, 0.25)',
                          }}>
                            {t('files.kernelLabel')}
                          </Tag>
                        )}
                        {isMarkdown(tab.name) && (
                          <Segmented
                            size="small"
                            value={modeFor(tab)}
                            onChange={(v) =>
                              setViewModes((prev) => ({ ...prev, [tab.path]: v as 'edit' | 'preview' }))
                            }
                            options={[
                              { label: t('files.preview'), value: 'preview', icon: <EyeOutlined /> },
                              { label: t('files.edit'), value: 'edit', icon: <EditOutlined /> },
                            ]}
                          />
                        )}
                        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 'var(--google-space-3)' }}>
                          <Tooltip title={t('files.download')}>
                            <Button
                              size="small" type="text"
                              icon={<DownloadOutlined />}
                              disabled={tab.isKernel && contents[tab.path] === undefined}
                              onClick={() => {
                                window.open(`${apiClient.defaults.baseURL}/agents/${agentId}/files/download?path=${encodeURIComponent(tab.path)}`, '_blank');
                              }}
                            />
                          </Tooltip>
                          {!tab.isKernel && (
                            <Popconfirm
                              title={t('files.delete')}
                              description={t('files.deleteFileConfirm', { name: tab.name })}
                              okText={t('common.delete')}
                              cancelText={t('common.cancel')}
                              okButtonProps={{ danger: true }}
                              onConfirm={() => void deleteEntry(tab.path, false)}
                            >
                              <Tooltip title={t('files.delete')}>
                                <Button size="small" danger type="text" icon={<DeleteOutlined />} />
                              </Tooltip>
                            </Popconfirm>
                          )}
                          <Button
                            size="small"
                            type="primary"
                            icon={<SaveOutlined />}
                            loading={saving}
                            disabled={contents[tab.path] === originals[tab.path]}
                            onClick={() => void saveFile(tab.path)}
                          >
                            {t('common.save')}
                          </Button>
                        </div>
                      </div>

                      {/* Editor body */}
                      {fileLoading && activeKey === tab.path ? (
                        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Spin />
                        </div>
                      ) : modeFor(tab) === 'preview' ? (
                        <div style={{
                          flex: 1, minHeight: 0, overflow: 'auto',
                          padding: 'var(--google-space-6) var(--google-space-8)',
                          fontSize: 14, lineHeight: 1.75,
                        }}>
                          <MarkdownView text={contents[tab.path] ?? ''} />
                        </div>
                      ) : (
                        <Input.TextArea
                          value={contents[tab.path] ?? ''}
                          onChange={(e) =>
                            setContents((prev) => ({ ...prev, [tab.path]: e.target.value }))}
                          onKeyDown={handleEditorKeyDown}
                          placeholder={t('files.emptyFile')}
                          spellCheck={false}
                          style={{
                            flex: 1, minHeight: 0, resize: 'none', border: 'none', borderRadius: 0,
                            padding: 'var(--google-space-6)', fontSize: 13, lineHeight: 1.7,
                            fontFamily: 'var(--google-font-mono)',
                            boxShadow: 'none',
                          }}
                        />
                      )}

                      {/* Status bar */}
                      <div style={{
                        padding: 'var(--google-space-1) var(--google-space-6)', borderTop: '1px solid var(--google-border)',
                        display: 'flex', gap: 'var(--google-space-6)', fontSize: 11, color: 'var(--google-muted-foreground)', background: 'var(--google-muted)',
                      }}>
                        <span>{t('files.chars', { count: (contents[tab.path] ?? '').length })}</span>
                        <span>{t('files.lines', { count: (contents[tab.path] ?? '').split('\n').length })}</span>
                        {contents[tab.path] !== originals[tab.path] && (
                          <span style={{ color: 'var(--google-chart-3)' }}>{t('files.unsaved')}</span>
                        )}
                        {activeIsKernel && tab.path === activeKey && dirty && (
                          <span>{t('files.rebuildHint')}</span>
                        )}
                      </div>
                    </div>
                  ),
                }))}
              />
            )}
          </div>
        </div>
      </GoogleCard>
      <style>{`
        .files-tabs { display: flex; flex-direction: column; height: 100%; }
        /* antd v6 Tabs DOM: .ant-tabs-body-holder > .ant-tabs-body > .ant-tabs-content(pane) */
        /* Only the visible pane gets the flex chain — inactive panes keep the
           library's .ant-tabs-content-hidden { display: none } rule, otherwise
           every open tab's content would stack vertically. */
        .files-tabs .ant-tabs-body-holder { flex: 1; display: flex; flex-direction: column; min-height: 0; }
        .files-tabs .ant-tabs-body { flex: 1; display: flex; flex-direction: column; min-height: 0; }
        .files-tabs .ant-tabs-content:not(.ant-tabs-content-hidden) { flex: 1; display: flex; flex-direction: column; min-height: 0; }
        .files-tabs .ant-tabs-content > div { flex: 1; min-height: 0; }
        /* File tree: keep every entry on a single line — long names get an
           ellipsis instead of wrapping the icon/name/size into a mess. */
        .files-tree .ant-tree-node-content-wrapper { display: flex; align-items: center; min-width: 0; overflow: hidden; }
        .files-tree .ant-tree-iconEle { flex-shrink: 0; }
        .files-tree .ant-tree-title { flex: 1; min-width: 0; display: block; overflow: hidden; }
        .files-tree-dir { display: flex; align-items: center; gap: 6px; min-width: 0; max-width: 100%; }
        .files-tree-file { display: flex; align-items: center; gap: 6px; min-width: 0; max-width: 100%; }
        .files-tree-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .files-tree-size { flex-shrink: 0; font-size: 11px !important; color: var(--google-muted-foreground); }
        /* Hover-revealed delete action on tree nodes */
        .files-tree-del {
          display: inline-flex; align-items: center; justify-content: center;
          margin-left: auto; padding: 0 4px; flex-shrink: 0;
          color: var(--google-muted-foreground); visibility: hidden;
          transition: color var(--google-transition-fast);
        }
        .files-tree .ant-tree-node-content-wrapper:hover .files-tree-del { visibility: visible; }
        .files-tree-del:hover { color: var(--google-destructive); }
        /* Sandbox sync status dot shown before mirrored file names */
        .files-sync-dot {
          width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; display: inline-block;
        }
      `}</style>
    </div>
  );
}

/* ───────── Tree helpers ───────── */
function updateTreeChildren(list: DataNode[], key: string, children: DataNode[]): DataNode[] {
  return list.map((node) => {
    if (node.key === key) return { ...node, children };
    if (node.children) {
      return { ...node, children: updateTreeChildren(node.children, key, children) };
    }
    return node;
  });
}
