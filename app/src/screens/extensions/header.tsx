import { useRef, useState } from 'react';
import { useShell } from '../../app/AppShell';
import type { ScreenProps } from '../../app/router';
import { Button } from '../../components/Button/Button';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { SearchField } from '../../components/SearchField/SearchField';
import { errorMessage } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { extensionsApi, type ExtensionList } from '../../features/extensions/api';

export const KIND_TABS = [
  { key: '', label: '全部' },
  { key: 'plugin', label: '插件' },
  { key: 'skill', label: '技能' },
  { key: 'connector', label: '连接器' },
  { key: 'agent', label: '智能体' },
] as const;

export function KindTabs({ kind, onChange }: { kind: string; onChange: (k: string) => void }) {
  return (
    <div className="sg-tabs sg-xkinds" role="tablist" aria-label="扩展类型">
      {KIND_TABS.map((t) => (
        <Tab key={t.key} size={37} label={t.label} selected={kind === t.key} onSelect={() => onChange(t.key)} />
      ))}
    </div>
  );
}

export function ExtensionsHeader({ navigate, tab, installedCount, q, onSearch, description, showImport }: { navigate: ScreenProps['navigate']; tab: 'installed' | 'market'; installedCount?: number | undefined; q: string; onSearch: (v: string) => void; description: string; showImport?: boolean }) {
  const { toast } = useShell();
  const fileRef = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);

  const importFile = async (file: File) => {
    setImporting(true);
    try {
      const text = await file.text();
      const manifest: unknown = JSON.parse(text);
      const res = await extensionsApi.importManifest(manifest);
      toast({ tone: 'undo', message: `已导入技能“${res.extension.name}”` });
      navigate(`/extensions/skills/${encodeURIComponent(res.extension.id)}`);
    } catch (err) {
      toast({ tone: 'failure', message: err instanceof SyntaxError ? '文件不是有效的 JSON 技能清单' : errorMessage(err, '导入失败，请重试') });
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <>
      <PageHeader
        title="扩展"
        description={description}
        actions={
          <>
            <SearchField size={32} width={250} placeholder={tab === 'market' ? '搜索市场' : '搜索扩展'} value={q} clearable onValueChange={onSearch} />
            {showImport && (
              <>
                <Button variant="secondary" size={32} icon="paperclip" label="导入本地技能" busy={importing} onClick={() => fileRef.current?.click()} />
                <input
                  ref={fileRef}
                  type="file"
                  accept="application/json,.json"
                  className="sr-only"
                  aria-hidden="true"
                  tabIndex={-1}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void importFile(f);
                  }}
                />
              </>
            )}
          </>
        }
      />
      <div className="sg-tabs" role="tablist" aria-label="扩展">
        <Tab size={37} label="已安装" {...(installedCount !== undefined ? { count: installedCount } : {})} selected={tab === 'installed'} onSelect={() => navigate('/extensions')} />
        <Tab size={37} label="市场" selected={tab === 'market'} onSelect={() => navigate('/extensions/market')} />
      </div>
    </>
  );
}

export function installedCountOf(list: ExtensionList | undefined): number | undefined {
  return list?.counts.installed;
}
