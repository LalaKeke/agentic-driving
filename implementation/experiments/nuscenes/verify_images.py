"""Decode real images and create an OFFLINE review contact sheet (never a model input)."""
import argparse
import hashlib
import json
from pathlib import Path
import PIL
from PIL import Image,ImageOps,ImageDraw

def check(root):
    report=json.loads((root/'preparation_report.json').read_text());index=json.loads((root/'runtime_index.json').read_text())
    rows=[];canvas=Image.new('RGB',(1280,800),'white');draw=ImageDraw.Draw(canvas)
    for i,(case,item) in enumerate(zip(report['cases'],index['cases'])):
        cap=json.loads((root/item['capture']).read_text());dimensions=[]
        for frame in cap['observations']:
            with Image.open(frame['path']) as im:im.verify()
            with Image.open(frame['path']) as im:
                im.load();dimensions.append(dict(frame_id=frame['id'],camera=frame['camera_id'],width=im.width,height=im.height,mode=im.mode))
                if frame['camera_id']=='CAM_FRONT' and frame['time_id']==cap['metadata']['reference_time_id']:
                    thumb=ImageOps.contain(im.convert('RGB'),(630,355));x=(i%2)*640;y=(i//2)*400
                    canvas.paste(thumb,(x,y+35));draw.text((x+8,y+8),case['case_id']+' / '+case['scene'],fill='black')
        rows.append(dict(case_id=case['case_id'],decoded_images=len(dimensions),images=dimensions))
    result=dict(status='REAL_IMAGE_DECODING_PASS',pillow_version=PIL.__version__,cases=rows,total_images=sum(r['decoded_images'] for r in rows),
                claim='Image readability only; no model grounding or driving feasibility claim')
    dest=root/'offline_evaluation';canvas.save(dest/'sample_contact_sheet.jpg');(dest/'image_checks.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cases',required=True);a=p.parse_args();print(json.dumps(check(Path(a.cases)),indent=2))
