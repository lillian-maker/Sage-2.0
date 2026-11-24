import { useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useShell } from '../../app/AppShell';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { Icon } from '../../components/Icon/Icon';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { IconButton } from '../../components/IconButton/IconButton';
import { Menu, MenuItem, Popover } from '../../components/Menu/MenuItem';
import { Switch } from '../../components/Switch/Switch';
import { Tab } from '../../components/Tabs/Tab';
import { Tag } from '../../components/Tag/Tag';
import { automationsApi, type AutomationBrief, type RunBrief } from '../../features/automations/api';
import { MISSED_REASON, RUN_TAG, dayTime, relativeDayTime, runResultText, scheduleText } from '../../features/automations/format';
import '../../features/automations/automations.css';

function RunRow({ run, navigate }: { run: RunBrief; navigate: (to: string) => void }) {
  const automationId = run.automation?.id;
  return (
    <button
      type="button"
      className="sg-autorow"
      onClick={() => automationId && navigate(`/automations/${automationId}/runs?run=${run.id}`)}
    >
      <span className="sg-autorow__main">
        <span className="sg-autorow__name">{run.automation?.name ?? run.title}</span>
        <span className="sg-autorow__meta">
          {dayTime(run.scheduled_for ?? run.started_at)}
          {run.missed_reason ? ` · ${MISSED_REASON[run.missed_reason] ?? run.missed_reason}` : ''}
        </span>
      </span>
      <span className="sg-autorow__side">
        {run.pending_authorization_count > 0 && <Tag label={`等你确认 · ${run.pending_authorization_count} 项`} tone="warning" size={20} />}
        <Tag {...(RUN_TAG[run.state] ?? { label: run.state, tone: 'neutral' as const, size: 18 as const })} />
      </span>
    </button>
  );
}

