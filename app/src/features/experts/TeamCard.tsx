import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import type { Team } from './api';
import './teams.css';

export function TeamCard({ team, onMention }: { team: Team; onMention?: () => void }) {
  return (
    <article className="sg-tcard">
      <b className="sg-tcard__name">{team.name}</b>
      <p className="sg-tcard__desc">{team.description}</p>
      <span className="sg-tcard__stack" aria-label={`${team.members.length} 位成员`}>
        {team.members.slice(0, 6).map((m) => (
          <Avatar key={m.id} size={24} src={m.avatar_ref} alt={m.name} ring />
        ))}
      </span>
      <div className="sg-tcard__foot">
        <span>
          主责 {team.lead_expert.name} · {team.source === 'template' ? '模板' : team.is_mine ? '你创建' : team.visibility === 'account' ? '企业内共享' : '共享给我'}
          {team.usage_count > 0 ? ` · 用过 ${team.usage_count} 次` : ''}
        </span>
        {onMention && <Button variant="secondary" size={28} label="@ 团队" onClick={onMention} />}
      </div>
    </article>
  );
}
