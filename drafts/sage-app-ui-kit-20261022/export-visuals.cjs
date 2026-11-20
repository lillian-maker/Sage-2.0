// Optional export tool. The UI kit itself has no runtime dependency on sharp.
// NODE_PATH=<existing node_modules> node export-visuals.cjs
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const sharp = require('sharp');
const base = __dirname;
const method = 'browser-measured native SVG, not screenshot';
const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&apos;' }[c]));
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
function number(value, label) {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`Invalid number: ${label}`);
  return Math.round(value * 100) / 100;
}
function box(value, label) {
  if (!value || typeof value !== 'object') throw new Error(`Missing box: ${label}`);
  const b = Object.fromEntries(['x','y','w','h'].map(k => [k, number(value[k], `${label}.${k}`)]));
  if (b.w < 0 || b.h < 0) throw new Error(`Negative dimensions: ${label}`);
  return b;
}
function paint(value, attribute) {
  if (value == null || value === '' || value === 'transparent' || value === 'none') return `${attribute}="none"`;
  const rgba = String(value).match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/i);
  if (rgba) {
    const rgb = rgba.slice(1,4).map(v => Math.min(255, Math.max(0, Number(v))));
    const alpha = rgba[4] == null ? 1 : Math.min(1, Math.max(0, Number(rgba[4])));
    return `${attribute}="rgb(${rgb.join(',')})"${alpha === 1 ? '' : ` ${attribute}-opacity="${alpha}"`}`;
  }
  if (!/^#[\da-f]{3,8}$/i.test(value)) throw new Error(`Unsupported ${attribute}: ${value}`);
  return `${attribute}="${esc(value)}"`;
}
function localAsset(src) {
  if (typeof src !== 'string' || !/^assets\/(?:avatars|brand)\/[A-Za-z0-9_-]+\.(?:webp|svg)$/.test(src)) throw new Error('Asset src must be a local assets/avatars or assets/brand resource');
  const resolved = fs.realpathSync(path.resolve(base, src));
  if (!resolved.startsWith(fs.realpathSync(base) + path.sep)) throw new Error('Asset escapes UI kit directory');
  return fs.readFileSync(resolved);
}
function svgImage(raw, prefix) {
  let source = raw.toString('utf8');
  const open = source.match(/<svg\b([^>]*)>/i);
  const viewBox = open?.[1].match(/\bviewBox\s*=\s*(["'])(.*?)\1/i)?.[2];
  if (!open || !viewBox || !source.includes('</svg>')) throw new Error('SVG resource has no root/viewBox');
  if (/<script\b|<!DOCTYPE|\bon\w+\s*=/i.test(source) || /(?:href|src)\s*=\s*["'](?!#)/i.test(source)) throw new Error('SVG resource must be self-contained and non-executable');
  const ids = new Map();
  source = source.replace(/\bid\s*=\s*(["'])(.*?)\1/g, (_, quote, id) => {
    if (!id || ids.has(id)) throw new Error('SVG resource contains empty or duplicate IDs');
    const next = prefix + id;
    ids.set(id, next);
    return `id=${quote}${next}${quote}`;
  });
  source = source.replace(/url\(\s*(["']?)#([^\s)"']+)\1\s*\)/g, (_, quote, id) => {
    if (!ids.has(id)) throw new Error(`SVG resource references unknown ID: ${id}`);
    return `url(#${ids.get(id)})`;
  });
  source = source.replace(/\b((?:xlink:)?href)\s*=\s*(["'])#(.*?)\2/g, (_, attr, quote, id) => {
    if (!ids.has(id)) throw new Error(`SVG resource references unknown ID: ${id}`);
    return `${attr}=${quote}#${ids.get(id)}${quote}`;
  });
  source = source.replace(/\b(aria-labelledby|aria-describedby)\s*=\s*(["'])(.*?)\2/g, (_, attr, quote, value) => `${attr}=${quote}${value.split(/\s+/).map(id => ids.get(id) || id).join(' ')}${quote}`);
  const rootFill = open[1].match(/\bfill\s*=\s*(["'])(.*?)\1/i)?.[2] || 'none';
  return { viewBox, body: source.slice(source.indexOf('>', source.indexOf('<svg')) + 1, source.lastIndexOf('</svg>')), fill: rootFill };
}
async function prepareAssets(layouts) {
  const cache = new Map();
  for (const layout of layouts) {
    if (!Array.isArray(layout.parts)) throw new Error('Layout parts must be an array');
    for (const part of layout.parts) {
      if (part.type !== 'image' || !part.src?.endsWith('.webp') || cache.has(part.src)) continue;
      const raw = localAsset(part.src);
      if (raw.toString('ascii',0,4) !== 'RIFF' || raw.toString('ascii',8,12) !== 'WEBP') throw new Error('Avatar resource is not WebP');
      const decoded = await sharp(raw).ensureAlpha().raw().toBuffer({ resolveWithObject:true });
      const png = await sharp(decoded.data, { raw:{ width:decoded.info.width, height:decoded.info.height, channels:decoded.info.channels } }).png().toBuffer();
      const roundTrip = await sharp(png).ensureAlpha().raw().toBuffer({ resolveWithObject:true });
      const pixelHash = hash(decoded.data), pngPixelHash = hash(roundTrip.data);
      if (pixelHash !== pngPixelHash || decoded.info.width !== roundTrip.info.width || decoded.info.height !== roundTrip.info.height || decoded.info.channels !== roundTrip.info.channels) throw new Error(`Lossless PNG cache pixel mismatch: ${part.src}`);
      cache.set(part.src, { png, sourceHash:hash(raw), pngHash:hash(png), pixelHash, pngPixelHash, width:decoded.info.width, height:decoded.info.height, channels:decoded.info.channels });
    }
  }
  return cache;
}
function avatarHashes() {
  return new Map(fs.readdirSync(path.join(base,'assets/avatars')).filter(name => name.endsWith('.webp')).sort().map(name => [name,hash(localAsset('assets/avatars/'+name))]));
}
function renderSVG(layout, assets = new Map()) {
  const width = number(layout.width, 'width'), height = number(layout.height, 'height');
  if (width <= 0 || height <= 0 || !/^[a-z0-9][a-z0-9-]{0,80}$/.test(layout.name)) throw new Error('Invalid layout dimensions or name');
  if (!Array.isArray(layout.parts)) throw new Error('Layout parts must be an array');
  const sprite = fs.readFileSync(path.join(base, 'icons.svg'), 'utf8');
  const icons = new Map([...sprite.matchAll(/<symbol\b[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/symbol>/g)].map(m => [m[1],m[2]]));
  if (icons.size !== 32) throw new Error('Expected 32 SVG icon symbols');
  const defs = ['<clipPath id="canvas-clip"><rect width="'+width+'" height="'+height+'"/></clipPath>', '<filter id="surface-shadow" x="-30%" y="-30%" width="160%" height="180%"><feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#000000" flood-opacity=".18"/></filter>'];
  const parts = [];
  layout.parts.forEach((part, index) => {
    if (!part || typeof part !== 'object') throw new Error(`Invalid part at ${index}`);
    let markup = '';
    if (part.type === 'line') {
      markup = `<path d="M${number(part.x,'line.x')} ${number(part.y,'line.y')}L${number(part.x2,'line.x2')} ${number(part.y2,'line.y2')}" fill="none" ${paint(part.stroke,'stroke')} stroke-width="${number(part.strokeWidth ?? 1,'line.strokeWidth')}"/>`;
    } else {
      const b = box(part, `part[${index}]`);
      const radius = number(Math.max(0, Math.min(number(part.radius ?? 0, 'radius'), b.w/2, b.h/2)), 'clampedRadius');
      if (part.type === 'rect') {
        markup = `<rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="${radius}" ry="${radius}" ${paint(part.fill,'fill')} ${paint(part.stroke,'stroke')} stroke-width="${number(part.strokeWidth ?? 0,'strokeWidth')}"${part.shadow ? ' filter="url(#surface-shadow)"' : ''}/>`;
      } else if (part.type === 'text') {
        markup = `<text x="${b.x}" y="${number(b.y+b.h*.8,'baseline')}" ${paint(part.color,'fill')} font-family="PingFang SC,Heiti SC,sans-serif" font-size="${number(part.size,'text.size')}" font-weight="${esc(part.weight ?? 400)}" letter-spacing="${number(part.spacing ?? 0,'text.spacing')}" xml:space="preserve">${esc(part.text ?? '')}</text>`;
      } else if (part.type === 'icon') {
        if (!icons.has(part.id)) throw new Error(`Unknown icon: ${part.id}`);
        markup = `<svg x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" viewBox="0 0 24 24" color="${esc(part.color)}">${icons.get(part.id)}</svg>`;
      } else if (part.type === 'image') {
        const raw = localAsset(part.src);
        if (part.src.endsWith('.svg')) {
          const image = svgImage(raw, `image-${index}-`);
          markup = `<svg x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" viewBox="${esc(image.viewBox)}" fill="${esc(image.fill)}">${image.body}</svg>`;
        } else {
          const cached = assets.get(part.src);
          if (!cached) throw new Error(`WebP cache missing; call await prepareAssets([layout]) before renderSVG(layout, assets): ${part.src}`);
          if (hash(raw) !== cached.sourceHash) throw new Error(`Avatar changed after cache preparation: ${part.src}`);
          markup = `<image x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" href="data:image/png;base64,${cached.png.toString('base64')}" preserveAspectRatio="xMidYMid meet"/>`;
        }
        if (radius) {
          defs.push(`<clipPath id="image-clip-${index}"><rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="${radius}" ry="${radius}"/></clipPath>`);
          markup = `<g clip-path="url(#image-clip-${index})">${markup}</g>`;
        }
      } else throw new Error(`Unknown part type: ${part.type}`);
    }
    if (part.clip) {
      const c = box(part.clip, `part[${index}].clip`);
      defs.push(`<clipPath id="part-clip-${index}"><rect x="${c.x}" y="${c.y}" width="${c.w}" height="${c.h}"/></clipPath>`);
      markup = `<g clip-path="url(#part-clip-${index})">${markup}</g>`;
    }
    parts.push(markup);
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><defs>${defs.join('')}</defs><g clip-path="url(#canvas-clip)">${parts.join('')}</g></svg>`;
}
async function main() {
  const checks = path.join(base, 'checks'), output = path.join(base, 'visuals');
  const files = fs.readdirSync(checks).filter(file => /^layout-[a-z0-9-]+\.json$/.test(file)).sort();
  if (!files.length) throw new Error('No checks/layout-*.json files found');
  const layouts = files.map(file => ({ file, data: JSON.parse(fs.readFileSync(path.join(checks,file),'utf8')) }));
  const names = layouts.map(layout => layout.data.name);
  if (new Set(names).size !== names.length) throw new Error('Duplicate layout output names');
  const sourceAvatarHashes = avatarHashes();
  const assets = await prepareAssets(layouts.map(layout => layout.data));
  const prepared = layouts.map(layout => ({ ...layout, svg: renderSVG(layout.data, assets) }));
  fs.mkdirSync(output, { recursive:true });
  const rasterMethod = 'Source WebP decoded losslessly to in-memory PNG for SVG rendering; decoded RGBA pixel hashes match; source WebP files unchanged; no face or image edits';
  const manifest = { generatedAt:new Date().toISOString(), method, density:144, hashAlgorithm:'sha256', rasterMethod, rasterAssets:[...assets].map(([src,asset]) => ({ source:src, sourceFormat:'webp', embeddedFormat:'png', sourceHash:asset.sourceHash, embeddedHash:asset.pngHash, decodedPixelHash:asset.pixelHash, cachedPixelHash:asset.pngPixelHash, pixelFormat:'RGBA8', width:asset.width, height:asset.height, channels:asset.channels, losslessPixelMatch:true })), entries:[] };
  for (const layout of prepared) {
    const { name } = layout.data;
    const svgBytes = Buffer.from(layout.svg);
    const png = await sharp(svgBytes, { density:144 }).png().toBuffer();
    const metadata = await sharp(png).metadata();
    if (metadata.width !== layout.data.width*2 || metadata.height !== layout.data.height*2) throw new Error(`Unexpected 2x dimensions: ${name}`);
    fs.writeFileSync(path.join(output, name+'.svg'), svgBytes);
    fs.writeFileSync(path.join(output, name+'.png'), png);
    const entry = { name, source:'checks/'+layout.file, file:name+'.png', size:png.length, hash:hash(png), metadata:{ format:metadata.format, width:metadata.width, height:metadata.height, channels:metadata.channels, density:metadata.density }, svg:{ file:name+'.svg', size:svgBytes.length, hash:hash(svgBytes) }, method };
    manifest.entries.push(entry);
    console.log(JSON.stringify(entry));
  }
  const finalAvatarHashes = avatarHashes();
  if (sourceAvatarHashes.size !== finalAvatarHashes.size || [...sourceAvatarHashes].some(([name,digest]) => finalAvatarHashes.get(name) !== digest)) throw new Error('Source WebP assets changed during export');
  manifest.sourceAvatars = { count:sourceAvatarHashes.size, allBytesUnchanged:true, files:[...sourceAvatarHashes].map(([file,digest]) => ({ file:'assets/avatars/'+file, hash:digest })) };
  fs.writeFileSync(path.join(output, 'manifest.json'), JSON.stringify(manifest,null,2)+'\n');
  return manifest;
}
module.exports = { prepareAssets, renderSVG, main };
if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode=1; });
