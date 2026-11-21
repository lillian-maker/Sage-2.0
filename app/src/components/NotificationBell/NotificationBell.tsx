import { createContext, useContext, useLayoutEffect } from 'react';
import { IconButton } from '../IconButton/IconButton';

/**
 * The shell shows one bell in the top-right corner of every page. Pages whose own top bar already
 * holds the bell (task header, experts frame, search layer…) render this component instead, which
 * registers with the shell so the corner bell steps aside and there is never a second one.
 */
const BellSlot = createContext<(() => () => void) | null>(null);
export const BellSlotProvider = BellSlot.Provider;

export function NotificationBell({ onClick, size = 28, shell }: { onClick: () => void; size?: 28 | 32; shell?: boolean }) {
  const register = useContext(BellSlot);
  useLayoutEffect(() => (shell || !register ? undefined : register()), [register, shell]);
  return <IconButton icon="bell" size={size} variant="ghost" label="通知" onClick={onClick} />;
}
