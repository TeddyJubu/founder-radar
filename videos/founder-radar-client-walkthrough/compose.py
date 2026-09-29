"""Build an editable, local-asset HyperFrames project from measured narration."""
from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1]).resolve()
data = json.loads((out / "timeline.json").read_text())
(out / "compositions").mkdir(exist_ok=True)
for name, crop in [("today-focus.png", "880:420:200:280"),
                   ("today-header.png", "880:250:200:30")]:
    if not (out/"assets"/name).exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i",
                        str(out/"assets/today.png"), "-vf", f"crop={crop}",
                        "-frames:v", "1", str(out/"assets"/name)], check=True)


def boxes(items, flow=False):
    return ('<div class="boxes flow">' if flow else '<div class="boxes">')+''.join(
        f'<div class="box"><span>{i+1:02}</span><h2>{html.escape(title)}</h2><p>{html.escape(text)}</p></div>'
        for i,(title,text) in enumerate(items))+'</div>'


shot_number = 0

def shot(name, start=0, duration=999):
    global shot_number
    shot_number += 1
    return f'<div id="shot-{shot_number:03}" class="shot clip" data-start="{start}" data-duration="{duration}"><img src="assets/{name}" alt="Isolated demonstration screenshot"></div>'


def body(scene):
    visual = scene['visual']; d=scene['duration']
    if visual.endswith('.png'):
        if visual == "today.png":
            visual = "today-header.png" if scene["title"].startswith("Ready") else "today-focus.png"
        return shot(visual,duration=d)
    if visual == 'tracks':
        return boxes([('Track A · discover','Universities · accelerators · grants · specific startup reporting'),('Track B · verify','Companies House · identity · incorporation evidence')])+ '<div class="note">Registration alone does not prove quality, trading age or funding status.</div>'
    if visual == 'pipeline':
        return boxes([('Source','A traceable clue'),('Company evidence','Identity · facts · source links'),('Fund rules + scores','Hard rules first · repeatable calculation'),('Hermes check','Approve or withhold · no invented scores'),('Today','Company cards ready for you')], flow=True)+'<div class="note">Same facts + same settings → same calculation</div>'
    if visual == 'funds':
        return boxes([('Northstar','Regional and specialist vehicles · North East focus'),('DSW','Separate SEIS and EIS paths · regional preference'),('Outward','Early technology in complex industries'),('Anticus','Yorkshire regional vehicles')])+ '<div class="note">London + DSW EIS: eligible under the soft rule; explain weaker regional fit.<br>Suggest another viable fund when available. Keep actual scores visible.</div>'
    if visual == 'score':
        return '<div class="scoreboard"><div><small>MATCH</small><strong>61.7</strong><p>Known fit to Outward</p></div><div><small>FRESH</small><strong>65.5</strong><p>Discovery opportunity</p></div><div><small>PRIORITY</small><strong>63.2</strong><p>Combined emphasis</p></div></div><div class="formula">0.60 × 61.7 + 0.40 × 65.5 = 63.2</div><div class="note">Fictional company · actual scorer · hard rules still come first</div>'
    if visual == 'source-demo':
        split=d*.36
        return shot('settings-baseline.png',duration=split)+shot('settings-source-off.png',split,d-split)
    if visual == 'rule-demo':
        split=d*.32
        return shot('settings-baseline.png',duration=split)+shot('settings-rule-reject.png',split,d-split)
    if visual == 'threshold-demo':
        split=d*.56
        return shot('settings-threshold.png',duration=split)+shot('settings-weight.png',split,d-split)
    if visual == 'rescore':
        return boxes([('Save settings','Google Sheet configuration'),('Rescore','Calculate stored companies again'),('Final check','Hermes must complete approval'),('Publish','Today · Sheet Today · digest')], flow=True)+ '<div class="note">Incomplete check → awaiting final check → withheld from published lists</div>'
    if visual == 'decisions':
        split=d*.43
        return shot('saved.png',duration=split)+shot('kept.png',split,d-split)
    raise ValueError(visual)


