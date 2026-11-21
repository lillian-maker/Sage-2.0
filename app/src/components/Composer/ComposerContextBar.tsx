import { Icon } from '../Icon/Icon';
import { StatusDot } from '../StatusDot/StatusDot';
import { Tag } from '../Tag/Tag';
import './Composer.css';
import './ComposerContextBar.css';

export interface ComposerContextBarProps {
  lead: string;
  model: string;
  trial?: boolean;
  onLead?: () => void;
  onModel?: () => void;
}

export function ComposerContextBar({ lead, model, trial, onLead, onModel }: ComposerContextBarProps) {
  return (
    <div className="sg-ctxbar">
      <StatusDot color="color.green.660" size={6} />
      <button type="button" className="sg-ctxbar__pick" onClick={onLead}>
        主责：{lead}
        <Icon name="chevron-down" size={12} />
      </button>
      <span className="sg-ctxbar__sep">·</span>
      <button type="button" className="sg-ctxbar__pick" onClick={onModel}>
        模型：{model}
        <Icon name="chevron-down" size={12} />
      </button>
      <span className="sg-ctxbar__end">{trial && <Tag label="本机试用" tone="neutral" size={20} icon="info" iconAfter />}</span>
    </div>
  );
}
