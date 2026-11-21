import { useEffect, useState } from 'react';
import { TextField } from '../../components/TextField/TextField';

/** Text input that saves on blur or Enter; Esc restores the saved value. */
export function CommitField({
  value,
  onCommit,
  validate,
  placeholder,
  width = 240,
  label,
  maxLength,
}: {
  value: string;
  onCommit: (next: string) => Promise<boolean>;
  validate?: (next: string) => string | null;
  placeholder?: string;
  width?: number;
  label: string;
  maxLength?: number;
}) {
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setDraft(value), [value]);
  const commit = async () => {
    const next = draft.trim();
    if (next === value) {
      setDraft(value);
      setError(null);
      return;
    }
    const problem = validate?.(next) ?? null;
    setError(problem);
    if (problem) return;
    const ok = await onCommit(next);
    if (!ok) setError('未保存，请重试');
  };
  return (
    <span className="sg-commitfield">
      <TextField
        size={32}
        width={width}
        value={draft}
        placeholder={placeholder}
        invalid={!!error}
        aria-label={label}
        maxLength={maxLength}
        onValueChange={(v) => {
          setDraft(v);
          if (error) setError(null);
        }}
        onBlur={() => void commit()}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          if (e.key === 'Escape') {
            setDraft(value);
            setError(null);
          }
        }}
      />
      {error && (
        <span className="sg-commitfield__error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}