hosts=[]
for i,s in enumerate(data['scenes']):
    key=f'chapter-{i+1:02}';root=f'{key}-frame';d=s['duration']
    style=f'''#{root}{{position:absolute;inset:0;background:#f6f3ec;color:#143e3a;overflow:hidden}}
    #{root} .heading{{position:absolute;left:52px;right:52px;top:28px}}#{root} h1{{font:36px GeorgiaLocal;margin:0 0 8px}}
    #{root} .subtitle{{font:18px ArialLocal;color:#5f7067}}#{root} .number{{float:right;font-size:19px;color:#914329}}
    #{root} .content{{position:absolute;left:0;right:0;top:113px;bottom:92px;overflow:hidden}}
    #{root} .shot{{position:absolute;left:42px;right:42px;top:0;bottom:0;overflow:hidden;border:1px solid #cdd7cb;border-radius:12px}}
    #{root} .shot img{{width:100%;height:100%;object-fit:contain;display:block}}
    #{root} .boxes{{display:flex;gap:17px;margin:25px 48px 0;align-items:stretch}}
    #{root} .box{{position:relative;flex:1;background:#e0e8dc;padding:22px 18px;border-top:4px solid #ad5938;border-radius:10px;min-height:215px}}
    #{root} .flow .box:not(:last-child)::after{{content:'→';position:absolute;right:-22px;top:62px;color:#914329;font-size:30px;z-index:4}}
    #{root} .box span{{font-size:17px;color:#914329}}#{root} h2{{font:27px GeorgiaLocal;line-height:1.16;margin:18px 0}}#{root} .box p{{font-size:22px;line-height:1.4;margin:0}}
    #{root} .note{{text-align:center;font-size:23px;line-height:1.45;margin:30px 60px;color:#425d50}}
    #{root} .scoreboard{{display:flex;gap:22px;padding:45px 85px 0}}#{root} .scoreboard>div{{flex:1;background:#e0e8dc;text-align:center;padding:25px;border-radius:16px}}
    #{root} small{{font-size:18px;color:#456959}}#{root} strong{{display:block;font:70px GeorgiaLocal;margin:16px}}#{root} .scoreboard p{{font-size:21px}}
    #{root} .formula{{font:31px ArialLocal;text-align:center;margin-top:30px}}
    #{root} .progress{{position:absolute;bottom:84px;left:0;right:0;height:4px;background:#d8ddd2}}
    #{root} .progress-fill{{height:100%;background:#a95231;transform-origin:left center}}
    '''
    # Timeline moves explanatory groups in once, and shows genuine chapter progress.
    script=f'''const tl=gsap.timeline({{paused:true}});
    tl.fromTo('#{root} .heading',{{y:12,opacity:0}},{{y:0,opacity:1,duration:.4,ease:'power2.out'}},0);
    if(document.querySelector('#{root} .box')) tl.fromTo('#{root} .box',{{y:15,opacity:0}},{{y:0,opacity:1,duration:.5,stagger:.45,ease:'power2.out'}},.5);
    tl.fromTo('#{root} .progress-fill',{{scaleX:0}},{{scaleX:1,duration:{d},ease:'none'}},0);
    window.__timelines['{key}']=tl;'''
    comp=f'''<!doctype html><html><body><template><style>{style}</style>
    <div id="{root}" data-composition-id="{key}" data-width="1280" data-height="720" data-duration="{d}">
    <div class="heading"><span class="number">{i+1:02} / {len(data['scenes']):02} · DEMO</span><h1>{html.escape(s['title'])}</h1><div class="subtitle">{html.escape(s['subtitle'])}</div></div>
    <div class="content">{body(s)}</div><div class="progress"><div class="progress-fill"></div></div></div><script>{script}</script></template></body></html>'''
    (out/'compositions'/f'{key}.html').write_text(comp)
    hosts.append(f'<div id="{key}" class="chapter clip" data-composition-id="{key}" data-composition-src="compositions/{key}.html" data-start="{s["start"]}" data-duration="{d}" data-width="1280" data-height="720" data-track-index="1"></div>')
captions=''.join(f'<div id="caption-{j:03}" class="caption clip" data-start="{c["start"]}" data-duration="{c["end"]-c["start"]}" data-track-index="4"><span>{html.escape(c["text"])}</span></div>' for j,c in enumerate(data['captions']))
root=f'''<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=1280,height=720"><title>Founder Radar client walkthrough</title><script src="assets/gsap.min.js"></script><style>
@font-face{{font-family:ArialLocal;src:url('assets/Arial.ttf')}}@font-face{{font-family:GeorgiaLocal;src:url('assets/Georgia.ttf')}}
html,body{{width:100%;height:100%;margin:0;background:#f6f3ec;font-family:ArialLocal}}#root{{position:relative;width:100%;height:100%;overflow:hidden}}.chapter{{position:absolute;inset:0}}.caption{{position:absolute;left:0;right:0;bottom:0;height:84px;background:#143e3a;color:#fff;display:flex;align-items:center;justify-content:center}}.caption>span{{font:27px ArialLocal;text-align:center;line-height:1.3;max-width:1180px}}#caption-bed{{position:absolute;left:0;right:0;bottom:0;height:84px;background:#143e3a}}</style></head><body>
<div id="root" data-composition-id="main" data-width="1280" data-height="720" data-duration="{data['duration']}" data-fps="24">{''.join(hosts)}<div id="caption-bed"></div>{captions}<audio id="voiceover" src="assets/narration.wav" data-start="0" data-duration="{data['duration']}" data-track-index="3" data-volume="1"></audio></div>
<script>window.__timelines=window.__timelines||{{}};window.__timelines.main=gsap.timeline({{paused:true}});window.__timelines.main.to('#caption-bed',{{duration:{data['duration']},backgroundColor:'#143e3a',ease:'none'}},0);</script></body></html>'''
(out/'index.html').write_text(root)
print(f"Editable HyperFrames project: {len(hosts)} chapters, {data['duration']:.2f}s")
