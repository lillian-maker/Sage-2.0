import { Icon } from '../../components/Icon/Icon';
import { Tag, type TagProps } from '../../components/Tag/Tag';
import './automations.css';

export interface PlaybookCardProps {
  playbook: { name: string; meta: string };
  variables: TagProps[];
  variablesLabel?: string;
}

export function PlaybookCard({ playbook, variables, variablesLabel = '变量示例（运行前规则待确认）：' }: PlaybookCardProps) {
  return (
    <section className="sg-playbook">
      <div className="sg-playbook__head">
        <Icon name="layers" size={15} />
        <span className="sg-playbook__name">{playbook.name}</span>
        <span className="sg-playbook__meta">{playbook.meta}</span>
      </div>
      <div className="sg-playbook__label">{variablesLabel}</div>
      <div className="sg-playbook__vars">
        {variables.map((v) => (
          <Tag key={v.label} {...v} />
        ))}
      </div>
    </section>
  );
}
