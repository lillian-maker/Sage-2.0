import { useEffect, useState } from 'react';
import { useShell } from '../../app/AppShell';
import { Link, type ScreenProps } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Menu, MenuItem } from '../../components/Menu/MenuItem';
import { ErrorBanner, ForbiddenState, LoadingState, Toast, errorMessage } from '../../components/States/States';
import { Tag } from '../../components/Tag/Tag';
import { apiMessage } from '../../features/task/model';
import { artifactApi, downloadUrl, stateName, versionName, versionTag } from '../../features/artifacts/model';
import { ArtifactMenu, BlocksView, ShareDialog, type MoveTarget, type Notice } from '../../features/artifacts/parts';
import { dayTime, wsApi } from '../../features/workspaces/model';

export default function Screen({ params, query, navigate }: ScreenProps) {
  const id = params.artifactId ?? '';
  const shell = useShell();
  const detail = useApiQuery((s) => artifactApi.get(id, s), [id]);
  const versions = useApiQuery((s) => artifactApi.versions(id, s), [id]);
  const workspaces = useApiQuery((s) => wsApi.list(s), []);
  const current = detail.data?.current_version?.number;
  const vParam = Number(query.get('v'));
  const viewing = Number.isInteger(vParam) && vParam > 0 ? vParam : current;
  const content = useApiQuery((s) => (viewing ? artifactApi.content(id, viewing, s) : Promise.resolve(null)), [id, viewing]);
  const citations = useApiQuery((s) => (viewing ? artifactApi.citations(id, viewing, s) : Promise.resolve(null)), [id, viewing]);

  const [menu, setMenu] = useState<'more' | 'versions' | null>(null);
  const [sharing, setSharing] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const a = detail.data;
  const accept = useSubmit(async () => {
    if (!a?.delivery || !a.source_task) return;
    await artifactApi.acceptDelivery(a.source_task.id, a.delivery.id);
    detail.reload();
    versions.reload();
    shell.refresh();
  });
  useEffect(() => {
    if (accept.error) setNotice({ tone: 'failure', text: apiMessage(accept.error, '验收失败，请重试') });
  }, [accept.error]);

  if (detail.status === 'error' && (detail.error.status === 403 || detail.error.status === 404)) {
    return (
      <div className="sg-arts__main">
        <ForbiddenState title="你没有查看此成果的权限" action={{ label: '返回', onClick: () => navigate('/artifacts') }} />
      </div>
    );
  }

  const shown = versions.data?.items.find((v) => v.number === viewing);
  const head = shown ?? a?.current_version ?? null;
  const targets: MoveTarget[] = [...(workspaces.data?.items ?? []).map((w) => ({ id: w.id, name: w.name })), { id: null, name: '未关联工作区' }];
  const canAccept = !!a?.delivery && a.delivery.decision === 'pending' && a.delivery.version === viewing && !!a.source_task;
  const pickVersion = (n: number) => navigate(n === current ? `/artifacts/${id}` : `/artifacts/${id}?v=${n}`, { replace: true });
  const reload = () => {
    detail.reload();
    versions.reload();
  };
  const blocks = content.data?.body.blocks ?? [];
  const citeCount = citations.data?.total ?? (viewing === current ? a?.current_version?.citation_count : undefined);

  return (
    <div className="sg-artview">
      <div className="sg-artview__top">
        <IconButton
          icon="chevron-left"
          size={28}
          variant="ghost"
          label="返回"
          onClick={() => (a?.source_task ? navigate(`/tasks/${a.source_task.id}`) : navigate('/artifacts'))}
        />
        {a?.source_task && (
          <Link className="sg-artview__crumb" href={`/tasks/${a.source_task.id}`}>
            {a.source_task.title} /
          </Link>
        )}
        <span className="sg-artview__name truncate">{a?.title ?? ''}</span>
        {head && (
          <button type="button" className="sg-artview__tagbtn" aria-haspopup="menu" aria-label="切换版本" onClick={() => setMenu(menu === 'versions' ? null : 'versions')}>
            <Tag {...versionTag(head)} icon="chevron-down" iconAfter />
          </button>
        )}
        {menu === 'versions' && versions.data && (
          <div className="sg-artview__versions" style={{ left: 24 }} onMouseLeave={() => setMenu(null)}>
            <Menu label="版本">
              {versions.data.items.map((v) => (
                <MenuItem
                  key={v.number}
                  icon={v.number === viewing ? 'check' : 'file'}
                  label={versionName(v)}
                  onSelect={() => {
                    setMenu(null);
                    pickVersion(v.number);
                  }}
                />
              ))}
            </Menu>
          </div>
        )}
        <div className="sg-artview__end">
          {viewing && (
            <a className="sg-iconbtn-link" href={downloadUrl(id, viewing)} download aria-label="下载">
              <IconButton icon="download" size={28} variant="ghost" label="下载" tabIndex={-1} />
            </a>
          )}
          <Button variant="secondary" size={30} icon="link" label="分享…" disabled={!a?.can_edit} onClick={() => setSharing(true)} />
          {a?.source_task && (
            <Button variant="secondary" size={30} label="存为 Playbook" onClick={() => navigate(`/tasks/${a.source_task!.id}#save-playbook`)} />
          )}
          {canAccept && <Button variant="primary" size={30} icon="check" label="验收通过" busy={accept.submitting} onClick={() => void accept.submit()} />}
          <IconButton icon="ellipsis" size={28} variant="ghost" label="更多操作" onClick={() => setMenu(menu === 'more' ? null : 'more')} />
        </div>
        {menu === 'more' && a && (
          <div className="sg-artview__menu">
            <ArtifactMenu
              artifact={a}
              targets={targets}
              navigate={navigate}
              showOpen={false}
              onChanged={reload}
              onNotice={setNotice}
              onClose={() => setMenu(null)}
            />
          </div>
        )}
      </div>

      <div className="sg-artview__body">
        <div className="sg-artview__canvas">
          {detail.status === 'error' && <ErrorBanner message={errorMessage(detail.error, '成果暂时无法读取')} onRetry={detail.reload} />}
          {a?.trashed_at && <InlineNotice icon="archive" tone="muted" text="这个成果在回收站中，30 天内可以恢复" />}
          <article className="sg-artdoc">
            <h2 className="sg-artdoc__title">{a?.title ?? ''}</h2>
            {head && (
              <div className="sg-artdoc__meta">
                {[
                  head.author?.name,
                  `${stateName(head.state)} v${head.number}`,
                  dayTime(head.created_at),
                  citeCount !== undefined ? `引用资料 ${citeCount} 份` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
            )}
            {content.status === 'error' && <ErrorBanner message={errorMessage(content.error, '正文暂时无法读取')} onRetry={content.reload} />}
            {content.status === 'loading' && !content.data && <LoadingState rows={4} />}
            {content.data && blocks.length > 0 && <BlocksView blocks={blocks} />}
            {content.data && blocks.length === 0 && (
              <InlineNotice icon="info" tone="muted" text="这类成果暂不支持在线预览，可以下载后查看" />
            )}
          </article>
        </div>

        <aside className="sg-artside" aria-label="版本与引用">
          <div className="sg-artside__head">
            <span>版本</span>
            <Link href={`/artifacts/${id}/versions`}>对比</Link>
          </div>
          {versions.status === 'error' && <InlineNotice icon="info" tone="error" text="版本暂时无法读取" action="重试" onAction={versions.reload} />}
          {versions.data?.items.map((v) => (
            <button key={v.number} type="button" className={`sg-artside__version${v.number === viewing ? ' is-active' : ''}`} onClick={() => pickVersion(v.number)}>
              <Icon name="file" size={14} />
              <span className="truncate">{versionName(v)}</span>
              <span className="sg-artside__meta">{dayTime(v.created_at)}</span>
            </button>
          ))}

          <div className="sg-artside__section">
            <div className="sg-artside__head">
              <span>{citations.data ? `引用资料 · ${citations.data.total}` : '引用资料'}</span>
            </div>
            {citations.status === 'error' && <InlineNotice icon="info" tone="error" text="引用暂时无法读取" action="重试" onAction={citations.reload} />}
            {citations.data?.items.length === 0 && <InlineNotice icon="info" tone="muted" text="这个版本没有引用资料" />}
            {citations.data?.items.map((c) => (
              <div key={c.id} className="sg-artside__cite">
                <span className="truncate">
                  [{c.ref_index}] {c.document?.title ?? c.label.split(' · ')[0]}
                </span>
                <span className="sg-artside__meta">{c.cited_version ? `v${c.cited_version}` : c.label.split(' · ')[1] ?? ''}</span>
              </div>
            ))}
          </div>

          {head?.state === 'draft' && (
            <div className="sg-artside__note">
              <Icon name="info" size={12} />
              <span>
                验收后版本会标为“已验收 v{head.number}”，之后的修改会生成 v{(current ?? head.number) + 1}，不覆盖已验收版本。
              </span>
            </div>
          )}
        </aside>
      </div>

      {sharing && a && (
        <ShareDialog
          artifact={a}
          onClose={() => setSharing(false)}
          onDone={() => {
            setSharing(false);
            detail.reload();
          }}
        />
      )}
      {notice && (
        <Toast
          tone={notice.tone}
          message={notice.text}
          {...(notice.undo
            ? {
                actionLabel: '撤销',
                onAction: () => {
                  notice.undo?.();
                  setNotice(null);
                },
              }
            : {})}
          onClose={() => setNotice(null)}
        />
      )}
    </div>
  );
}
