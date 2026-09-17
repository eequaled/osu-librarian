"""Static HTML report: filters + mapset/difficulty toggle + checkboxes. No server."""
from __future__ import annotations

import html
import json

from .library import Beatmap


def _row(b: Beatmap) -> str:
    blob = f"{b.artist} {b.title} {b.creator} {b.diff} {b.source} {b.tags} {b.beatmap_id}".lower()
    played = "1" if b.played else "0"
    return (
        f'<tr data-id="{html.escape(b.id)}" data-set="{html.escape(b.set_id)}" '
        f'data-played="{played}" data-mode="{b.mode_name}" data-ranked="{html.escape(b.ranked)}" '
        f'data-stars="{b.stars:.2f}" data-blob="{html.escape(blob, quote=True)}">'
        f'<td><input type="checkbox" class="sel"></td>'
        f"<td>{'▶' if b.played else '·'}</td>"
        f"<td>{html.escape(b.artist)} — {html.escape(b.title)} "
        f"<span class='diff'>[{html.escape(b.diff)}]</span><br>"
        f"<span class='sub'>{html.escape(b.creator)} · ★{b.stars:.2f} · {b.mode_name} · "
        f"{html.escape(b.ranked)} · {html.escape(b.grade or '—')} · id:{b.beatmap_id}</span></td>"
        f"<td>{b.mode_name}</td><td>{b.stars:.2f}</td><td>{'yes' if b.played else 'no'}</td></tr>"
    )


def build_report(maps: list[Beatmap], title: str = "osu! Librarian") -> str:
    rows = "\n".join(_row(b) for b in maps)
    payload = json.dumps([b.__dict__ for b in maps])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#11141a;color:#e8eaf0;margin:0}}
header{{position:sticky;top:0;background:#1a2030;padding:12px;border-bottom:1px solid #333}}
.controls{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
input,select,button{{padding:6px 8px;border-radius:6px;border:1px solid #444;background:#222a3a;color:#eee}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:6px 8px;border-bottom:1px solid #262c3d;font-size:14px}}
.diff{{color:#9fb4ff}}.sub{{color:#8a93a8;font-size:12px}}
#stats{{color:#9fb4ff;font-size:13px;margin-top:6px}}
tr.hidden{{display:none}}tr.group td{{background:#1c2333;font-weight:bold}}
</style>
<header><h2 style="margin:0 0 8px">{html.escape(title)} <small>({len(maps)} diffs)</small></h2>
<div class="controls">
<input id="q" placeholder="search artist/title/mapper/diff/id…" size="32">
<select id="played"><option value="all">played: all</option><option value="played">played only</option><option value="unplayed">unplayed only</option></select>
<select id="mode"><option value="all">mode: all</option><option>osu</option><option>taiko</option><option>catch</option><option>mania</option></select>
<input id="smin" type="number" step="0.5" min="0" value="0" style="width:70px" title="min stars">–
<input id="smax" type="number" step="0.5" min="0" value="99" style="width:70px" title="max stars">
<select id="view"><option value="difficulty">view: by difficulty</option><option value="mapset">view: by mapset</option></select>
<button id="all">select filtered</button><button id="none">clear</button><button id="inv">invert</button>
<button id="copy">copy selected</button><button id="dl">download selected .json</button>
</div><div id="stats"></div></header>
<table><thead><tr><th></th><th>p</th><th>beatmap</th><th>mode</th><th>★</th><th>played</th></tr></thead>
<tbody id="body">{rows}</tbody></table>
<script>
const DATA={payload};
const $=id=>document.getElementById(id);
function apply(){{const q=$('q').value.toLowerCase(),pl=$('played').value,mo=$('mode').value,
smin=parseFloat($('smin').value||0),smax=parseFloat($('smax').value||99),view=$('view').value;
let vis=0,sel=0,played=0;document.querySelectorAll('#body tr').forEach(tr=>{{
const okQ=!q||tr.dataset.blob.includes(q),okP=pl==='all'||(pl==='played')==(tr.dataset.played==='1'),
okM=mo==='all'||tr.dataset.mode===mo,st=parseFloat(tr.dataset.stars||0),okS=st>=smin&&st<=smax;
const show=okQ&&okP&&okM&&okS;tr.classList.toggle('hidden',!show);if(show)vis++;
if(tr.querySelector('.sel').checked)sel++;if(tr.dataset.played==='1'&&show)played++;}});
$('stats').textContent=`showing ${{vis}}/${{DATA.length}} · played in view: ${{played}} · selected: ${{document.querySelectorAll('.sel:checked').length}} · view: ${{view}}`;}}
['q','played','mode','smin','smax','view'].forEach(id=>$(id).addEventListener('input',apply));
$('all').onclick=()=>{{document.querySelectorAll('#body tr:not(.hidden) .sel').forEach(c=>c.checked=true);apply();}};
$('none').onclick=()=>{{document.querySelectorAll('.sel').forEach(c=>c.checked=false);apply();}};
$('inv').onclick=()=>{{document.querySelectorAll('#body tr:not(.hidden) .sel').forEach(c=>c.checked=!c.checked);apply();}};
document.querySelector('#body').addEventListener('change',apply);
$('copy').onclick=()=>{{const ids=[...document.querySelectorAll('.sel:checked')].map(c=>c.closest('tr').dataset.id);
navigator.clipboard.writeText(ids.join('\\n'));alert(ids.length+' ids copied');}};
$('dl').onclick=()=>{{const ids=new Set([...document.querySelectorAll('.sel:checked')].map(c=>c.closest('tr').dataset.id));
const rows=DATA.filter(d=>ids.has(d.id));const a=document.createElement('a');
a.href=URL.createObjectURL(new Blob([JSON.stringify(rows,null,1)],{{type:'application/json'}}));
a.download='osu-selection.json';a.click();}};
apply();
</script></html>"""
