#!/usr/bin/env python3
"""Register a generated pair and export its unmodified square halves and sizes."""
from pathlib import Path
from PIL import Image
import argparse,json,hashlib,shutil,io,os

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--id',required=True);p.add_argument('--source',required=True);p.add_argument('--prompt-file',required=True);p.add_argument('--replace',action='store_true');a=p.parse_args()
roles={x['id']:x for x in json.loads((ROOT/'design/characters.json').read_text())}
assert a.id in roles
src=Path(a.source).resolve();assert src.is_file()
with Image.open(src) as im:
 im.load();w,h=im.size
 assert w==h*2, f'Expected exact 2:1 sheet, got {w}x{h}'
 image=im.convert('RGB')
target=ROOT/'sources'/f'{a.id}.png'
assert not target.exists() or a.replace, f'Already registered {a.id}'
shutil.copyfile(src,target)
outputs={}
for n,theme in enumerate(['dark','light']):
 square=image.crop((n*h,0,(n+1)*h,h));out=ROOT/'assets'/theme;out.mkdir(parents=True,exist_ok=True)
 master=out/f'{a.id}.png';buffer=io.BytesIO();square.save(buffer,format='PNG',optimize=True)
 temporary=master.with_suffix('.complete.tmp');temporary.write_bytes(buffer.getvalue());os.replace(temporary,master)
 sizes={}
 for size in [32,48,64,80,128,256,512]:
  folder=out/str(size);folder.mkdir(exist_ok=True);wp=folder/f'{a.id}.webp'
  square.resize((size,size),Image.Resampling.LANCZOS).save(wp,'WEBP',quality=90,method=6)
  sizes[str(size)]={'path':str(wp.relative_to(ROOT)),'width':size,'height':size,'bytes':wp.stat().st_size}
 outputs[theme]={'png':{'path':str(master.relative_to(ROOT)),'width':h,'height':h,'bytes':master.stat().st_size,'sha256':hashlib.sha256(master.read_bytes()).hexdigest()},'webp':sizes}
record={**roles[a.id],'source':str(target.relative_to(ROOT)),'sourceDimensions':[w,h],'sourceSha256':hashlib.sha256(target.read_bytes()).hexdigest(),'prompt':Path(a.prompt_file).read_text(),'generationMethod':'built-in image_gen','assets':outputs,'animation':False,'postProcessing':'equal-square crop, resize and format conversion only'}
(ROOT/'design'/f'{a.id}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
print(json.dumps({'id':a.id,'source':[w,h],'themes':2,'nativePng':2,'webpSizesPerTheme':7,'registered':True}))
