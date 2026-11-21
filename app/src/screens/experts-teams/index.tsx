import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { expertsApi } from '../../features/experts/api';
import { DPage } from '../../features/experts/parts';
import { TeamCard } from '../../features/experts/TeamCard';
import { EXPERT_TABS } from '../experts/index';
import '../../features/experts/teams.css';

export default function Screen({ navigate }: ScreenProps) {
  const teams = useApiQuery((signal) => expertsApi.teams(signal), []);
  const counts = useApiQuery((signal) => expertsApi.list({ group: 'all' }, signal), []).data?.counts;
  return (
    <DPage>
      <PageHeader
        title="专家"
        description="团队决定“谁来做”，Playbook 决定“怎么做”；在输入框 @ 团队即可整组交办"
        actions={<Button variant="primary" size={32} icon="plus" label="新建团队" onClick={() => navigate('/experts/teams/new')} />}
      />
      <div className="sg-tabs" role="tablist" aria-label="专家分组">
        {EXPERT_TABS.map((t) => (
          <Tab
            key={t.key}
            size={37}
            label={t.label}
            selected={t.key === 'teams'}
            {...(t.key === 'teams' ? (teams.data ? { count: teams.data.total } : {}) : counts ? { count: counts[t.key] } : {})}
            onSelect={() => (t.key === 'teams' ? undefined : navigate(t.key === 'all' ? '/experts' : `/experts?group=${t.key}`))}
          />
        ))}
      </div>
      {teams.status === 'error' && <ErrorBanner message={errorMessage(teams.error, '团队列表暂时无法读取')} onRetry={teams.reload} />}
      {!teams.data ? (
        <div className="sg-dstate">
          <QueryGate q={teams} label="团队" rows={3} />
        </div>
      ) : (
        <div className="sg-tgrid" style={{ marginTop: 22 }}>
          {teams.data.items.map((t) => (
            <TeamCard key={t.id} team={t} onMention={() => navigate('/tasks/new')} />
          ))}
          <button type="button" className="sg-tcard sg-tcard--new" onClick={() => navigate('/experts/teams/new')}>
            <Icon name="plus" size={18} />
            <b>新建团队</b>
            <span>2–8 位专家，设一位默认主责</span>
          </button>
        </div>
      )}
      {teams.data && teams.data.items.length === 0 && (
        <EmptyState icon="users" title="还没有团队" description="把 2–8 位专家组成团队，在输入框 @ 团队即可整组交办" />
      )}
    </DPage>
  );
}
