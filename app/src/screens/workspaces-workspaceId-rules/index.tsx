import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Menu, MenuItem } from '../../components/Menu/MenuItem';
import { EmptyState, ErrorBanner, ForbiddenState, LoadingState, Toast, errorMessage } from '../../components/States/States';
import { StepMarker } from '../../components/StepMarker/StepMarker';
import { Tag } from '../../components/Tag/Tag';
import { TextField } from '../../components/TextField/TextField';
import { apiMessage } from '../../features/task/model';
import { dayTime, wsApi } from '../../features/workspaces/model';
import { WorkspaceTabs } from '../../features/workspaces/parts';
import '../../features/workspaces/workspaces.css';

const MAX_RULES = 30;
const MAX_LEN = 200;

function conflictMessage(err: unknown): string {
  return apiMessage(err, '保存失败，请重试').replace(/^状态已变化/, '规则已被其他人修改');
}

export default function Screen({ params, navigate }: ScreenProps) {
  const id = params.workspaceId ?? '';
  const enc = encodeURIComponent(id);
  const ws = useApiQuery((s) => wsApi.get(id, s), [id]);
  const rules = useApiQuery((s) => wsApi.rules(id, s), [id]);

  const [adding, setAdding] = useState('');
  const [menuFor, setMenuFor] = useState<number | null>(null);
  const [editing, setEditing] = useState<{ index: number; text: string } | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const current = rules.data?.current ?? null;
  const list = current?.rules ?? [];
  const canEdit = !!rules.data?.can_edit;

  const save = useSubmit(async (next: string[], note: string) => {
    await wsApi.saveRules(id, { rules: next, version: current?.number ?? 0, change_note: note });
    setEditing(null);
    setAdding('');
    rules.reload();
    ws.reload();
  });
  const restore = useSubmit(async (v: number) => {
    await wsApi.restoreRules(id, v, current?.number ?? 0);
    rules.reload();
    ws.reload();
  });

  useEffect(() => {
    const err = save.error ?? restore.error;
    if (!err) return;
    setToast(conflictMessage(err));
    if (err.code === 'VERSION_CONFLICT' || err.code === 'STATE_CONFLICT') rules.reload();
  }, [save.error, restore.error]);

  if (rules.status === 'error' && (rules.error.status === 403 || rules.error.status === 404)) {
    return (
      <div className="sg-ws">
        <div className="sg-ws__main">
          <ForbiddenState title="无法查看这个工作区的规则" action={{ label: '返回全部任务', onClick: () => navigate('/tasks') }} />
        </div>
      </div>
    );
  }

  const trimmedAdd = adding.trim();
  const full = list.length >= MAX_RULES;
  const submitAdd = () => {
    if (!trimmedAdd || trimmedAdd.length > MAX_LEN || full || save.submitting) return;
    void save.submit([...list, trimmedAdd], `新增：${trimmedAdd.slice(0, 30)}`);
  };
  const w = ws.data;
  const members = w?.counts.members;

  return (
    <div className="sg-ws">
      <div className="sg-ws__main">
        {ws.status === 'error' && <ErrorBanner message={errorMessage(ws.error, '工作区暂时无法读取')} onRetry={ws.reload} />}
        <div className="sg-ws__head">
          <h1>{w?.name ?? ''}</h1>
          <div className="sg-ws__headend">
            <Button variant="secondary" size={30} icon="plus" label="邀请" onClick={() => navigate(`/workspaces/${enc}/members?invite=1`)} />
          </div>
        </div>
        <div className="sg-ws__sub">
          {w ? `${w.account_kind === 'personal' ? '个人' : '企业'}工作区${members !== undefined ? ` · ${members} 位成员` : ''}` : ''}
        </div>
        <div className="sg-ws__tabs">
          <WorkspaceTabs ws={w} current="rules" rulesLabel={`规则 ${list.length}`} navigate={navigate} />
        </div>
        <div className="sg-rules__intro">专家在这个工作区的每个任务都会遵守这些规则。与本次指令冲突时，专家会先指出冲突，再按本次指令执行。</div>

        {rules.status === 'error' && <ErrorBanner message={errorMessage(rules.error, '规则暂时无法读取')} onRetry={rules.reload} />}
        {rules.status === 'loading' && !rules.data && <LoadingState rows={4} />}
        {rules.data && list.length === 0 && !canEdit && <EmptyState icon="book" title="还没有规则" description="工作区管理员可以在这里添加规则" />}

        {list.map((rule, i) => (
          <div key={`${current?.number}-${i}`} className="sg-rules__item">
            <StepMarker index={i + 1} state="static" shape="square" size={22} />
            {editing?.index === i ? (
              <div className="sg-rules__edit">
                <TextField
                  multiline
                  rows={2}
                  value={editing.text}
                  maxLength={MAX_LEN}
                  autoFocus
                  onValueChange={(text) => setEditing({ index: i, text })}
                />
                <div className="sg-rules__editactions">
                  <Button variant="ghost" size={28} label="取消" onClick={() => setEditing(null)} />
                  <Button
                    variant="primary"
                    size={28}
                    label="保存"
                    busy={save.submitting}
                    disabled={!editing.text.trim() || editing.text.trim() === rule}
                    onClick={() => {
                      const next = [...list];
                      next[i] = editing.text.trim();
                      void save.submit(next, `修改第 ${i + 1} 条`);
                    }}
                  />
                </div>
              </div>
            ) : (
              <div className="sg-rules__text">{rule}</div>
            )}
            {canEdit && editing?.index !== i ? (
              <IconButton icon="ellipsis" size={25} variant="ghost" label={`第 ${i + 1} 条规则的操作`} onClick={() => setMenuFor(menuFor === i ? null : i)} />
            ) : (
              <span />
            )}
            {menuFor === i && (
              <div className="sg-rules__menu" onMouseLeave={() => setMenuFor(null)}>
                <Menu label="规则操作">
                  <MenuItem
                    icon="square-pen"
                    label="编辑"
                    onSelect={() => {
                      setMenuFor(null);
                      setEditing({ index: i, text: rule });
                    }}
                  />
                  <MenuItem
                    icon="x"
                    label="删除"
                    danger
                    disabled={save.submitting}
                    onSelect={() => {
                      setMenuFor(null);
                      void save.submit(
                        list.filter((_, j) => j !== i),
                        `删除：${rule.slice(0, 30)}`,
                      );
                    }}
                  />
                </Menu>
              </div>
            )}
          </div>
        ))}

        {canEdit && (
          <>
            <label className="sg-rules__add">
              <Icon name="plus" size={14} />
              <input
                value={adding}
                maxLength={MAX_LEN}
                disabled={full || save.submitting}
                placeholder={full ? `已达 ${MAX_RULES} 条上限` : `添加一条规则（${MAX_LEN} 字以内，最多 ${MAX_RULES} 条）`}
                aria-label="添加一条规则"
                onChange={(e) => setAdding(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    submitAdd();
                  }
                }}
              />
            </label>
            {trimmedAdd && (
              <div className="sg-rules__editactions sg-rules__hint">
                <span>
                  {trimmedAdd.length}/{MAX_LEN} · 回车保存，会生成 v{(current?.number ?? 0) + 1}
                </span>
                <Button variant="primary" size={28} label="添加" busy={save.submitting} onClick={submitAdd} />
              </div>
            )}
          </>
        )}
      </div>

      <aside className="sg-ws__side sg-rules__side" aria-label="版本记录">
        <div className="sg-rules__sidehead">
          <h2>版本记录</h2>
          <p>每次修改都会生成一个新版本，可以回滚</p>
        </div>
        <div>
          {rules.data?.versions.items.length === 0 && <InlineNotice icon="info" tone="muted" text="还没有版本" />}
          {rules.data?.versions.items.map((v) => {
            const isCurrent = v.number === current?.number;
            return (
              <div key={v.number} className="sg-rules__version">
                <Tag label={`v${v.number}`} tone="neutral" size={20} />
                <div>
                  <div className="sg-rules__vmeta">
                    {v.author?.name ?? '系统'} · {dayTime(v.created_at)}
                  </div>
                  <div className="sg-rules__vnote">{v.restored_from ? `回滚到 v${v.restored_from}` : v.change_note || '—'}</div>
                  {canEdit && !isCurrent && (
                    <div className="sg-rules__vaction">
                      <Button
                        variant="ghost"
                        size={24}
                        icon="refresh"
                        label={`回滚到 v${v.number}`}
                        busy={restore.submitting}
                        onClick={() => void restore.submit(v.number)}
                      />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        {rules.data && !canEdit && <InlineNotice icon="info" tone="muted" text="只有工作区管理员可以编辑规则，成员只读。" />}
      </aside>
      {toast && <Toast tone="failure" message={toast} onClose={() => setToast(null)} />}
    </div>
  );
}