export default function Screen({ query, navigate }: ScreenProps) {
  const shell = useShell();
  const tabParam = query.get('tab');
  const tab = tabParam === 'runs' ? 'runs' : tabParam === 'templates' ? 'templates' : 'rules';
  const page = useApiQuery((s) => automationsApi.list(s), []);
  const runs = useApiQuery((s) => (tab === 'runs' ? automationsApi.allRuns({}, s) : Promise.resolve(null)), [tab]);
  const playbooksPage = useApiQuery((s) => (tab === 'templates' ? automationsApi.playbooks(s) : Promise.resolve(null)), [tab]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [runNowId, setRunNowId] = useState<string | null>(null);
  const [menuAt, setMenuAt] = useState<{ id: string; x: number; y: number } | null>(null);

  const toggle = useSubmit(async (a: AutomationBrief) => {
    setBusyId(a.id);
    try {
      if (a.state === 'enabled') {
        try {
          await automationsApi.pause(a.id);
        } catch (e) {
          shell.toast({ tone: 'failure', message: errorMessage(e, '操作失败，请重试'), actionLabel: '重试', onAction: () => onToggle(a) });
          return;
        }
        shell.toast({ tone: 'undo', message: `已暂停「${a.name}」，不会按计划运行` });
      } else {
        shell.toast({ tone: 'undo', message: `正在启用「${a.name}」…` });
        try {
          await automationsApi.enable(a.id);
        } catch {
          // The server rolled the rule back to its previous state; the switch never flipped.
          shell.toast({ tone: 'failure', message: `未能启用「${a.name}」`, detail: '这条规则还没有获得执行权限，已恢复为暂停，没有运行。', actionLabel: '知道了', onAction: () => undefined, variant: 'card' });
          return;
        }
        shell.toast({ tone: 'undo', message: `已启用「${a.name}」` });
      }
      page.reload();
      shell.refresh();
    } finally {
      setBusyId(null);
    }
  });

  const onToggle = (a: AutomationBrief) => void toggle.submit(a);

  const runNow = useSubmit(async (a: AutomationBrief) => {
    setRunNowId(a.id);
    try {
      await automationsApi.runNow(a.id);
      shell.toast({ tone: 'undo', message: `已开始补跑「${a.name}」` });
      page.reload();
    } finally {
      setRunNowId(null);
    }
  });

  const items = page.data?.items ?? [];

  let body;
  if (tab === 'runs') {
    if (runs.status === 'loading' && !runs.data) body = <LoadingState rows={4} />;
    else if (runs.status === 'error' && !runs.data)
      body = <ErrorBanner message={errorMessage(runs.error, '运行记录暂时无法读取')} onRetry={runs.reload} />;
    else if ((runs.data?.items.length ?? 0) === 0) body = <EmptyState icon="timer" title="还没有运行记录" description="规则按计划或手动运行后，记录会出现在这里" />;
    else
      body = (
        <div className="sg-autolist">
          {runs.status === 'error' && <ErrorBanner message={errorMessage(runs.error, '刷新失败')} onRetry={runs.reload} />}
          {(runs.data?.items ?? []).map((r) => (
            <RunRow key={r.id} run={r} navigate={navigate} />
          ))}
        </div>
      );
  } else if (tab === 'templates') {
    const pbs = playbooksPage.data?.items ?? [];
    if (playbooksPage.status === 'loading' && !playbooksPage.data) body = <LoadingState rows={4} />;
    else if (playbooksPage.status === 'error' && !playbooksPage.data)
      body = <ErrorBanner message={errorMessage(playbooksPage.error, '模板暂时无法读取')} onRetry={playbooksPage.reload} />;
    else if (pbs.length === 0) body = <EmptyState icon="layers" title="还没有模板" description="把任务存成 Playbook 后，可以在这里一键新建自动化" />;
    else
      body = (
        <div className="sg-autolist">
          {playbooksPage.status === 'error' && <ErrorBanner message={errorMessage(playbooksPage.error, '刷新失败')} onRetry={playbooksPage.reload} />}
          {pbs.map((p) => (
            <div key={p.id} className="sg-autorow">
              <span className="sg-autorow__main">
                <span className="sg-autorow__name">{p.name}</span>
                <span className="sg-autorow__meta">
                  {[`${p.step_count} 步 · ${p.expert_count} 位专家`, p.created_by?.name, p.variables.length ? `变量 ${p.variables.map((v) => v.name).join('、')}` : '']
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
              <span className="sg-autorow__side">
                <Button variant="secondary" size={28} label="用它新建自动化" onClick={() => navigate(`/automations/new/edit?type=playbook&playbook=${p.id}`)} />
              </span>
            </div>
          ))}
        </div>
      );
  } else if (page.status === 'loading' && !page.data) body = <LoadingState rows={4} />;
  else if (page.status === 'error' && !page.data)
    body = <ErrorBanner message={errorMessage(page.error, '自动化暂时无法读取')} onRetry={page.reload} />;
  else if (items.length === 0)
    body = (
      <EmptyState
        icon="timer"
        title="还没有自动化"
        description="把重复的活交给规则，到点自动开工"
        action={{ label: '新建自动化', onClick: () => navigate('/automations/new/edit?type=prompt') }}
      />
    );
  else
    body = (
      <div className="sg-autolist">
        {page.status === 'error' && <ErrorBanner message={errorMessage(page.error, '刷新失败')} onRetry={page.reload} />}
        <div className="sg-autorow sg-autorow--head">
          <span className="sg-autorow__main">规则</span>
          <span className="sg-autorow__side">
            <span className="sg-autorow__col">下次运行</span>
            <span className="sg-autorow__col">最近结果</span>
            <span className="sg-autorow__col sg-autorow__col--toggle">启用</span>
            <span className="sg-autorow__menusp" />
          </span>
        </div>
        {items.map((a) => {
          const missed = a.last_run?.state === 'missed';
          return (
            <div key={a.id} className="sg-autorow" role="button" tabIndex={0} onClick={() => navigate(`/automations/${a.id}/edit?type=${a.content_kind}`)} onKeyDown={(e) => e.key === 'Enter' && navigate(`/automations/${a.id}/edit?type=${a.content_kind}`)}>
              <span className="sg-autorow__main">
                <span className="sg-autorow__name">{a.name}</span>
                <span className="sg-autorow__meta sg-autorow__meta--lead">
                  {a.lead_expert && <Avatar size={16} alt={a.lead_expert.name} src={a.lead_expert.avatar_ref ?? null} />}
                  {/* A draft Playbook rule has no confirmed lead yet: the Playbook names it on enable. */}
                  {a.content_kind === 'playbook' && a.state === 'draft'
                    ? `Playbook · ${scheduleText(a.schedule)}`
                    : a.lead_expert
                      ? `${a.lead_expert.name} · ${scheduleText(a.schedule)}`
                      : scheduleText(a.schedule)}
                </span>
                {a.state === 'draft' && <span className="sg-autorow__meta">已保存，尚未启用；启用前不会运行。</span>}
              </span>
              <span className="sg-autorow__side">
                <span className="sg-autorow__col">{a.state === 'enabled' && a.next_run_at ? relativeDayTime(a.next_run_at) : a.state === 'paused' ? '已暂停' : '—'}</span>
                <span className="sg-autorow__col">
                  {missed && a.state === 'enabled' ? (
                    <button type="button" className="sg-autorow__runlink" disabled={runNowId === a.id} onClick={(e) => { e.stopPropagation(); void runNow.submit(a); }}>
                      已错过 · 立即运行
                    </button>
                  ) : a.last_run && a.last_run.state !== 'missed' && a.last_run.state !== 'running' ? (
                    runResultText(a.last_run)
                  ) : a.state === 'enabled' ? (
                    <Tag {...(RUN_TAG[a.last_run?.state ?? ''] ?? { label: a.content_kind === 'playbook' ? 'Playbook' : '一段指令', tone: 'neutral' as const, size: 18 as const })} />
                  ) : (
                    '—'
                  )}
                </span>
                <span className="sg-autorow__col sg-autorow__col--toggle" onClick={(e) => e.stopPropagation()}>
                  <Switch
                    checked={a.state === 'enabled'}
                    disabled={busyId === a.id}
                    label={a.state === 'enabled' ? `暂停 ${a.name}` : `启用 ${a.name}`}
                    onChange={() => onToggle(a)}
                  />
                </span>
                <span onClick={(e) => e.stopPropagation()}>
                  <IconButton
                    icon="ellipsis"
                    size={28}
                    variant="ghost"
                    label={`${a.name} 更多操作`}
                    onClick={(e) => {
                      const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                      setMenuAt({ id: a.id, x: r.left, y: r.bottom + 4 });
                    }}
                  />
                </span>
              </span>
            </div>
          );
        })}
      </div>
    );
  const menuAutomation = menuAt ? items.find((x) => x.id === menuAt.id) : undefined;

  return (
    <div className="sg-autopage">
      <PageHeader
        title="自动化"
        description="需要 Mac 开机且 Sage 运行时才会按计划执行；新建后默认不授予执行权限"
        actions={<Button variant="primary" size={32} icon="plus" label="新建自动化" onClick={() => navigate('/automations/new/edit?type=prompt')} />}
      />
      <div className="sg-tabs" role="tablist" aria-label="自动化">
        <Tab size={37} label="规则" selected={tab === 'rules'} {...(page.data ? { count: items.length } : {})} onSelect={() => navigate('/automations', { replace: true })} />
        <Tab size={37} label="运行记录" selected={tab === 'runs'} onSelect={() => navigate('/automations?tab=runs', { replace: true })} />
        <Tab size={37} label="模板" selected={tab === 'templates'} onSelect={() => navigate('/automations?tab=templates', { replace: true })} />
      </div>
      {body}
      {tab === 'rules' && items.length > 0 && (
        <div className="sg-autotip">
          <Icon name="info" size={14} />
          <span> 在任务里说“每周一帮我看一下竞品价格”，Sage 会生成一份自动化草稿，并直接打开编辑页让你检查时间、内容和权限；你启用之后才会按计划运行。</span>
          <Button variant="ghost" size={28} label="查看自动化运行情况" onClick={() => navigate('/automations?tab=runs', { replace: true })} />
        </div>
      )}
      {menuAt && menuAutomation && (
        <Popover at={menuAt} placement="below" width={220} onClose={() => setMenuAt(null)}>
          <Menu label={`${menuAutomation.name} 操作`}>
            <MenuItem icon="square-pen" label="编辑" onSelect={() => navigate(`/automations/${menuAutomation.id}/edit?type=${menuAutomation.content_kind}`)} />
            <MenuItem icon="timer" label="查看运行记录" onSelect={() => navigate(`/automations/${menuAutomation.id}/runs`)} />
            {menuAutomation.state === 'enabled' && <MenuItem icon="refresh" label="立即运行" onSelect={() => void runNow.submit(menuAutomation)} />}
          </Menu>
        </Popover>
      )}
    </div>
  );
}
