import { useState, type ReactNode } from 'react';
import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { Menu, MenuItem } from '../../components/Menu/MenuItem';
import { SegmentedControl } from '../../components/SegmentedControl/SegmentedControl';
import { TextField } from '../../components/TextField/TextField';
import { apiMessage } from '../task/model';
import { artifactApi, downloadUrl, shareText, type Artifact, type Audience, type Block } from './model';
import './artifacts.css';
import { Modal } from '../../components/Modal/Modal';

export interface Notice {
  tone: 'undo' | 'failure';
  text: string;
  undo?: () => void;
}

function Dialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <Modal className="sg-artdialog" label={title} onClose={onClose}>
      <div className="sg-artdialog__title">{title}</div>
      {children}
    </Modal>
  );
}

export function RenameDialog({ artifact, onClose, onDone }: { artifact: Artifact; onClose: () => void; onDone: () => void }) {
  const [title, setTitle] = useState(artifact.title);
  const save = useSubmit(async () => {
    await artifactApi.update(artifact.id, { title: title.trim() });
    onDone();
  });
  const t = title.trim();
  return (
    <Dialog title="重命名" onClose={onClose}>
      <form
        className="sg-artdialog__body"
        onSubmit={(e) => {
          e.preventDefault();
          if (t && t !== artifact.title) void save.submit();
        }}
      >
        <TextField size={32} value={title} maxLength={120} autoFocus aria-label="成果名称" onValueChange={setTitle} />
        {save.error && <div className="sg-artdialog__error">{apiMessage(save.error, '重命名失败，请重试')}</div>}
        <div className="sg-artdialog__foot">
          <Button variant="ghost" size={30} label="取消" onClick={onClose} />
          <Button type="submit" variant="primary" size={30} label="保存" busy={save.submitting} disabled={!t || t === artifact.title} />
        </div>
      </form>
    </Dialog>
  );
}

const AUDIENCES: Array<{ value: Audience; label: string }> = [
  { value: 'only_me', label: '仅本人' },
  { value: 'workspace_members', label: '工作区成员' },
  { value: 'public_link', label: '公开链接' },
  { value: 'encrypted_link', label: '加密链接' },
];

export function ShareDialog({ artifact, onClose, onDone }: { artifact: Artifact; onClose: () => void; onDone: () => void }) {
  const [audience, setAudience] = useState<Audience>(artifact.share?.audience ?? 'only_me');
  const [secret, setSecret] = useState('');
  const save = useSubmit(async () => {
    if (audience === 'only_me') await artifactApi.unshare(artifact.id);
    else
      await artifactApi.share(artifact.id, {
        audience,
        ...(audience === 'public_link' || audience === 'encrypted_link' ? { expires_in_days: 7 } : {}),
        ...(audience === 'encrypted_link' ? { secret } : {}),
      });
    onDone();
  });
  const noWorkspace = audience === 'workspace_members' && !artifact.workspace;
  const badSecret = audience === 'encrypted_link' && (secret.length < 6 || secret.length > 64);
  const unchanged = audience === (artifact.share?.audience ?? 'only_me') && audience !== 'encrypted_link' && audience !== 'public_link';
  const link = artifact.share && artifact.share.audience !== 'only_me' ? artifact.share.url : null;
  return (
    <Dialog title={`分享“${artifact.title}”`} onClose={onClose}>
      <div className="sg-artdialog__body">
        <SegmentedControl label="分享对象" options={AUDIENCES} value={audience} onChange={setAudience} />
        <div className="sg-artdialog__hint">
          {audience === 'only_me' && '只有你可以打开；已有链接会失效'}
          {audience === 'workspace_members' && (noWorkspace ? '这个成果还没有归入工作区，不能按工作区成员分享' : `“${artifact.workspace?.name}”的成员可以查看`)}
          {audience === 'public_link' && '拿到链接的人都可以查看，7 天后过期'}
          {audience === 'encrypted_link' && '拿到链接且输入密码的人可以查看，7 天后过期'}
        </div>
        {audience === 'encrypted_link' && (
          <TextField size={32} type="password" value={secret} placeholder="设置 6–64 位访问密码" invalid={!!secret && badSecret} aria-label="访问密码" onValueChange={setSecret} />
        )}
        {link && <div className="sg-artdialog__hint truncate">当前链接：{new URL(link, window.location.origin).href}</div>}
        {save.error && <div className="sg-artdialog__error">{apiMessage(save.error, '分享设置失败，请重试')}</div>}
        <div className="sg-artdialog__foot">
          <Button variant="ghost" size={30} label="取消" onClick={onClose} />
          <Button variant="primary" size={30} label="保存" busy={save.submitting} disabled={noWorkspace || badSecret || unchanged} onClick={() => void save.submit()} />
        </div>
      </div>
    </Dialog>
  );
}

export interface MoveTarget {
  id: string | null;
  name: string;
}

