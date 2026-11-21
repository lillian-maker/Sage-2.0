import { useState } from 'react';
import { useThemeStyle, type ThemeStyle } from '../../app/ThemeProvider';
import { assetUrl } from '../../assets';
import './Avatar.css';

export type AvatarSize = 16 | 18 | 20 | 22 | 24 | 26 | 28 | 30 | 32 | 34 | 48 | 56;

export interface AvatarProps {
  size: AvatarSize;
  initials?: string;
  /** `img:<imageRef>` asset key or an absolute URL (e.g. avatar_ref from the API). */
  src?: string | null;
  alt: string;
  /** White 2px ring used where avatars overlap. */
  ring?: boolean;
}

/** “Chen Yu” → “CY”, “Lillian Li” → “L” (a repeated letter is shown once, as in the design), “望野” → “望”. */
export function avatarInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2 && /^[A-Za-z]/.test(parts[0]!)) {
    const [a, b] = [parts[0]![0]!.toUpperCase(), parts[1]![0]!.toUpperCase()];
    return a === b ? a : a + b;
  }
  return name.trim().slice(0, 1).toUpperCase();
}

// avatar_ref values are URLs, site paths such as /api/app/avatars/…, repo-relative image files or img: asset keys;
// anything else (e.g. a bare profile avatar name) is not an image address and falls back to initials.
const IMAGE_REF = /^(https?:|\/\/|\/|img:|data:|blob:)|\.(png|jpe?g|webp|gif|svg)$/i;

/** The single mapping from a person / expert to Avatar props, shared by every screen. */
export function avatarProps(name: string, ref?: string | null): Omit<AvatarProps, 'size'> {
  return { alt: name, src: ref && IMAGE_REF.test(ref) ? ref : null, initials: avatarInitials(name) };
}

/** D47: each theme style has its own expert avatar set under assets/avatars/<set>/<employee ID>.webp. */
export const THEME_AVATAR_SET: Record<ThemeStyle, 'photo' | 'iso' | 'collage' | 'flat' | 'line'> = {
  business_steady: 'photo',
  neo_electric: 'iso',
  fashion_editorial: 'collage',
  comfort_readable: 'flat',
  minimal_focus: 'line',
};

// Only experts' repo avatars are named by employee ID; the five sets cover AGT-001…AGT-050 and the three
// management roles, so other IDs (AGT-051+) and real people's uploads keep their own image.
const EXPERT_AVATAR = /(?:^|\/)(?:digital-employees\/assets\/(?:[^/]+\/)*|assets\/people\/|assets\/avatars\/[a-z]+\/)(AGT-(?:0[0-4]\d|050)|MGMT-(?:CEO|CGO|CGOV))\.webp$/;

/** Theme avatar URL for an expert's own avatar_ref; any other ref is returned unchanged. */
export function themedAvatarSrc(ref: string | null | undefined, style: ThemeStyle): string | null | undefined {
  const m = ref ? EXPERT_AVATAR.exec(ref) : null;
  if (!m || m[1] === 'AGT-000') return ref;
  return `/api/app/avatars/assets/avatars/${THEME_AVATAR_SET[style]}/${m[1]}.webp`;
}

/** Expert avatar by employee ID in a given style (theme cards preview each style side by side). */
export function expertAvatarSrc(employeeId: string, style: ThemeStyle): string {
  return `/api/app/avatars/assets/avatars/${THEME_AVATAR_SET[style]}/${employeeId}.webp`;
}

export function Avatar({ size, initials, src, alt, ring }: AvatarProps) {
  const style = useThemeStyle();
  const resolved = assetUrl(themedAvatarSrc(src, style));
  // A missing image falls back to initials instead of a broken-image glyph.
  const [failed, setFailed] = useState<string | undefined>();
  const url = resolved && resolved !== failed ? resolved : undefined;
  const text = initials ?? alt.trim().slice(0, 1).toUpperCase();
  return (
    <span className={`sg-avatar sg-avatar--s${size}${ring ? ' has-ring' : ''}`} style={{ width: size, height: size }} title={alt}>
      {url ? <img src={url} alt={alt} width={size} height={size} onError={() => setFailed(url)} /> : <span aria-label={alt}>{text}</span>}
    </span>
  );
}
