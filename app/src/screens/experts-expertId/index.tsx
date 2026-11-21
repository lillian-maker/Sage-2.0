import { useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { Menu, MenuItem, Popover } from '../../components/Menu/MenuItem';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { StatusDot } from '../../components/StatusDot/StatusDot';
import { Tab } from '../../components/Tabs/Tab';
import { expertsApi, relativeDay, shortDate, type ExpertDetail } from '../../features/experts/api';
import { DPage, KvRow, MutedLabel, SectionHead, chatWith } from '../../features/experts/parts';
import '../../features/experts/profile.css';

const TIER: Record<string, string> = { official: '官方', community_reviewed: '社区 · 已审核', third_party: '第三方', enterprise: '企业', local_import: '本地导入' };
const SHARE: Record<string, string> = { only_me: '仅自己', department: '指定部门', account: '所有成员可用' };
const ROLE: Record<string, string> = { lead: '主责', collaborator: '协作', participant: '协作', review: '复核' };

type TabKey = 'profile' | 'skills' | 'network' | 'tasks';

function toolState(t: ExpertDetail['tools'][number]) {
  if (t.connection_state === 'connected') return { text: '已连接', cls: 'sg-dok' };
  if (t.connection_state === 'admin_required') return { text: '需管理员开通', cls: 'sg-dwarn' };
  if (t.connection_state) return { text: '需连接', cls: 'sg-dwarn' };
  return { text: '', cls: '' };
}

export default function Screen({ params, query, navigate }: ScreenProps) {
  const expertId = params.expertId!;
  const tabParam = query.get('tab');
  const tab: TabKey = tabParam === 'skills' || tabParam === 'tasks' ? tabParam : 'profile';
  const detail = useApiQuery((signal) => expertsApi.detail(expertId, signal), [expertId]);
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  const e = detail.data;

  const setTab = (t: TabKey) => {
    if (t === 'network') navigate(`/experts/network?expert=${encodeURIComponent(expertId)}`);
    else navigate(`/experts/${encodeURIComponent(expertId)}${t === 'profile' ? '' : '?tab=' + t}`, { replace: true });
  };

  if (!e)
    return (
      <DPage>
        <BackLink label="专家" size={30} onBack={() => navigate('/experts')} />
        <div className="sg-dstate">
          <QueryGate q={detail} label="专家资料" rows={3} />
        </div>
      </DPage>
    );

  const sub = [e.specialty || e.title, e.domain].filter(Boolean).join(' · ');
  const tabs = (
    <div className="sg-tabs" role="tablist" aria-label="专家资料">
      <Tab size={37} label="资料" selected={tab === 'profile'} onSelect={() => setTab('profile')} />
      <Tab size={37} label="技能" count={e.stats.skill_count} selected={tab === 'skills'} onSelect={() => setTab('skills')} />
      <Tab size={37} label="协作网络" selected={false} onSelect={() => setTab('network')} />
      <Tab size={37} label="历史任务" count={e.stats.history_task_count} selected={tab === 'tasks'} onSelect={() => setTab('tasks')} />
    </div>
  );

  const skillsBlock = (
    <>
      <MutedLabel right={`${e.skills.length} 个`}>专业技能</MutedLabel>
      {e.skills.length === 0 && <p className="sg-dmuted">还没有配置技能</p>}
      <div>
        {e.skills.map((s) => (
          <button key={s.extension_id} type="button" className="sg-drow" onClick={() => navigate(`/extensions/skills/${encodeURIComponent(s.extension_id)}`)}>
            <Icon name="puzzle" size={15} />
            <span className="sg-drow__text">
              <span className="sg-drow__name">{s.name}</span>
              <span className="sg-drow__sub">{[TIER[s.publisher_tier] ?? s.publisher_tier, s.installed_version || s.latest_version].filter(Boolean).join(' · ')}</span>
            </span>
            <span className="sg-drow__right">{s.install_state === 'installed' ? '已安装' : s.install_state === 'disabled' ? '已停用' : '未安装'}</span>
          </button>
        ))}
      </div>
      <MutedLabel right="执行时仍需在任务中确认授权">工具与连接</MutedLabel>
      {e.tools.length === 0 && <p className="sg-dmuted">所选技能不需要额外工具</p>}
      <div>
        {e.tools.map((t) => {
          const st = toolState(t);
          return (
            <div key={t.extension_id} className="sg-drow">
              <Icon name="link" size={15} />
              <span className="sg-drow__text">
                <span className="sg-drow__name">{t.name}</span>
                <span className="sg-drow__sub">{['只读', t.required_for ? t.required_for.name : null].filter(Boolean).join(' · ')}</span>
              </span>
              {st.text && <span className={`sg-drow__right ${st.cls}`}>{st.text}</span>}
            </div>
          );
        })}
      </div>
    </>
  );

  const tasksBlock = (
    <>
      <MutedLabel right={e.recent_tasks.length > 0 ? undefined : ''}>最近与你协作</MutedLabel>
      {e.recent_tasks.length === 0 && <EmptyState icon="message-square" title="还没有一起做过的任务" />}
      <div>
        {e.recent_tasks.map((t) => (
          <button key={t.id} type="button" className="sg-drow" onClick={() => navigate(`/tasks/${encodeURIComponent(t.id)}`)}>
            <Icon name="message-square" size={15} />
            <span className="sg-drow__text">
              <span className="sg-drow__name">{t.title}</span>
              <span className="sg-drow__sub">{[t.workspace?.name ?? '未关联工作区', ROLE[t.role] ?? t.role].filter(Boolean).join(' · ')}</span>
            </span>
            <span className="sg-drow__right">{relativeDay(t.last_activity_at)}</span>
          </button>
        ))}
      </div>
    </>
  );

  return (
    <DPage>
      <BackLink label="专家" size={30} onBack={() => navigate('/experts')} />
      {detail.status === 'error' && <ErrorBanner message={errorMessage(detail.error, '专家资料刷新失败')} onRetry={detail.reload} />}
      <header className="sg-eprof">
        <Avatar size={56} src={e.avatar_ref} alt={e.name} />
        <div className="sg-eprof__name">
          <h1>{e.name}</h1>
          <p>
            {sub}
            {e.assignable && (
              <span className="sg-eprof__ok">
                <StatusDot color="color.green.660" size={7} /> 可交办
              </span>
            )}
          </p>
        </div>
        <div className="sg-eprof__actions">
          <Button variant="secondary" size={32} icon="plus" label="以此为模板新建" onClick={() => navigate(`/experts/new/start?template=${encodeURIComponent(e.id)}`)} />
          <Button variant="primary" size={32} icon="message-square" label="对话" onClick={() => chatWith(e.id)} disabled={!e.assignable} />
          <IconButton
            icon="ellipsis"
            size={32}
            variant="ghost"
            label="更多"
            onClick={(ev) => {
              const r = ev.currentTarget.getBoundingClientRect();
              setMenuAt({ x: r.right - 200, y: r.bottom + 6 });
            }}
          />
        </div>
      </header>
      {tabs}
      {tab === 'tasks' ? (
        <div className="sg-eprof__single">{tasksBlock}</div>
      ) : tab === 'skills' ? (
        <div className="sg-eprof__single">{skillsBlock}</div>
      ) : (
        <div className="sg-eprof__cols">
          <div className="sg-eprof__main">
            <MutedLabel>职责</MutedLabel>
            <p className="sg-eprof__resp">{e.responsibilities}</p>
            {e.capability_tags.length > 0 && (
              <>
                <MutedLabel>能力标签</MutedLabel>
                <div className="sg-ecard__tags">
                  {e.capability_tags.map((t) => (
                    <span key={t}>{t}</span>
                  ))}
                </div>
              </>
            )}
            {skillsBlock}
          </div>
          <aside className="sg-eprof__side">
            <MutedLabel>资料</MutedLabel>
            <KvRow k="来源">{e.source === 'system' ? '系统专家' : '我创建的'}</KvRow>
            {e.catalog_code && <KvRow k="目录编号">{e.catalog_code}</KvRow>}
            <KvRow k="版本">{[e.version_label || '草稿', shortDate(e.updated_at)].filter(Boolean).join(' · ')}</KvRow>
            <KvRow k="共享">{SHARE[e.share_scope] ?? e.share_scope}</KvRow>
            {e.reports_to && <KvRow k="汇报给">{e.reports_to.name}</KvRow>}
            <div className="sg-ddivider" />
            {tasksBlock}
            <div className="sg-dnote">
              <Icon name="info" size={14} />
              <span>
                交办示例：{e.start_description ?? '帮我评估这个类目的竞争格局。'}
                <button
                  type="button"
                  className="sg-dlink"
                  onClick={() =>
                    navigate(`/tasks/new?text=${encodeURIComponent(e.start_description ?? '帮我评估这个类目的竞争格局。')}`)
                  }
                >
                  填入输入舱
                </button>
              </span>
            </div>
          </aside>
        </div>
      )}
      {menuAt && (
        <Popover at={menuAt} width={200} onClose={() => setMenuAt(null)}>
          <Menu label="更多操作">
            <MenuItem icon="users" label="查看协作网络" onSelect={() => navigate(`/experts/network?expert=${encodeURIComponent(e.id)}`)} />
            {e.status === 'draft' && e.wizard_step != null && (
                <MenuItem icon="square-pen" label="继续编辑草稿" onSelect={() => navigate(`${WIZARD_PATHS[e.wizard_step! - 1]}?draft=${encodeURIComponent(e.id)}`)} />
              )}
          </Menu>
        </Popover>
      )}
    </DPage>
  );
}

const WIZARD_PATHS = ['/experts/new/identity', '/experts/new/soul', '/experts/new/skills', '/experts/new/tools', '/experts/new/knowledge', '/experts/new/confirm'];

export { SectionHead };
