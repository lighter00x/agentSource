"""Export original figures and extraction/audit results to an offline review page."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import shutil
from pathlib import Path

from PIL import Image

from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.persistence.models import Document
from drugpaper.services import Services
from drugpaper.vision.overlay import comprehensive
from scripts.audit_phase5_results import audit, audit_figure
from scripts.ingest_phase5_data import ROOT, settings_for

HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>论文图表人工验收</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f6fa;color:#182333;font:15px/1.55 system-ui,sans-serif}header{padding:22px 30px;background:#10243c;color:white}header h1{font-size:22px;margin:0 0 8px}.note{background:#fff4df;border-left:4px solid #d49a26;padding:12px 18px;margin:18px 24px}a{color:#1761ad}header a{color:#c0dfff}.layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:16px;padding:0 24px 24px}aside,main{background:white;border:1px solid #dde2e9;border-radius:8px;padding:16px}aside{height:80vh;overflow:auto;position:sticky;top:12px}input,select{width:100%;padding:9px;border:1px solid #cbd3dd;margin-bottom:10px;border-radius:4px}button{cursor:pointer;border:1px solid #cbd3dd;border-radius:5px;background:white;padding:8px 12px;font:inherit}.item{display:block;width:100%;text-align:left;margin:7px 0}.item.active{border-color:#2467a4;background:#eef5ff}.badge{display:inline-block;padding:2px 8px;border-radius:4px;background:#e9edf2;font-size:12px;margin:3px}.failed{background:#ffe8e7;color:#a52420}.not_verified,.needs_review{background:#fff0ce;color:#865c09}.passed,.ok{background:#e0f3e5;color:#246b37}.images{display:grid;grid-template-columns:1fr 1fr;gap:16px}.images img{width:100%;border:1px solid #d8dfe8;cursor:zoom-in}.images figure{margin:0}figcaption{font-size:13px;color:#526172}h2{font-size:20px;margin:2px 0 10px}h3{font-size:16px;margin:22px 0 10px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.tabs button.selected{background:#10243c;color:white}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f9;padding:14px;border-radius:5px;font:13px/1.65 monospace}.scroll{overflow:auto;max-height:65vh}table{border-collapse:collapse;font-size:14px;width:100%;white-space:nowrap}th,td{border:1px solid #d8dfe8;padding:7px 10px;text-align:left}th{background:#edf2f7}.muted{color:#65748a;font-size:13px}.empty{padding:20px;background:#fff1ef;border:1px solid #ffc7c1}ul{padding-left:20px}@media(max-width:900px){.layout{grid-template-columns:1fr}aside{height:auto;max-height:220px;position:static}.images{grid-template-columns:1fr}}
</style><header><h1>论文图表人工验收</h1><div id="title"></div><div><a href="paper.pdf" target="_blank">原始 PDF</a> · <a href="../content-audit.json">独立审计 JSON</a> · <a href="../content-audit.md">审计汇总</a> · <span id="source"></span></div></header>
<div class="note">此页面用于核对内容，不能把文件存在、模型自检通过或待复核状态当作真实验收通过。基准比较只覆盖标明的字段；检测框回绘由导出时的几何生成，不是历史模型输入截图。</div>
<div class="layout"><aside><input id="search" placeholder="搜索节点 / 图表类型"><select id="filter"><option value="all">全部结果</option><option value="failed">自动检查不通过</option><option value="not_verified">尚未独立核验</option><option value="recheck">定向重验记录</option></select><div id="list"></div></aside><main id="detail"></main></div>
<script id="data" type="application/json">__DATA__</script><script>
const DATA=JSON.parse(document.getElementById('data').textContent);const latestIndex=DATA.figures.findIndex(f=>f.source_file==='molecular-delivery-current/p001.b003');let selected=Math.max(0,latestIndex),tab=latestIndex>=0?'backfill':'table';
const E=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
const labels={failed:'不通过',passed:'字段基准通过',not_verified:'未独立核验',needs_review:'待复核',ok:'模型自检通过',remediated:'模型修复后通过'};
const badge=(s)=>E('span',labels[s]||s,'badge '+s);
const pretty=x=>JSON.stringify(x,null,2);
document.getElementById('title').textContent=DATA.title;
document.getElementById('source').textContent='文档 '+DATA.document_id+' · 导出 '+DATA.exported;
function list(){const root=document.getElementById('list');root.replaceChildren();const q=document.getElementById('search').value.toLowerCase(),filter=document.getElementById('filter').value;DATA.figures.forEach((f,i)=>{if(!(f.ref+' '+f.caption+' '+f.report.image_type).toLowerCase().includes(q))return;if(filter==='recheck'&&f.variant!=='recheck')return;if(['failed','not_verified'].includes(filter)&&f.audit.acceptance!==filter)return;const b=E('button',undefined,'item'+(selected===i?' active':''));b.append(E('div',f.caption+' · PDF第'+f.page+'页'),E('div',f.ref.split('/').pop()+' · '+f.report.image_type,'muted'),badge(f.audit.acceptance));if(f.variant==='recheck')b.append(E('span',f.source_file,'badge'));b.onclick=()=>{selected=i;list();detail()};root.append(b)})}
function section(parent,title,value){parent.append(E('h3',title));parent.append(E('pre',typeof value==='string'?value:pretty(value)))}
function detail(){const f=DATA.figures[selected],r=f.report,a=f.audit,root=document.getElementById('detail');root.replaceChildren();root.append(E('h2',f.caption+' · '+f.ref.split('/').pop()),badge(a.acceptance),badge(r.status),E('div',(f.variant==='recheck'?'定向重验':'批量原始记录')+' · '+f.source_file+' · '+r.image_type+' · 逻辑调用 '+(r.accounting?.total??((r.accounting?.fixed_calls||0)+(r.accounting?.exception_calls||0)+(r.accounting?.remediation_calls||0))),'muted'));
const images=E('div',undefined,'images');f.images.forEach((pair)=>{for(const [name,path] of [['原始图表',pair.original],['检测框及提取数据回绘',pair.overlay]]){const fig=E('figure'),img=E('img');img.src=path;img.alt=name;img.onclick=()=>window.open(path,'_blank','noopener');fig.append(E('figcaption',name+'（点击查看大图）'),img);images.append(fig)}});root.append(images);
if(f.overlay_note)root.append(E('div',f.overlay_note,'note'));
const tabs=E('div',undefined,'tabs');[['table','结构化结果'],['backfill','分子回填与产物'],['audit','验收与问题'],['trace','过程 / 补救'],['json','完整 JSON']].forEach(([key,title])=>{const b=E('button',title,tab===key?'selected':'');b.onclick=()=>{tab=key;detail()};tabs.append(b)});root.append(tabs);
const body=E('div');root.append(body);const x=r.extraction||{};
if(tab==='table'){if(Array.isArray(x.rows)&&x.rows.length){const scroll=E('div',undefined,'scroll'),table=E('table');for(const [rows,header] of [[x.header_rows||[],true],[x.rows,false]]){for(const row of rows){const tr=E('tr');for(const c of row){const cell=typeof c==='object'&&c!==null?c:{text:c},td=E(header?'th':'td',cell.text??(cell.mol_id!==undefined&&cell.mol_id!==null?'结构框 #'+cell.mol_id:''));td.rowSpan=Number(cell.row_span)||1;td.colSpan=Number(cell.col_span)||1;if(cell.status==='unclear')td.className='needs_review';if(cell.markers?.length)td.append(E('sup',cell.markers.join(',')));tr.append(td)}table.append(tr)}}scroll.append(table);body.append(scroll);section(body,'脚注',x.footnote_markers||{});section(body,'结构主体 / 元数据',{title:x.title,orientation:x.orientation,subject:x.subject_compound_label,scaffold_mol_id:x.scaffold_mol_id,status:x.status})}else if(!Object.keys(x).length||x.status==='unclear'||x.status==='not_a_table'){body.append(E('div','没有可验收的结构化内容；错误兜底或空提取。','empty'));section(body,'实际输出',x)}else{section(body,'实际结构化内容',x)}}
if(tab==='backfill'){section(body,'逐框覆盖（检测覆盖不等于真实召回率）',r.molecular_coverage||{});for(const product of f.delivered_images||[]){body.append(E('h3','片段 '+product.segment+' · '+product.stage));const img=E('img');img.src=product.path;img.style.maxWidth='100%';img.onclick=()=>window.open(product.path,'_blank','noopener');body.append(img)}section(body,'确定性回填结果',r.backfilled_extraction||{});}if(tab==='audit'){section(body,'独立基准对照（注意覆盖范围）',a.reference_check);section(body,'脚注与关联基准对照',a.metadata_comparison||{status:'not_verified'});section(body,'自动验收不通过理由',a.reasons);section(body,'L1检查',r.checks);section(body,'L2逐项自检',r.review);section(body,'未核验字段',r.unchecked);section(body,'未解决问题',r.open_questions)}
if(tab==='trace'){section(body,'调用 / 耗时',r.accounting);section(body,'执行与补救轨迹',r.trace);section(body,'Coordinator问答',r.query_answers);section(body,'子Panel报告',r.child_reports)}
if(tab==='json')section(body,'原始完整 FigureReport',r)}
document.getElementById('search').oninput=list;document.getElementById('filter').onchange=list;list();detail();
</script></html>"""


