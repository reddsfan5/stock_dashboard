"""Generate illustrated sector guides from curated JSON and local image assets."""
import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / 'content' / 'sectors'
TEMPLATE = ROOT / 'scripts/services/templates/sector_atlas.html'


def build(data, catalog):
    e = lambda value: escape(str(value), quote=True)
    def paras(items):
        return '<ul>' + ''.join('<li>'+e(x)+'</li>' for x in items) + '</ul>'
    nav = [('definition','定义与边界'), ('chain','五层产业链')]
    nav += [(x['id'],x['title'].split('：')[0]) for x in data['chapters']]
    nav += [('parts','零部件速查'),('stocks','相关个股'),('sources','资料说明')]
    toc = ''.join(f'<a href="#{key}">{i:02d} <span>{e(label)}</span></a>' for i,(key,label) in enumerate(nav,1))
    layers = ''.join(f'<article><small>0{i}</small><h3>{e(title)}</h3><p class="atlas-meta">{e(fields)}</p><p>{e(parts)}</p><p class="layer-relation">{e(relation)}</p></article>' for i,(title,fields,parts,relation) in enumerate(data['layers'],1))
    chapters = ''
    for chapter in data['chapters']:
        src = '/assets/sector-atlas/'+data['id']+'/'+chapter['image']
        width,height = (784,1168) if chapter['id']=='rocket' else (1168,784)
        chapters += f'''<section class="atlas-section" id="{e(chapter['id'])}">
<div class="chapter-heading"><span>{e(chapter['eyebrow'])}</span><h2>{e(chapter['title'])}</h2><p>{e(chapter['lead'])}</p></div>
<div class="chapter-body"><figure><button class="atlas-image" data-image="{e(src)}" data-caption="{e(chapter['title'])}" aria-label="放大：{e(chapter['title'])}"><img src="{e(src)}" alt="{e(chapter['alt'])}" loading="lazy" width="{width}" height="{height}"></button><figcaption>专题配图 · 点击放大，可切换原始尺寸</figcaption></figure>
<div class="chapter-notes">{paras(chapter['points'])}<div class="research-question"><small>研究时问一句</small><p>{e(chapter['question'])}</p></div></div></div><details class="image-note"><summary>图片口径与阅读说明</summary><p>{e(chapter['note'])}</p></details></section>'''
    parts = ''.join(f'<details class="part-row"><summary>{e(title)}</summary><p>{e(text)}</p></details>' for title,text in data['parts'])
    stocks = ''.join(f'''<article class="atlas-stock" data-layer="{e(s['layer'])}" data-search="{e(s['name']+' '+s['code']+' '+s['business'])}"><header><a class="stock-name" href="/symbol.html?code={s['code']}">{e(s['name'])}<small>{e(s['code'])}</small></a><span class="atlas-tag">{e(s['layer'])}</span></header><p>{e(s['business'])}</p><p class="atlas-meta">{e(s['boundary'])}</p><footer><a href="{e(s['source'])}" target="_blank" rel="noopener">{e(s['source_label'])} ↗</a><a href="/symbol.html?code={s['code']}">查看标的 →</a></footer></article>''' for s in data['stocks'])
    filters = ''.join(f'<option>{e(x[0])}</option>' for x in data['layers'])
    options = ''.join(f'<option value="{e(x["id"])}" {"selected" if x["id"]==data["id"] else ""}>{e(x["name"])}</option>' for x in catalog)
    values = dict(COVER=e('/assets/sector-atlas/'+data['id']+'/'+data['chapters'][0]['image']),CHECKED=e(data['checked_date']),NAME=e(data['name']),SUBTITLE=e(data['subtitle']),DEFINITION=e(data['definition']),BOUNDARY=e(data['boundary']),TOC=toc,LAYERS=layers,CHAPTERS=chapters,PARTS=parts,STOCKS=stocks,FILTERS=filters,OPTIONS=options,COUNT=str(len(catalog)))
    html = TEMPLATE.read_text(encoding='utf-8')
    for key,value in values.items():
        html = html.replace('__'+key+'__', value)
    return html


def generate():
    catalog = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(CONTENT.glob('*.json'))]
    out = ROOT / 'output'
    out.mkdir(exist_ok=True)
    for data in catalog:
        html = build(data, catalog)
        (out / ('sector_atlas_'+data['id']+'.html')).write_text(html, encoding='utf-8')
        if data['id'] == 'commercial-space':
            (out / 'sector_atlas.html').write_text(html, encoding='utf-8')
    print(f'板块图谱：{len(catalog)} 个专题')


if __name__ == '__main__':
    generate()
