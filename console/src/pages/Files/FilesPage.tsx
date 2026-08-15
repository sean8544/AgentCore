import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Button,
  Empty,
  Input,
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
  ControlOutlined,
  FileMarkdownOutlined,
  FileOutlined,
  FolderFilled,
  FolderOutlined,
  DownloadOutlined,
  ReloadOutlined,
  RobotOutlined,
  SaveOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import type { DataNode, EventDataNode } from 'antd/es/tree';
import type { UploadProps } from 'antd';
import { apiClient } from '../../api/client';
import { useAgentId } from '../../stores/agentStore';
import { useI18n } from '../../i18n';

const { Text } = Typography;

/* ───────── Constants ───────── */
const ORANGE = '#FF7F16';
const KERNEL_FILES = ['agent.md', 'profile.md', 'soul.md', 'bootstrap.md'] as const;
const KERNEL_LABEL_KEYS: Record<string, string> = {
  'agent.md': 'files.agentIdentity',
  'profile.md': 'files.profileConfig',
  'soul.md': 'files.corePersona',
  'bootstrap.md': 'files.bootstrap',
};

/* ───────── Types ───────── */
interface FileItem {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number | null;
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

function toTreeNodes(items: FileItem[]): DataNode[] {
  return items.map((item) => ({
    key: item.path,
    title: item.is_dir ? item.name : (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span>{item.name}</span>
        {item.size != null && (
          <Text type="secondary" style={{ fontSize: 11 }}>{formatSize(item.size)}</Text>
        )}
      </span>
    ),
    isLeaf: !item.is_dir,
    icon: item.is_dir ? undefined : <FileOutlined style={{ color: '#999' }} />,
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

  const contentsRef = useRef(contents);
  contentsRef.current = contents;

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
      setTreeData(toTreeNodes(items));
      setRootNames(items.map((i) => i.name));
    } catch {
      setTreeData([]);
    } finally {
      setTreeLoading(false);
    }
  }, [loadDir]);

  /* Reset everything when switching agents. */
  useEffect(() => {
    setTabs([]);
    setActiveKey('');
    setContents({});
    setOriginals({});
    setExpandedKeys([]);
    setRootNames([]);
    void reloadTree();
  }, [agentId, reloadTree]);

  /* ── Lazy-load subdirectories ── */
  const onLoadData = useCallback(
    async (node: EventDataNode<DataNode>) => {
      if (node.children?.length) return;
      try {
        const items = await loadDir(String(node.key));
        setTreeData((origin) => updateTreeChildren(origin, String(node.key), toTreeNodes(items)));
      } catch {
        antdMessage.error(t('files.loadFailed'));
      }
    },
    [loadDir],
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
  /* Kernel files that actually exist on disk; before the first listing loads,
     show them all (tolerate missing listing). */
  const visibleKernelFiles = rootNames.length
    ? KERNEL_FILES.filter((name) => rootNames.includes(name))
    : KERNEL_FILES;

  return (
    <div style={{
      display: 'flex', height: '100%', margin: -24, background: '#fff',
      borderRadius: 8, overflow: 'hidden',
    }}>
      {/* ── Left: file navigator ── */}
      <div style={{
        width: 280, flexShrink: 0, borderRight: '1px solid #f0f0f0',
        display: 'flex', flexDirection: 'column', background: '#fafafa',
      }}>
        {/* Header */}
        <div style={{ padding: '14px 16px 10px', borderBottom: '1px solid #f0f0f0' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
            <RobotOutlined style={{ color: ORANGE }} />
            <Text strong style={{ fontSize: 13 }}>{t('files.title')}</Text>
            <Text code style={{ fontSize: 11, marginLeft: 'auto' }}>{agentId}</Text>
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 12px 24px' }}>
          {/* Kernel files */}
          <div style={{
            fontSize: 12, fontWeight: 600, color: '#8c8c8c', letterSpacing: 0.5,
            margin: '4px 4px 8px',
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
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 10px', borderRadius: 8, cursor: 'pointer',
                  marginBottom: 2, fontSize: 13,
                  background: active ? '#fff3e8' : 'transparent',
                  color: active ? ORANGE : '#333',
                  border: active ? `1px solid #ffd8b3` : '1px solid transparent',
                }}
              >
                <ControlOutlined style={{ color: active ? ORANGE : '#faad14' }} />
                <span style={{ fontWeight: active ? 600 : 400 }}>{name}</span>
                <Text type="secondary" style={{ fontSize: 11, marginLeft: 'auto' }}>
                  {t(KERNEL_LABEL_KEYS[name] ?? '')}
                </Text>
              </div>
            );
          })}

          {/* Workspace files */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            margin: '18px 4px 8px',
          }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: '#8c8c8c', letterSpacing: 0.5 }}>
              {t('files.workspaceFiles')}
            </span>
            <span style={{ display: 'flex', gap: 2 }}>
              <Upload {...uploadProps}>
                <Tooltip title={t('files.uploadTo', { dir: uploadDir || t('files.rootDir') })}>
                  <Button type="text" size="small" icon={<UploadOutlined style={{ fontSize: 13 }} />}
                    style={{ color: '#8c8c8c' }} />
                </Tooltip>
              </Upload>
              <Tooltip title={t('common.refresh')}>
                <Button type="text" size="small" icon={<ReloadOutlined style={{ fontSize: 13 }} />}
                  onClick={() => void reloadTree()} style={{ color: '#8c8c8c' }} />
              </Tooltip>
            </span>
          </div>
          {treeLoading ? (
            <div style={{ textAlign: 'center', padding: 24 }}><Spin size="small" /></div>
          ) : treeData.length === 0 ? (
            <Text type="secondary" style={{ fontSize: 12, padding: '0 4px' }}>
              {t('files.workspaceEmpty')}
            </Text>
          ) : (
            <Tree
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
                  ? <FolderFilled style={{ color: ORANGE }} />
                  : <FolderOutlined style={{ color: ORANGE }} />}
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
                <span style={{ color: '#999', fontSize: 13 }}>
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
            tabBarStyle={{ marginBottom: 0, padding: '0 12px' }}
            items={tabs.map((tab) => ({
              key: tab.path,
              label: (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  {tab.isKernel
                    ? <ControlOutlined style={{ color: ORANGE, fontSize: 13 }} />
                    : <FileMarkdownOutlined style={{ color: '#999', fontSize: 13 }} />}
                  {tab.name}
                  {contents[tab.path] !== originals[tab.path] && (
                    <span style={{
                      width: 6, height: 6, borderRadius: '50%', background: ORANGE,
                      display: 'inline-block',
                    }} />
                  )}
                </span>
              ),
              children: (
                <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
                  {/* Editor toolbar */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '8px 16px', borderBottom: '1px solid #f5f5f5',
                    background: '#fafafa',
                  }}>
                    <FileOutlined style={{ color: '#999' }} />
                    <Text code style={{ fontSize: 12 }}>{tab.path}</Text>
                    {tab.isKernel && (
                      <Tag color="orange" style={{ marginInlineEnd: 0, fontSize: 11 }}>
                        {t('files.kernelLabel')}
                      </Tag>
                    )}
                    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Tooltip title={t('files.download')}>
                        <Button
                          size="small" type="text"
                          icon={<DownloadOutlined />}
                          disabled={tab.isKernel && contents[tab.path] === undefined}
                          onClick={() => {
                            window.open(`/api/agents/${agentId}/files/download?path=${encodeURIComponent(tab.path)}`, '_blank');
                          }}
                        />
                      </Tooltip>
                      <Button
                        size="small"
                        type="primary"
                        icon={<SaveOutlined />}
                        loading={saving}
                        disabled={contents[tab.path] === originals[tab.path]}
                        onClick={() => void saveFile(tab.path)}
                        style={{
                          background: contents[tab.path] !== originals[tab.path] ? ORANGE : undefined,
                          borderColor: contents[tab.path] !== originals[tab.path] ? ORANGE : undefined,
                        }}
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
                        padding: 16, fontSize: 13, lineHeight: 1.7,
                        fontFamily: "'JetBrains Mono', Menlo, Consolas, monospace",
                        boxShadow: 'none',
                      }}
                    />
                  )}

                  {/* Status bar */}
                  <div style={{
                    padding: '4px 16px', borderTop: '1px solid #f0f0f0',
                    display: 'flex', gap: 16, fontSize: 11, color: '#999', background: '#fafafa',
                  }}>
                    <span>{t('files.chars', { count: (contents[tab.path] ?? '').length })}</span>
                    <span>{t('files.lines', { count: (contents[tab.path] ?? '').split('\n').length })}</span>
                    {contents[tab.path] !== originals[tab.path] && (
                      <span style={{ color: ORANGE }}>{t('files.unsaved')}</span>
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
