"""Ranked output: markdown for reading, HTML for the browser, JSON for the next stage."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path


def _why(reasons_json: str, limit: int = 6) -> str:
    try:
        rs = json.loads(reasons_json)
    except Exception:
        return ""
    parts = []
    for r in rs[:limit]:
        mark = "T" if r.get("where") == "title" else ("+" if r.get("where") == "meta" else "b")
        parts.append(f"{r['term']}({mark}{r['pts']:+g})")
    return ", ".join(parts)


def _bucket(rows, verdict):
    return [r for r in rows if r["verdict"] == verdict]


def to_markdown(rows, new_keys: set[str], stats: dict) -> str:
    ts = datetime.now(timezone.utc).astimezone().strftime("%B %d, %Y %H:%M %Z")
    out = [f"# Job pipeline — {ts}", ""]
    out.append(f"{stats.get('fetched',0)} postings pulled, {stats.get('new',0)} new since last run, "
               f"{len(_bucket(rows,'keep'))} apply, {len(_bucket(rows,'maybe'))} look.")
    if stats.get("errors"):
        out.append("")
        out.append("**Source errors:** " + "; ".join(stats["errors"]))
    for verdict, label in (("keep", "APPLY"), ("maybe", "LOOK")):
        bucket = _bucket(rows, verdict)
        if not bucket:
            continue
        out += ["", f"## {label} ({len(bucket)})", ""]
        for r in bucket:
            flag = " **NEW**" if r["job_key"] in new_keys else ""
            comp = f" · {r['comp_text']}" if r["comp_text"] else ""
            out.append(f"### {r['score']:g} — [{r['title']}]({r['url']}){flag}")
            out.append(f"{r['company']} · {r['location'] or 'location n/a'}{comp} · "
                       f"{r['lane']} · {r['source']}")
            out.append(f"why: {_why(r['reasons'])}")
            out.append("")
    return "\n".join(out)


CSS = """
:root{--bg:#fff;--fg:#14171a;--mut:#5b6570;--line:#e3e6ea;--acc:#1F3864;--new:#0b7a3b}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#12151a;--fg:#e8ebef;--mut:#9aa4b0;--line:#262c35;--acc:#7fa3e0;--new:#4ad48a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px;margin-bottom:24px}
h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--acc);
border-bottom:2px solid var(--acc);padding-bottom:6px;margin:32px 0 12px}
.job{border-bottom:1px solid var(--line);padding:14px 0;display:flex;gap:14px}
.sc{font-variant-numeric:tabular-nums;font-weight:700;color:var(--acc);min-width:42px;font-size:17px}
.ttl{font-weight:600;text-decoration:none;color:var(--fg)}.ttl:hover{color:var(--acc)}
.meta{color:var(--mut);font-size:13px;margin-top:2px}
.why{color:var(--mut);font-size:12px;margin-top:6px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}
.new{color:var(--new);font-size:11px;font-weight:700;letter-spacing:.06em;margin-left:6px}
.err{background:#fdecec;color:#8a1f1f;padding:10px 12px;border-radius:6px;font-size:13px;margin-bottom:18px}
@media(prefers-color-scheme:dark){.err{background:#3a1c1c;color:#f0b4b4}}
"""


def to_html(rows, new_keys: set[str], stats: dict) -> str:
    ts = datetime.now(timezone.utc).astimezone().strftime("%B %d, %Y %H:%M %Z")
    p = [f"<!doctype html><html><head><meta charset=utf-8>",
         "<meta name=viewport content='width=device-width,initial-scale=1'>",
         "<title>Job pipeline</title>", f"<style>{CSS}</style></head><body><div class=wrap>",
         f"<h1>Job pipeline</h1><div class=sub>{ts} — {stats.get('fetched',0)} pulled, "
         f"{stats.get('new',0)} new, {len(_bucket(rows,'keep'))} apply, "
         f"{len(_bucket(rows,'maybe'))} look</div>"]
    if stats.get("errors"):
        p.append("<div class=err><b>Source errors:</b> " +
                 html.escape("; ".join(stats["errors"])) + "</div>")
    for verdict, label in (("keep", "Apply"), ("maybe", "Look")):
        bucket = _bucket(rows, verdict)
        if not bucket:
            continue
        p.append(f"<h2>{label} ({len(bucket)})</h2>")
        for r in bucket:
            flag = "<span class=new>NEW</span>" if r["job_key"] in new_keys else ""
            comp = f" · {html.escape(r['comp_text'])}" if r["comp_text"] else ""
            p.append(
                f"<div class=job><div class=sc>{r['score']:g}</div><div>"
                f"<a class=ttl href='{html.escape(r['url'])}' target=_blank rel=noopener>"
                f"{html.escape(r['title'])}</a>{flag}"
                f"<div class=meta>{html.escape(r['company'])} · "
                f"{html.escape(r['location'] or 'location n/a')}{comp} · "
                f"{html.escape(r['lane'])} · {html.escape(r['source'])}</div>"
                f"<div class=why>{html.escape(_why(r['reasons']))}</div></div></div>")
    p.append("</div></body></html>")
    return "".join(p)


def to_json(rows, new_keys: set[str]) -> str:
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["is_new"] = r["job_key"] in new_keys
        d["reasons"] = json.loads(r["reasons"]) if r["reasons"] else []
        d.pop("description", None)
        out.append(d)
    return json.dumps(out, indent=2)


def write_all(rows, new_keys, stats, outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    files = []
    for name, text in (("digest.md", to_markdown(rows, new_keys, stats)),
                       ("digest.html", to_html(rows, new_keys, stats)),
                       ("digest.json", to_json(rows, new_keys))):
        p = outdir / name
        p.write_text(text, encoding="utf-8")
        files.append(p)
    return files
