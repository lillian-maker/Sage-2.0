import { ActionBar } from '../../components/ActionBar/ActionBar';
import { Card, CardHeader } from '../../components/Card/CardHeader';
import { ChecklistItem, type ChecklistItemProps } from './ChecklistItem';
import './review.css';

export interface DeliveryReviewCardProps {
  subtitle: string;
  checklist: ChecklistItemProps[];
  onAccept?: () => void;
  onReturn?: () => void;
  busy?: 'accept' | 'return';
  /** A decided delivery keeps its checklist on screen without the accept / return actions. */
  readOnly?: boolean;
}

export function DeliveryReviewCard({ subtitle, checklist, onAccept, onReturn, busy, readOnly }: DeliveryReviewCardProps) {
  return (
    <Card className="sg-delivery">
      <CardHeader icon="check" title="交付验收" subtitle={subtitle} />
      <div className="sg-delivery__list">
        {checklist.map((c) => (
          <ChecklistItem key={c.label} {...c} />
        ))}
      </div>
      {!readOnly && (
      <ActionBar
        variant="footer"
        hint="退回时请写明原因，主责会只返工相关部分"
        actions={[
          { variant: 'secondary', size: 32, label: '退回修改', onClick: onReturn, busy: busy === 'return', disabled: !!busy },
          { variant: 'primary', size: 32, label: '验收通过', onClick: onAccept, busy: busy === 'accept', disabled: !!busy },
        ]}
      />
      )}
    </Card>
  );
}