/** Row / header “更多” menu (54 菜单). Writes go straight to the API; the caller reloads via onChanged. */
export function ArtifactMenu({
  artifact,
  targets,
  navigate,
  onChanged,
  onNotice,
  onClose,
  showOpen = true,
}: {
  artifact: Artifact;
  targets: MoveTarget[];
  navigate: (to: string) => void;
  onChanged: () => void;
  onNotice: (n: Notice) => void;
  onClose: () => void;
  showOpen?: boolean;
}) {
  const [moveOpen, setMoveOpen] = useState(false);
  const [dialog, setDialog] = useState<'rename' | 'share' | null>(null);
  const id = artifact.id;
  const fail = (err: unknown, fallback: string) => onNotice({ tone: 'failure', text: apiMessage(err, fallback) });

  const trash = useSubmit(async () => {
    try {
      await artifactApi.trash(id);
    } catch (e) {
      fail(e, '移到回收站失败，请重试');
      return;
    }
    onChanged();
    onClose();
    onNotice({
      tone: 'undo',
      text: `已将“${artifact.title}”移到回收站，30 天内可恢复`,
      undo: () =>
        void artifactApi.untrash(id).then(onChanged, (e: unknown) => fail(e, '恢复失败，请到回收站中恢复')),
    });
  });
  const untrash = useSubmit(async () => {
    try {
      await artifactApi.untrash(id);
      onChanged();
      onClose();
    } catch (e) {
      fail(e, '恢复失败，请重试');
    }
  });
  const move = useSubmit(async (target: MoveTarget) => {
    const prev = artifact.workspace?.id ?? null;
    try {
      await artifactApi.update(id, { workspace_id: target.id });
    } catch (e) {
      fail(e, '移动失败，请重试');
      return;
    }
    onChanged();
    onClose();
    onNotice({
      tone: 'undo',
      text: target.id ? `已移动到“${target.name}”` : '已移出工作区',
      undo: () => void artifactApi.update(id, { workspace_id: prev }).then(onChanged, (e: unknown) => fail(e, '撤销失败，请重试')),
    });
  });

  if (dialog === 'rename')
    return (
      <RenameDialog
        artifact={artifact}
        onClose={onClose}
        onDone={() => {
          onChanged();
          onClose();
        }}
      />
    );
  if (dialog === 'share')
    return (
      <ShareDialog
        artifact={artifact}
        onClose={onClose}
        onDone={() => {
          onChanged();
          onClose();
        }}
      />
    );

  const v = artifact.current_version;
  if (artifact.trashed_at) {
    return (
      <div className="sg-artmenu" onMouseLeave={onClose}>
        <Menu label="回收站中的成果">
          <MenuItem icon="refresh" label="恢复" disabled={untrash.submitting} onSelect={() => void untrash.submit()} />
        </Menu>
      </div>
    );
  }
  return (
    <div className="sg-artmenu" onMouseLeave={onClose}>
      <Menu label="成果操作">
        {showOpen && <MenuItem icon="file" label="打开" onSelect={() => navigate(`/artifacts/${id}`)} />}
        {artifact.source_task && <MenuItem icon="message-square" label="在来源任务中查看" onSelect={() => navigate(`/tasks/${artifact.source_task!.id}`)} />}
        <div className="sg-artmenu__sep" />
        <MenuItem icon="link" label="分享…" onSelect={() => setDialog('share')} />
        {v && (
          <a className="sg-menuitem" role="menuitem" href={downloadUrl(id, v.number)} download onClick={onClose}>
            <Icon name="download" size={15} />
            <span className="truncate">下载</span>
          </a>
        )}
        <MenuItem icon="clock" label="版本历史" onSelect={() => navigate(`/artifacts/${id}/versions`)} />
        <div className="sg-artmenu__sep" />
        <div className="sg-artmenu__sub" onMouseEnter={() => setMoveOpen(true)}>
          <MenuItem icon="folder" label="移动到工作区" onSelect={() => setMoveOpen((o) => !o)} />
          <Icon name="chevron-right" size={12} className="sg-artmenu__chev" />
          {moveOpen && (
            <div className="sg-artmenu__subpanel">
              <Menu label="移动到工作区">
                {targets.map((t) => (
                  <MenuItem
                    key={t.id ?? 'none'}
                    icon={t.id ? 'folder' : 'x'}
                    label={t.name}
                    disabled={move.submitting || (artifact.workspace?.id ?? null) === t.id}
                    onSelect={() => void move.submit(t)}
                  />
                ))}
              </Menu>
            </div>
          )}
        </div>
        <MenuItem icon="square-pen" label="重命名" onSelect={() => setDialog('rename')} />
        <button type="button" role="menuitem" className="sg-menuitem is-danger" disabled={trash.submitting} onClick={() => void trash.submit()}>
          <Icon name="archive" size={15} />
          <span className="truncate">移到回收站</span>
          <span className="sg-artmenu__hint">30 天内可恢复</span>
        </button>
      </Menu>
    </div>
  );
}

export function ShareCell({ artifact }: { artifact: Artifact }) {
  const s = shareText(artifact.share);
  return (
    <span className="sg-artshare">
      <Icon name={s.icon} size={13} />
      <span className="truncate">{s.text}</span>
    </span>
  );
}

/** Citation markers like “[1]” stay inline text but are tinted so they read as references. */
function withRefs(text: string): ReactNode[] {
  return text.split(/(\[\d+\])/).map((part, i) => (/^\[\d+\]$/.test(part) ? <span key={i} className="sg-artdoc__ref">{part}</span> : part));
}

export function BlocksView({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {blocks.map((b, i) => {
        if (b.type === 'heading') return <h3 key={i} className="sg-artdoc__h">{b.text}</h3>;
        if (b.type === 'table')
          return (
            <table key={i} className="sg-artdoc__table">
              <thead>
                <tr>
                  {b.columns.map((c, j) => (
                    <th key={j}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((r, j) => (
                  <tr key={j}>
                    {r.map((c, k) => (
                      <td key={k}>{c}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          );
        return (
          <p key={i} className="sg-artdoc__p">
            {withRefs(b.text)}
          </p>
        );
      })}
    </>
  );
}
