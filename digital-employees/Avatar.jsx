import React from 'react';

/** Static avatar, with a matched theme variant and resolution for a 2x display. */
export default function Avatar({id,name,theme='dark',size=48,shape='circle',base='/assets',className='',...rest}) {
  const sizes=[32,48,64,80,128,256,512];
  const resolution=sizes.find(n=>n>=size*2)||512;
  const selectedTheme=theme==='light'?'light':'dark';
  return <img {...rest} className={`wp-employee-avatar ${className}`} data-shape={shape}
    src={`${base}/${selectedTheme}/${resolution}/${id}.webp`} alt={name||id}
    width={size} height={size} loading="lazy" decoding="async" />;
}
