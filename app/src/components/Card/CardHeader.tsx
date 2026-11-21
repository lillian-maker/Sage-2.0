import { Button, type ButtonProps } from '../Button/Button';
import { Icon, type IconName } from '../Icon/Icon';
import { Tag, type TagProps } from '../Tag/Tag';
import './Card.css';

export interface CardHeaderProps {
  icon: IconName;
  title: string;
  subtitle?: string;
  tag?: TagProps;
  actions?: ButtonProps[];
}

export function CardHeader({ icon, title, subtitle, tag, actions }: CardHeaderProps) {
  const stacked = !!(tag || actions?.length);
  return (
    <div className={`sg-cardheader${stacked ? ' is-stacked' : ''}`}>
      <Icon name={icon} size={16} />
      <div className="sg-cardheader__text">
        <span className="sg-cardheader__title">{title}</span>
        {subtitle && <span className="sg-cardheader__subtitle truncate">{subtitle}</span>}
      </div>
      {stacked && (
        <div className="sg-cardheader__end">
          {tag && <Tag {...tag} />}
          {actions?.map((a, i) => <Button key={i} {...a} />)}
        </div>
      )}
    </div>
  );
}

export function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return <section className={'sg-card' + (className ? ' ' + className : '')}>{children}</section>;
}
