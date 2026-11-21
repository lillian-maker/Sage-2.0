import { Icon } from '../../components/Icon/Icon';
import './task.css';

export interface ArtifactRowProps {
  title: string;
  meta: string;
  kind: 'report' | 'file';
  onOpen?: () => void;
}

export function ArtifactRow({ title, meta, kind, onOpen }: ArtifactRowProps) {
  return (
    <button type="button" className={`sg-artifact sg-artifact--${kind}`} onClick={onOpen}>
      <Icon name="file" size={16} />
      <span className="sg-artifact__title truncate">{title}</span>
      {meta && <span className="sg-artifact__meta">{meta}</span>}
    </button>
  );
}