FINAL_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>图表最终回填结果</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f6fa;color:#182333;font:15px/1.6 system-ui,sans-serif}header{padding:20px 24px;background:#10243c;color:white}h1{font-size:22px;margin:0 0 6px}.layout{display:grid;grid-template-columns:270px minmax(0,1fr);gap:16px;padding:20px}aside,main{background:white;border:1px solid #dde2e9;border-radius:8px;padding:16px}aside{max-height:85vh;overflow:auto;position:sticky;top:16px;align-self:start}button{display:block;width:100%;text-align:left;padding:10px;margin:6px 0;border:1px solid #dde2e9;border-radius:5px;background:white;cursor:pointer;font:inherit}button.active{background:#eef5ff;border-color:#2467a4}h2{font-size:20px;margin:0 0 12px}.muted{color:#65748a;font-size:13px}.note{padding:12px;background:#fff4df;margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:16px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #dde2e9;padding:8px;text-align:left;overflow-wrap:anywhere;min-width:100px}.scroll{overflow:auto}img{display:block;max-width:100%;height:auto;margin:14px 0;border:1px solid #dde2e9}a{color:#1761ad}input{width:100%;padding:9px;border:1px solid #dde2e9;border-radius:4px}@media(max-width:800px){.layout{grid-template-columns:1fr}aside{position:static;max-height:220px}}
</style><header><h1>图表最终回填结果</h1><div id="title"></div><div id="summary"></div></header>
<div class="layout"><aside><input id="search" placeholder="搜索图表"><div id="list"></div></aside><main id="detail"></main></div>
<script id="data" type="application/json">__DATA__</script><script>
const DATA=JSON.parse(document.getElementById('data').textContent);let selected=Math.max(0,DATA.figures.findIndex(f=>f.available));
const E=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
document.getElementById('title').textContent=DATA.title;
document.getElementById('summary').textContent=DATA.figures.length+' 张图表 · '+DATA.figures.filter(f=>f.available).length+' 张已有结构化回填结果';
function list(){const root=document.getElementById('list');root.replaceChildren();const q=document.getElementById('search').value.toLowerCase();DATA.figures.forEach((f,i)=>{if(!(f.caption+' '+f.ref).toLowerCase().includes(q))return;const b=E('button',undefined,i===selected?'active':'');b.append(E('div',f.caption),E('div','第 '+f.page+' 页 · '+(f.available?'已有回填数据':'尚无回填数据'),'muted'));b.onclick=()=>{selected=i;list();detail()};root.append(b)})}
function table(root,names,rows){const wrap=E('div',undefined,'scroll'),t=E('table'),head=E('tr');names.forEach(n=>head.append(E('th',n)));t.append(head);rows.forEach(row=>{const tr=E('tr');row.forEach(c=>tr.append(E('td',c)));t.append(tr)});wrap.append(t);root.append(wrap)}
function detail(){const f=DATA.figures[selected],root=document.getElementById('detail');root.replaceChildren();if(!f)return;root.append(E('h2',f.caption));if(!f.available){root.append(E('div','这张图表尚无结构化回填结果。','note'));return}
root.append(E('div',f.verified?'分子连接关系对照通过；立体化学未独立核验。':f.review_pending?'已保留回填数据，仍有待复核问题。':'结构化数据已回填；内容仍待独立核验。','note'));
const a=E('a','下载完整回填数据');a.href=f.result_path;a.download='';root.append(a);const x=f.extraction;
if(x.scaffold_esmiles){root.append(E('h3','识别母核'));root.append(E('pre',x.scaffold_esmiles))}
if(x.sar_records){root.append(E('h3','SAR 完整分子与文献性质'));if(x.sar_records.assembly_skipped)root.append(E('div','未完成拼接：'+x.sar_records.assembly_skipped,'note'));table(root,['化合物','取代基','完整 SMILES','分子式','文献测定性质','拼接状态'],(x.sar_records.rows||[]).map(r=>[r.label,JSON.stringify(r.values),r.structures?.map(s=>s.canonical_smiles).join('；')||'尚未得到完整结构',r.structures?.map(s=>s.formula).join('；')||'—',(r.properties||[]).map(p=>p.metric_label_raw+': '+p.value_raw+(p.unit_raw||p.unit?' '+(p.unit_raw||p.unit):'')).join('；'),r.status==='completed'?'拼接完成':r.error_code||x.sar_records.assembly_skipped||'待核对']))}
root.append(E('h3','结构化回填结果'));root.append(E('pre',JSON.stringify(x,(key,value)=>['molecule_inventory','molecular_bindings','evidence','measurement_id','assay_id','document_id','produced_by_artifact_id','locked'].includes(key)?undefined:value,2)))}
document.getElementById('search').oninput=list;list();detail();
</script></html>"""


def final_view(figures: list[dict], root: Path) -> list[dict]:
    """One latest record per figure; preserve missing final products explicitly."""
    latest: dict[str, tuple[tuple[bool, float], dict]] = {}
    for figure in figures:
        source = figure["source_file"]
        current = source.startswith("molecular-delivery-current/")
        source_path = root / source
        if source.startswith("molecular-delivery"):
            source_path = root / source.split("/")[0] / "report.json"
        rank = (current, source_path.stat().st_mtime)
        if figure["ref"] not in latest or rank > latest[figure["ref"]][0]:
            latest[figure["ref"]] = (rank, figure)
    return [
        {
            "ref": figure["ref"],
            "caption": figure["caption"],
            "page": figure["page"],
            "available": bool(figure["report"].get("backfilled_extraction")),
            "extraction": figure["report"].get("backfilled_extraction", {}),
            "result_path": "results/" + figure["ref"].split("/")[-1] + ".json",
            "review_pending": figure["report"].get("status") not in {"ok", "remediated"},
            "verified": figure["audit"]["reference_check"].get("status") == "passed"
            and (
                figure["report"].get("image_type") == "reported_structures"
                or figure["report"].get("backfilled_extraction", {}).get("sar_records", {}).get("status")
                == "completed"
            ),
        }
        for _, figure in latest.values()
    ]


async def export(root: Path, doc_id: str) -> Path:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sv = Services.from_settings(settings_for(root))
    out = root / "review"
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    try:
        async with sv.db.session("default") as session:
            doc = await session.get(Document, doc_id)
            if doc is None or doc.user_id != "default":
                raise PermissionError("document not visible")
            pdf_ref = doc.sha256
        src = await IrDocumentSource.load(sv.db, doc_id)
        store = sv.store_for("default")
        (out / "paper.pdf").write_bytes(await store.get(pdf_ref))
        payload = json.loads((root / f"{doc_id}.json").read_text())
        audited = audit(root)
        (root / "content-audit.json").write_text(json.dumps(audited, ensure_ascii=False, indent=2))
        by_ref = {
            f["node_ref"]: f for d in audited["documents"] if d["document_id"] == doc_id for f in d["figures"]
        }
        variants = [(r, "batch", by_ref[r["node_ref"]], f"{doc_id}.json") for r in payload["reports"]]
        recheck_paths = await asyncio.to_thread(lambda: sorted(root.glob("*recheck.json")))
        for recheck_path in recheck_paths:
            for e in json.loads(recheck_path.read_text()).get("tables", []):
                r = e["report"]
                a = audit_figure(r)
                a["reference_check"] = e["independent_comparison"]
                a["metadata_comparison"] = e.get("metadata_comparison", {"status": "not_verified"})
                if (
                    a["reference_check"]["status"] != "passed"
                    or a["metadata_comparison"]["status"] == "failed"
                ):
                    a["acceptance"] = "failed"
                variants.append((r, "recheck", a, recheck_path.name))
        delivery_reports = await asyncio.to_thread(
            lambda: sorted(root.glob("molecular-delivery*/report.json"))
        )
        for delivery_report in delivery_reports:
            for entry in json.loads(delivery_report.read_text()).get("figures", []):
                r = json.loads(await asyncio.to_thread(Path(entry["report_path"]).read_text))
                a = audit_figure(r)
                a["reference_check"] = entry["independent_comparison"]
                if a["reference_check"]["status"] == "failed":
                    a["acceptance"] = "failed"
                variants.append((r, "recheck", a, delivery_report.parent.name + "/" + entry["node"]))
        figures = []
        for index, (r, variant, check, source_file) in enumerate(variants):
            node = src.node(r["node_ref"])
            pairs = []
            note = ""
            for segment, region in enumerate(node.geometry.regions):
                ref = await sv.renderer_for("default").crop(pdf_ref, region)
                image = Image.open(io.BytesIO(await store.get(ref))).convert("RGB")
                image.thumbnail((1600, 1600))
                boxes = [
                    {
                        "bbox": n.geometry.local_bbox.model_dump(),
                        "mol_id": n.attributes.get("reading_index"),
                        "reference": n.attributes.get("label", "?"),
                    }
                    for n in src.ir.nodes
                    if n.parent_id == node.local_id
                    and n.node_type == "molecule"
                    and n.status != "removed"
                    and n.geometry.local_bbox
                    and n.geometry.parent_region_index == segment
                ]
                try:
                    overlay = comprehensive(image, {"boxes": boxes, "extraction": r.get("extraction") or {}})
                except (TypeError, ValueError, KeyError, AttributeError, IndexError) as exc:
                    overlay = comprehensive(image, {"boxes": boxes})
                    note = "结构化内容无法完整回绘，仅显示检测框：" + str(exc)
                paths = {}
                for key, img in [("original", image), ("overlay", overlay)]:
                    name = f"figure_{index:02d}_{segment}_{key}.png"
                    img.save(assets / name)
                    paths[key] = "assets/" + name
                pairs.append(paths)
            delivered = []
            for product in r.get("image_products", []):
                name = f"delivery_{index:02d}_{product['segment']}_{product['stage']}_page{product.get('page', 0)}.png"
                (assets / name).write_bytes(await store.get(product["blob_ref"]))
                delivered.append({**product, "path": "assets/" + name})
            figures.append(
                {
                    "ref": r["node_ref"],
                    "page": (node.page or 0) + 1,
                    "caption": node.label or node.local_id,
                    "report": r,
                    "variant": variant,
                    "source_file": source_file,
                    "audit": check,
                    "images": pairs,
                    "delivered_images": delivered,
                    "overlay_note": note,
                }
            )
        data = {
            "document_id": doc_id,
            "title": src.ir.meta.title,
            "figures": figures,
            "exported": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }
        text = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))
        text = text.replace("../content-audit.json", "content-audit.json").replace(
            "../content-audit.md", "content-audit.md"
        )
        (out / "content-audit.json").write_text(json.dumps(audited, ensure_ascii=False, indent=2))
        audit_md = root / "content-audit.md"
        if audit_md.exists():
            await asyncio.to_thread(shutil.copyfile, audit_md, out / "content-audit.md")
        (out / "audit.html").write_text(text)
        final_figures = final_view(figures, root)
        results_dir = out / "results"
        results_dir.mkdir(exist_ok=True)
        for figure in final_figures:
            if figure["available"]:
                (out / figure["result_path"]).write_text(
                    json.dumps(figure["extraction"], ensure_ascii=False, indent=2) + "\n"
                )
        final_data = {"title": data["title"], "figures": final_figures}
        path = out / "index.html"
        path.write_text(
            FINAL_HTML.replace("__DATA__", json.dumps(final_data, ensure_ascii=False).replace("<", "\\u003c"))
        )
        print("exported", path.resolve(), "figures", len(final_figures))
        return path
    finally:
        await sv.aclose()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--doc", default="doc_01M3X9AEHKJD9EB809Z820QGHZ")
    args = parser.parse_args()
    await export(args.root, args.doc)


if __name__ == "__main__":
    asyncio.run(main())
