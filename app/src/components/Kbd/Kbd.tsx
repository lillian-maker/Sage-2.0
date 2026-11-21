import './Kbd.css';

export interface KbdProps {
  /** One key ("⌘K" style text) or a chord; an array renders one cap per key with the gap handled inside. */
  keys: string | string[];
}

export function Kbd({ keys }: KbdProps) {
  if (Array.isArray(keys)) {
    return (
      <span className="sg-kbd-group">
        {keys.map((k, i) => (
          <kbd key={i} className="sg-kbd">
            {k}
          </kbd>
        ))}
      </span>
    );
  }
  return <kbd className="sg-kbd">{keys}</kbd>;
}
