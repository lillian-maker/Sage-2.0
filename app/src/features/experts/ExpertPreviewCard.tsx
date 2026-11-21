import { PersonRow } from '../../components/PersonRow/PersonRow';
import './experts.css';

export interface ExpertDraft {
  name: string;
  specialty: string;
  avatarRef?: string | null;
  tags: string[];
}

export interface ExpertPreviewCardProps {
  expert: ExpertDraft;
}

export function ExpertPreviewCard({ expert }: ExpertPreviewCardProps) {
  return (
    <aside className="sg-sidecard">
      <div className="sg-sidecard__label">预览</div>
      <PersonRow size={56} name={expert.name || '未命名专家'} subtitle={expert.specialty} avatar={{ alt: expert.name || '专家', src: expert.avatarRef ?? undefined }} />
      <div className="sg-sidecard__tags">
        {expert.tags.map((t) => (
          <span key={t}>{t}</span>
        ))}
      </div>
    </aside>
  );
}
