import { useState } from 'react';
import { Button } from '../Button/Button';
import { IconButton } from '../IconButton/IconButton';
import { Tag, type TagProps } from '../Tag/Tag';
import './Composer.css';

export type ComposerTool = 'materials' | 'experts' | 'skills';

export interface ComposerProps {
  placeholder?: string;
  value?: string;
  contextTag?: TagProps;
  tools: ComposerTool[];
  canSend: boolean;
  busy?: boolean;
  onValueChange?: (value: string) => void;
  onSend?: (value: string) => void;
  onTool?: (tool: ComposerTool) => void;
  /** '100%' fills the container and lifts the composer max width. */
  width?: number | '100%';
  /** Selected materials / experts / skills / workspace shown inside the box, above the text. */
  chips?: TagProps[];
}

const TOOL = {
  materials: { icon: 'paperclip', label: '资料' },
  experts: { icon: 'contact', label: '专家' },
  skills: { icon: 'zap', label: '技能' },
} as const;

export function Composer({ placeholder, value, contextTag, tools, canSend, busy, onValueChange, onSend, onTool, width, chips }: ComposerProps) {
  const [local, setLocal] = useState(value ?? '');
  const text = value ?? local;
  const send = () => {
    if (canSend && !busy && text.trim()) onSend?.(text);
  };
  return (
    <div className="sg-composer" style={width === '100%' ? { width, maxWidth: 'none' } : { width }}>
      {chips && chips.length > 0 && (
        <div className="sg-composer__chips">
          {chips.map((c, i) => (
            <Tag key={i} {...c} />
          ))}
        </div>
      )}
      <textarea
        className="sg-composer__input"
        rows={1}
        value={text}
        placeholder={placeholder}
        aria-label={placeholder ?? '输入消息'}
        onChange={(e) => {
          setLocal(e.target.value);
          onValueChange?.(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="sg-composer__bar">
        {contextTag && <Tag {...contextTag} />}
        {tools.map((t) => (
          <Button key={t} variant="ghost" size={32} icon={TOOL[t].icon} label={TOOL[t].label} className="sg-composer__tool" onClick={() => onTool?.(t)} />
        ))}
        <span className="sg-composer__spacer" />
        {/* The Figma send button is a flat two-color export; drawing it from the arrow glyph keeps it themeable. */}
        <IconButton
          icon="arrow-right"
          size={32}
          iconSize={18}
          variant={canSend ? 'primary' : 'tonal'}
          label="发送"
          disabled={!canSend || busy}
          onClick={send}
          className="sg-composer__send"
        />
      </div>
    </div>
  );
}
