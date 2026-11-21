const images = import.meta.glob('./images/*.png', { query: '?url', import: 'default', eager: true }) as Record<string, string>;

/** Resolves an `img:<imageRef>` key (assets.csv) or a plain URL to a loadable URL. */
export function assetUrl(ref: string | undefined | null): string | undefined {
  if (!ref) return undefined;
  if (ref.startsWith('img:')) return images['./images/' + ref.slice(4) + '.png'];
  // API refs such as avatar_ref are repo-relative ("digital-employees/…"); resolve from the site root.
  if (!/^(https?:|data:|blob:|\/)/.test(ref)) return '/' + ref;
  return ref;
}
