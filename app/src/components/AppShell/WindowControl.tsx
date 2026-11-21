import { color } from '../../tokens';

export interface WindowControlProps {
  kind: 'close' | 'minimize' | 'zoom';
}

const FILL = { close: 'color.red.330', minimize: 'color.orange.410', zoom: 'color.green.530' } as const;

/** Placeholder for macOS traffic lights in the browser; Electron (titleBarStyle hiddenInset) draws the real ones. */
export function WindowControl({ kind }: WindowControlProps) {
  return <span aria-hidden style={{ display: 'inline-block', width: 12, height: 12, borderRadius: 6, background: color(FILL[kind]), flex: 'none' }} />;
}
