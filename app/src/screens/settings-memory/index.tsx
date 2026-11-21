import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { Select } from '../../components/Select/Select';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { Switch } from '../../components/Switch/Switch';
import { TextField } from '../../components/TextField/TextField';
import { fmtDay } from '../../features/billing/format';
import { SettingsLayout, SettingsRow } from '../../features/settings/SettingsLayout';
import { deleteMemory, getMemories, patchMemory, type Memory, type MemoryCategory } from '../../features/settings/api';

import { useProfile } from '../../features/settings/useProfile';

const CATEGORIES: Array<{ key: MemoryCategory; label: string }> = [
  { key: 'about_me', label: '关于我' },
  { key: 'recent_projects', label: '近期项目' },
  { key: 'lessons', label: '经验教训' },
];

function sourceLabel(m: Memory): string {
  if (m.source_kind === 'onboarding') return '首次引导';
  if (m.source_task) return `任务：${m.source_task.title}`;
  if (m.source_kind === 'conversation') return '对话';
  return '手动添加';
}

/** 70 设置 · 记忆 (670:2). */
export default function Screen({ navigate }: ScreenProps) {
  const { query: profileQ, profile, patch, save } = useProfile();
  const first = useApiQuery((s) => getMemories(0, s), []);
  const [items, setItems] = useState<Memory[]>([]);
  const [counts, setCounts] = useState<Record<MemoryCategory, number> | null>(null);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; draft: string } | null>(null);
  const [forgetting, setForgetting] = useState<Memory | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!first.data) return;
    setItems(first.data.items);
    setCounts(first.data.category_counts);
    setNextOffset(first.data.next_offset);
  }, [first.data]);

  const loadMore = async () => {
    if (nextOffset === null) return;
    setLoadingMore(true);
    setListError(null);
    try {
      const page = await getMemories(nextOffset);
      setItems((x) => [...x, ...page.items]);
      setCounts(page.category_counts);
      setNextOffset(page.next_offset);
    } catch (err) {
      setListError(errorMessage(err, '加载失败，请重试'));
    } finally {
      setLoadingMore(false);
    }
  };

  const saveEdit = async () => {
    if (!editing) return;
    const content = editing.draft.trim();
    if (!content) return;
    setBusy(true);
    const saved = await save.run(() => patchMemory(editing.id, content));
    setBusy(false);
    if (saved) {
      setItems((x) => x.map((m) => (m.id === saved.id ? saved : m)));
      setEditing(null);
    }
  };

  const forget = async () => {
    if (!forgetting) return;
    setBusy(true);
    const ok = await save.run(() => deleteMemory(forgetting.id).then(() => true));
    setBusy(false);
    if (ok) {
      setItems((x) => x.filter((m) => m.id !== forgetting.id));
      setCounts((c) => (c ? { ...c, [forgetting.category]: Math.max(0, (c[forgetting.category] ?? 1) - 1) } : c));
    }
    setForgetting(null);
  };

  return (
    <SettingsLayout active="memory" title="记忆" save={{ status: save.status, message: save.message }}>
      <QueryGate q={profileQ} hasData={!!profile} rows={2}>
        {profile && (
          <>
            <SettingsRow label="自动记忆" description="任务结束后提炼值得记住的稳定信息；一次性的细节不会记">
              <Switch checked={profile.memory_auto_enabled} label="自动记忆" onChange={(v) => void patch({ memory_auto_enabled: v })} />
            </SettingsRow>
            <SettingsRow label="记忆提炼使用的模型" description="建议用响应快、成本低的模型">
              <Select
                size={32}
                width={120}
                label="记忆提炼使用的模型"
                value={profile.memory_model}
                options={[
                  { value: 'standard', label: 'Sage 标准' },
                  { value: 'deep', label: 'Sage 深度' },
                ]}
                onChange={(v) => void patch({ memory_model: v as 'standard' | 'deep' })}
              />
            </SettingsRow>
          </>
        )}
      </QueryGate>
      <QueryGate q={first} rows={4}>
        {first.data && items.length === 0 && (
          <div className="sg-settings__state">
            <EmptyState icon="book" title="还没有记忆" description="在对话中说“记住……”，或完成任务后由 Sage 自动提炼" />
          </div>
        )}
        {CATEGORIES.map((cat) => {
          const rows = items.filter((m) => m.category === cat.key);
          const total = counts?.[cat.key] ?? rows.length;
          if (total === 0 && rows.length === 0) return null;
          return (
            <section className="sg-memgroup" key={cat.key}>
              <h2>
                {cat.label} · {total}
              </h2>
              {rows.map((m) => (
                <div className="sg-memrow" key={m.id}>
                  {editing?.id === m.id ? (
                    <div className="sg-memrow__edit">
                      <TextField
                        multiline
                        rows={2}
                        width={756}
                        value={editing.draft}
                        autoFocus
                        aria-label="修改记忆"
                        maxLength={500}
                        onValueChange={(v) => setEditing({ id: m.id, draft: v })}
                        onKeyDown={(e) => e.key === 'Escape' && setEditing(null)}
                      />
                      <div className="sg-memrow__editactions">
                        <Button variant="ghost" size={28} label="取消" onClick={() => setEditing(null)} />
                        <Button variant="primary" size={28} label="保存" busy={busy} disabled={!editing.draft.trim()} onClick={() => void saveEdit()} />
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="sg-memrow__text">
                        <b>{m.content}</b>
                        <span>
                          {m.source_task ? (
                            <button type="button" className="sg-memrow__src" onClick={() => navigate(`/tasks/${m.source_task!.id}`)}>
                              {sourceLabel(m)}
                            </button>
                          ) : (
                            sourceLabel(m)
                          )}{' '}
                          · {fmtDay(m.edited_at ?? m.created_at)}
                          {m.edited_at ? ' · 已修改' : ''}
                        </span>
                      </div>
                      <div className="sg-memrow__actions">
                        <button type="button" onClick={() => setEditing({ id: m.id, draft: m.content })}>
                          修改
                        </button>
                        <button type="button" className="is-danger" onClick={() => setForgetting(m)}>
                          忘掉
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </section>
          );
        })}
        {listError && <ErrorBanner message={listError} onRetry={() => void loadMore()} />}
        {nextOffset !== null && (
          <div className="sg-bill__more">
            <Button variant="ghost" size={32} label="加载更多" busy={loadingMore} onClick={() => void loadMore()} />
          </div>
        )}
      </QueryGate>
      <div className="sg-settings__note">
        工作区专属的约定写在工作区规则里，不进入个人记忆。企业账号下，个人记忆只有本人可见。在对话中说“记住……”或“忘掉……”会立即生效。
      </div>
      {forgetting && (
        <ConfirmDialog
          title="忘掉这条记忆？"
          message={`“${forgetting.content.length > 40 ? forgetting.content.slice(0, 40) + '…' : forgetting.content}”将被删除，之后的任务不会再用到它。`}
          confirmLabel="忘掉"
          cancelLabel="取消"
          tone="warning"
          busy={busy}
          onConfirm={() => void forget()}
          onCancel={() => setForgetting(null)}
        />
      )}
    </SettingsLayout>
  );
}
