# -*- coding: utf-8 -*-
"""HTML 渲染器：把 engine 产出的 snapshot 渲染成单文件自包含日报。

零外部依赖、零 JS 依赖（用原生 <details> 做折叠），可直接分享/离线打开。
"""
import html
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

STRENGTH_CLS = {"强": "s-strong", "中": "s-mid", "弱": "s-weak",
                "失效": "s-dead", "样本不足": "s-na"}


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def pct(v, d=2):
    if v is None:
        return "—"
    return f"{v:+.{d}f}%"


def num(v, d=2):
    if v is None:
        return "—"
    return f"{v:.{d}f}"


def cls_of(v):
    if v is None:
        return "flat"
    if v > 0.0001:
        return "up"
    if v < -0.0001:
        return "down"
    return "flat"


def strength_tag(s):
    return f'<span class="tag {STRENGTH_CLS.get(s, "s-na")}">{esc(s)}</span>'


CSS = """
*{box-sizing:border-box}
:root{
  --bg:#f6f7f9; --card:#fff; --ink:#16181d; --ink2:#565d6b; --ink3:#8b919c;
  --line:#e5e7eb; --line2:#eef0f3;
  --up:#d32f2f; --down:#15803d; --accent:#1d4ed8; --accent-bg:#eff4ff;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-size:14px;line-height:1.65;
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:20px 16px 60px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

header.hd{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:16px}
h1{margin:0 0 6px;font-size:21px;font-weight:600;letter-spacing:-.2px}
.sub{color:var(--ink2);font-size:13px;margin:0}
.pillrow{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.pill{display:inline-flex;align-items:baseline;gap:6px;background:#f2f4f7;border-radius:8px;padding:6px 11px;font-size:12.5px;color:var(--ink2)}
.pill b{font-family:var(--mono);font-size:13.5px;font-weight:600;color:var(--ink)}
.pill .v.up{color:var(--up)} .pill .v.down{color:var(--down)} .pill .v.flat{color:var(--ink3)}

h2{font-size:16px;font-weight:600;margin:28px 0 12px;padding-left:11px;border-left:3px solid var(--ink);letter-spacing:-.1px}
h2 .hint{font-weight:400;font-size:12.5px;color:var(--ink3);margin-left:8px;border:0;padding:0}

.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.note{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:11px 14px;font-size:12.5px;color:#713f12;margin:14px 0}
.note b{font-weight:600}

table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line2);white-space:nowrap}
th{background:#fafbfc;color:var(--ink2);font-weight:500;font-size:12.5px;position:sticky;top:0}
td.n,th.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#fafbfc}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--ink3)}
.muted{color:var(--ink3)}

.matrix td.sec{font-weight:500;white-space:nowrap}
.matrix td.exp{font-family:var(--mono);font-weight:600;position:relative}
.matrix td.bar{width:150px;padding:0 10px}
.bar-in{height:7px;border-radius:4px;background:#eef0f3;position:relative;overflow:hidden}
.bar-in i{position:absolute;top:0;bottom:0;display:block;border-radius:4px}
.bar-in i.p{left:50%;background:var(--up)}
.bar-in i.m{right:50%;background:var(--down)}

.tag{display:inline-block;font-size:11.5px;padding:2px 9px;border-radius:99px;font-weight:500;white-space:nowrap}
.s-strong{background:#fdecec;color:#a12d2d}
.s-mid{background:#fdf3e3;color:#8a5a0b}
.s-weak{background:#eef0f3;color:#565d6b}
.s-dead{background:#f3f4f6;color:#8b919c}
.s-na{background:#f3f4f6;color:#8b919c;border:1px dashed #cfd3da}

details.sec{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:10px;overflow:hidden}
details.sec>summary{cursor:pointer;padding:14px 16px;list-style:none;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary:hover{background:#fafbfc}
details.sec>summary .caret{color:var(--ink3);font-size:11px;transition:transform .15s}
details.sec[open]>summary .caret{transform:rotate(90deg)}
details.sec>summary .nm{font-weight:600;font-size:14.5px}
details.sec>summary .spacer{flex:1}
details.sec>summary .mini{font-family:var(--mono);font-size:12.5px;color:var(--ink2)}
.body{padding:2px 16px 16px;border-top:1px solid var(--line2)}
.logic{color:var(--ink2);font-size:12.5px;margin:12px 0 14px;padding-left:10px;border-left:2px solid var(--line)}
.dual{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:720px){.dual{grid-template-columns:1fr}}
.mini-t{font-size:12px;color:var(--ink3);margin:0 0 6px;font-weight:500}
table.mini{font-size:12.5px}
table.mini th,table.mini td{padding:6px 8px}
.prior-warn{background:#f0f7ff;border:1px solid #cfe1fb;border-radius:8px;padding:8px 11px;font-size:12px;color:#1e40af;margin:10px 0}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .k{font-size:12px;color:var(--ink3);margin-bottom:2px}
.kpi .v{font-size:19px;font-weight:600;font-family:var(--mono);font-variant-numeric:tabular-nums}
.kpi .d{font-size:11.5px;color:var(--ink3);margin-top:2px}

.news{margin-top:14px}
.news .it{display:flex;gap:9px;padding:7px 0;border-bottom:1px dashed var(--line2);font-size:12.5px;line-height:1.55}
.news .it:last-child{border-bottom:0}
.news .tm{color:var(--ink3);font-family:var(--mono);font-size:11.5px;flex:0 0 84px}
.news .sr{color:var(--accent);flex:0 0 auto;font-size:11.5px}

footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);color:var(--ink3);font-size:12px;line-height:1.8}
.warnbox{background:#fef2f2;border:1px solid #fecaca;border-radius:10px;padding:12px 14px;font-size:12.5px;color:#7f1d1d;margin-top:16px}
.nav{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}
.nav a{font-size:12px;background:#f2f4f7;border-radius:6px;padding:3px 9px;color:var(--ink2)}
.nav a:hover{background:var(--accent-bg);color:var(--accent);text-decoration:none}
.nav a.on{background:var(--ink);color:#fff}

/* ---- AI 归因解读 ---- */
.ai{background:linear-gradient(180deg,#f7faff 0%,#fff 60%);border:1px solid #dbe6fb;border-radius:12px;padding:0;overflow:hidden}
.ai .hd{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid #e8effb;background:#f3f7ff}
.ai .hd .bd{font-weight:600;font-size:13.5px;color:#1e3a8a}
.ai .hd .pv{font-size:11.5px;color:#7c8aa3;font-family:var(--mono);margin-left:auto}
.ai .bd-in{padding:14px 16px 16px}
.ai p{margin:0;font-size:13.5px;line-height:1.85;color:#1f2733}
.ai .pv-box{margin-top:14px;padding:11px 14px;background:#fff7ed;border:1px solid #fed7aa;border-left:3px solid #ea580c;border-radius:8px;font-size:13px;line-height:1.8;color:#7c2d12}
.ai .pv-box b{color:#9a3412}
.ai-off{background:#fafbfc;border:1px dashed #d8dce3;border-radius:12px;padding:12px 16px;font-size:12.5px;color:var(--ink3)}
.ai-inline{margin:12px 0 0;padding:10px 12px;background:#f7faff;border:1px solid #dbe6fb;border-radius:9px;font-size:12.5px;line-height:1.75;color:#243040}
.ai-inline .lb{display:inline-block;font-size:10.5px;font-weight:600;color:#1d4ed8;background:#e5eeff;border-radius:4px;padding:1px 6px;margin-right:6px;vertical-align:1px}
"""


def kpi_block(d):
    b = d["baseline"]
    items = [
        ("沪深300", b["hs300"]["chg"], f'{num(b["hs300"]["last"])} 收盘'),
        ("创业板指", b["gem"]["chg"], f'{num(b["gem"]["last"])} 收盘'),
        ("纳斯达克", b["ixic"]["chg"], f'{num(b["ixic"]["last"])} 收盘'),
    ]
    out = ['<div class="kpis">']
    for name, chg, sub in items:
        out.append(
            f'<div class="kpi"><div class="k">{esc(name)}</div>'
            f'<div class="v {cls_of(chg)}">{pct(chg)}</div>'
            f'<div class="d">{esc(sub)}</div></div>')
    sig = d["signals"]
    strong = sum(1 for s in sig if s["strength"] == "强")
    acc = d["accuracy"]
    out.append(
        f'<div class="kpi"><div class="k">强传导环节</div>'
        f'<div class="v">{strong}<span style="font-size:13px;color:var(--ink3)">/8</span></div>'
        f'<div class="d">R² ≥ 0.35</div></div>')
    acc_s = "—" if acc is None else f"{acc:.0%}"
    out.append(
        f'<div class="kpi"><div class="k">T+1 命中率</div>'
        f'<div class="v">{acc_s}</div>'
        f'<div class="d">walk-forward 滚动验证</div></div>')
    out.append('</div>')
    return "".join(out)


def matrix_block(d):
    rows = []
    for s in d["signals"]:
        exp = s["expected_excess"]
        if exp is None:
            bar = ""
        else:
            w = min(abs(exp) / 3.0, 1.0) * 50.0
            side = "p" if exp > 0 else "m"
            bar = f'<div class="bar-in"><i class="{side}" style="width:{w:.1f}%"></i></div>'
        r2 = "—" if s["r2"] is None else f'{s["r2"]:.3f}'
        f0 = s["followers"][0] if s["followers"] else None
        foll = "—"
        if f0:
            c = "—" if f0["corr"] is None else f'{f0["corr"]:+.2f}'
            foll = f'{esc(f0["name"])} <span class="muted" style="font-size:11.5px">ρ{c}</span>'
        prior_mark = ' <span class="muted" style="font-size:11px">先验</span>' if s["used_prior"] else ""
        rows.append(
            f'<tr>'
            f'<td class="sec">{esc(s["name"])}</td>'
            f'<td class="n {cls_of(s["us_ret"])}">{pct(s["us_ret"])}</td>'
            f'<td class="n">{num(s["beta"])}</td>'
            f'<td class="n">{r2}{prior_mark}</td>'
            f'<td class="n {cls_of(exp)}">{pct(exp)}</td>'
            f'<td class="bar">{bar}</td>'
            f'<td>{strength_tag(s["strength"])}</td>'
            f'<td>{foll}</td>'
            f'</tr>')
    return (
        '<div class="card"><table class="matrix">'
        '<thead><tr>'
        '<th>产业链环节</th><th class="n">美股隔夜</th><th class="n">β</th><th class="n">R²</th>'
        '<th class="n">预期超额</th><th class="bar">幅度</th><th>传导强度</th><th>跟随意愿最高</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')


def ai_sector(d, sid):
    """某环节的 AI 归因解读（没有则返回空串）。"""
    a = d.get("attribution") or {}
    txt = (a.get("sectors") or {}).get(sid)
    if not txt:
        return ""
    return f'<p class="ai-inline"><span class="lb">AI 归因</span>{esc(txt)}</p>'


def attribution_block(d):
    a = d.get("attribution")
    if not a:
        return ('<div class="ai-off">本节需要 LLM 凭证（<code>.env</code> 中的 '
                '<code>DEEPSEEK_API_KEY</code> 等）。未配置或调用失败时自动跳过，'
                '不影响上方量化信号。</div>')
    pv = a.get("pivot")
    pv_html = (f'<div class="pv-box"><b>最值得盯的一点　</b>{esc(pv)}</div>'
               if pv else "")
    return (
        f'<div class="ai">'
        f'<div class="hd"><span class="bd">AI 归因解读</span>'
        f'<span class="pv">{esc(a.get("provider", ""))} / {esc(a.get("model", ""))}</span></div>'
        f'<div class="bd-in"><p>{esc(a.get("overview", ""))}</p>{pv_html}</div>'
        f'</div>')


def sector_detail(d):
    out = []
    for s in d["signals"]:
        us_rows = "".join(
            f'<tr><td>{esc(x["name"])}</td><td class="muted">{esc(x["code"])}</td>'
            f'<td class="n">{num(x["w"]*100,0)}%</td>'
            f'<td class="n {cls_of(x["chg"])}">{pct(x["chg"])}</td></tr>' for x in s["us_detail"])
        cn_rows = "".join(
            f'<tr><td>{esc(x["name"])}</td><td class="muted">{esc(x["code"])}</td>'
            f'<td class="n">{num(x["w"]*100,0)}%</td>'
            f'<td class="n {cls_of(x["chg"])}">{pct(x["chg"])}</td></tr>' for x in s["cn_detail"])
        fol_parts = []
        for fo in s["followers"]:
            corr_s = "—" if fo["corr"] is None else f'{fo["corr"]:+.2f}'
            fol_parts.append(
                f'<tr><td>{esc(fo["name"])}</td><td class="muted">{esc(fo["code"])}</td>'
                f'<td class="n">{num(fo["last"])}</td>'
                f'<td class="n {cls_of(fo["last_chg"])}">{pct(fo["last_chg"])}</td>'
                f'<td class="n">{corr_s}</td>'
                f'<td class="n muted">{fo["n"]}</td></tr>')
        fol_rows = "".join(fol_parts)

        warn = ""
        if s["used_prior"]:
            warn = (f'<div class="prior-warn">样本不足（{s["n"]} 条 &lt; 8），'
                    f'β 暂用先验值 {num(s["beta"])}，随样本累积会自动切换为实测值。</div>')
        elif s["prior"] and s["strength"] != "样本不足":
            prior_cn = {"strong": "强", "medium": "中", "weak": "弱"}[s["prior"]]
            if prior_cn != s["strength"]:
                warn = (f'<div class="prior-warn">⚠ 先验判断为「{prior_cn}」传导，'
                        f'实测为「{esc(s["strength"])}」—— 以实测为准，先验已被数据修正。</div>')

        exp = s["expected_excess"]
        head = (
            f'<summary><span class="caret">▶</span>'
            f'<span class="nm">{esc(s["name"])}</span>'
            f'{strength_tag(s["strength"])}'
            f'<span class="spacer"></span>'
            f'<span class="mini">美股 {pct(s["us_ret"])} → 预期超额 '
            f'<b class="{cls_of(exp)}">{pct(exp)}</b></span></summary>')

        disp = "—" if s["dispersion"] is None else f'{s["dispersion"]:.2f}'
        body = (
            f'<div class="body">'
            f'<p class="logic">{esc(s["logic"])}</p>'
            f'{ai_sector(d, s["id"])}'
            f'{warn}'
            f'<div class="dual">'
            f'<div><p class="mini-t">美股锚点（隔夜 {esc(d["us_date"])}）</p>'
            f'<table class="mini"><thead><tr><th>标的</th><th>代码</th><th class="n">权重</th>'
            f'<th class="n">涨跌</th></tr></thead><tbody>{us_rows}</tbody></table>'
            f'<p class="mini-t" style="margin-top:8px">锚点离散度（标准差）{disp}'
            f'{"，内部严重分化，环节信号被稀释" if (s["dispersion"] or 0) > 1.5 else ""}</p>'
            f'</div>'
            f'<div><p class="mini-t">A股/港股跟随标的（最新 {esc(d["cn_date"])}）</p>'
            f'<table class="mini"><thead><tr><th>标的</th><th>代码</th><th class="n">最新</th>'
            f'<th class="n">涨跌</th></tr></thead><tbody>{cn_rows}</tbody></table></div>'
            f'</div>'
            f'<p class="mini-t" style="margin-top:14px">逐股跟随意愿 '
            f'<span class="muted">（个股超额收益 与 本环节美股信号 的相关系数 ρ，'
            f'近 {len(s["followers"]) and (s["followers"][0]["n"] if s["followers"] else 0)} 个配对交易日）</span></p>'
            f'<table class="mini"><thead><tr><th>标的</th><th>代码</th><th class="n">最新价</th>'
            f'<th class="n">最新涨跌</th><th class="n">ρ</th><th class="n">样本</th></tr></thead>'
            f'<tbody>{fol_rows}</tbody></table>'
            f'</div>')
        open_attr = " open" if s["strength"] in ("强",) else ""
        out.append(f'<details class="sec"{open_attr}>{head}{body}</details>')
    return "".join(out)


def verify_block(d):
    v = d["verify"]
    if not v:
        return '<div class="card" style="padding:14px"><p class="muted" style="margin:0">样本不足，暂无法验证。</p></div>'
    parts = []
    for x in v:
        hit_tag = ('<span class="tag s-strong">命中</span>' if x["hit"]
                   else '<span class="tag s-dead">未中</span>')
        parts.append(
            f'<tr><td class="sec">{esc(x["name"])}</td>'
            f'<td class="muted">{esc(x["us_date"])}</td><td class="muted">{esc(x["cn_date"])}</td>'
            f'<td class="n {cls_of(x["us_ret"])}">{pct(x["us_ret"])}</td>'
            f'<td class="n">{num(x["beta"])}</td>'
            f'<td class="n {cls_of(x["pred"])}">{pct(x["pred"])}</td>'
            f'<td class="n {cls_of(x["actual"])}">{pct(x["actual"])}</td>'
            f'<td>{hit_tag}</td></tr>')
    rows = "".join(parts)
    acc = d["accuracy"]
    acc_s = "—" if acc is None else f"{acc:.0%}"
    return (
        f'<div class="note"><b>验证口径：</b>只用 T-1 及之前的样本拟合 β，去预测 T 日，再对照 T 日实际超额收益'
        f'——这是唯一诚实的 walk-forward 口径。若用含 T 日的数据拟合再预测 T 日，命中率会虚高。</div>'
        f'<div class="card"><table><thead><tr>'
        f'<th>环节</th><th>美股日</th><th>A股日</th><th class="n">美股</th><th class="n">β</th>'
        f'<th class="n">预测超额</th><th class="n">实际超额</th><th>结果</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></div>'
        f'<div class="kpis" style="margin-top:12px">'
        f'<div class="kpi"><div class="k">方向命中率</div><div class="v">'
        f'{acc_s}</div>'
        f'<div class="d">{sum(1 for x in v if x["hit"])} / {len(v)}（上交易日）</div></div>'
        f'</div>')


def news_block(d):
    if not d.get("news"):
        return ""
    out = ['<div class="card"><div style="padding:4px 14px 10px" class="news">']
    for s in d["signals"]:
        items = d["news"].get(s["id"], [])
        if not items:
            continue
        out.append(f'<p class="mini-t" style="margin:12px 0 4px">{esc(s["name"])}</p>')
        for it in items:
            out.append(
                f'<div class="it"><span class="tm">{esc(it["time"][-5:] if it["time"] else "")}</span>'
                f'<span class="sr">{esc(it["src"])}</span>'
                f'<span>{esc(it["title"])}</span></div>')
    out.append('</div></div>')
    return "".join(out)


def market_block(d):
    m = d.get("market") or {}
    dims = m.get("dims") or []
    if not dims:
        return ""
    rows = "".join(
        f'<tr><td>{esc(x["name"])}</td><td class="n">{num(x["score"], 0)}</td>'
        f'<td class="muted">{esc(x["state"])}</td></tr>' for x in dims)
    return (
        f'<div class="card"><table><thead><tr><th>维度</th><th class="n">得分</th>'
        f'<th>状态</th></tr></thead><tbody>{rows}</tbody></table></div>')


def nav_block(d):
    rdir = os.path.join(ROOT, "reports")
    if not os.path.isdir(rdir):
        return ""
    files = sorted([f for f in os.listdir(rdir) if f.endswith(".html")], reverse=True)[:24]
    cur = d.get("_report_name")
    links = []
    for f in files:
        label = f.replace(".html", "")
        # 新命名 YYYY-MM-DD；旧命名 YYYY-MM-DD_HHMM 也兼容
        if "_" in label:
            dd, tt = label.split("_", 1)
            label = f"{dd[5:]} {tt[:2]}:{tt[2:4]}" if len(tt) >= 4 else f"{dd[5:]} {tt}"
        elif len(label) == 10:
            label = label[5:]
        on = ' class="on"' if f == cur else ""
        links.append(f'<a href="reports/{esc(f)}"{on}>{esc(label)}</a>')
    return '<div class="nav">' + "".join(links) + '</div>'


def render(d):
    us_date, cn_date = d["us_date"], d["cn_date"]
    target = d.get("target_cn_date")
    closed = target and target <= d["cn_date"]
    tag = ("预判对象日已收盘，下方验证表为实测" if closed
           else f"预判对象日 {esc(target)} 尚未收盘，待验证")
    ai_src = ""
    if d.get("attribution"):
        at = d["attribution"]
        ai_src = (f"AI 归因：<code>attrib.py</code>（{esc(at.get('provider', ''))}"
                  f"/{esc(at.get('model', ''))}）—— 解读文字由模型基于左侧事实生成，"
                  f"用于机制参考，可能出错<br>\n    ")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI产业链传导日报 · {esc(d['cn_date'])}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<header class="hd">
  <h1>AI 产业链 · 美股 → A股 传导日报</h1>
  <p class="sub">
    美股锚点日 <b>{esc(us_date)}</b>（隔夜） →  A股目标日 <b>{esc(target or cn_date)}</b>
    &nbsp;·&nbsp; 生成于 {esc(d['generated_at'])}（北京）
    &nbsp;·&nbsp; {esc(tag)}
  </p>
  {kpi_block(d)}
</header>

<div class="note">
  <b>怎么读：</b>「美股隔夜」是该环节美国锚点标的的加权涨跌幅；「β」是历史传导系数
  ——美股该环节涨 1%，A股同环节次日平均超额变动 β%；「R²」是这套关系的可信度；
  「预期超额」= β × 美股隔夜，指的是 <b>相对沪深300的超额</b>，非绝对涨跌。
  强度分四档：强（R²≥0.35）/ 中（≥0.15）/ 弱（≥0.05）/ 失效（&lt;0.05）。
</div>

<h2>AI 归因解读<span class="hint">为什么传导成这样 —— 机制层解释，非数据复述</span></h2>
{attribution_block(d)}

<h2>传导热力矩阵<span class="hint">8 大环节一览，红色为预期正向超额、绿色为负向</span></h2>
{matrix_block(d)}

<h2>逐环节明细<span class="hint">点击展开 · 含美股锚点、A股标的、逐股跟随意愿（ρ）与 AI 归因</span></h2>
{sector_detail(d)}

<h2>T+1 滚动验证</h2>
{verify_block(d)}

<h2>隔夜消息面<span class="hint">取自各环节波动最大的美股锚点</span></h2>
{news_block(d)}

<h2>A股市场情绪画像<span class="hint">用于判断传导能否落地的大盘环境</span></h2>
{market_block(d)}

<h2>方法论与口径</h2>
<div class="card" style="padding:14px 16px">
<p style="margin:0 0 10px"><b>传导系数</b>　对每个环节取最近 {d['meta']['reg_window']} 个配对交易日：</p>
<p style="margin:0 0 10px;font-family:var(--mono);font-size:12.5px;color:var(--ink2)">
x = 美股该环节当日加权涨跌（隔夜）<br>
y = A股该环节次日涨跌 − 沪深300次日涨跌（超额，剥离大盘）<br>
β = OLS(x, y) 斜率　·　R² = 可信度
</p>
<p style="margin:0 0 10px"><b>三项必要处理：</b></p>
<ul style="margin:0 0 10px;padding-left:20px;color:var(--ink2)">
<li><b>剔除未收盘占位行</b> —— 接口在当前市场未收盘时会返回上一交易日副本（OHLC 四值完全相同），不剔除会导致中美串日、β 全错。</li>
<li><b>交易日配对</b> —— 美股 D 日收盘（北京 D+1 凌晨）对应 A股 D 之后第一个交易日，自然吸收中美假期错位；长假后多个美股日累积到同一 A股日。</li>
<li><b>大盘剥离</b> —— y 先减沪深300，否则测出的是「大盘同步性」而非产业链传导。</li>
</ul>
<p style="margin:0;color:var(--ink2)">美股端 x 使用绝对涨跌（不剥离纳指），因为「美股 AI 板块整体涨跌」正是需要观察的信号本身。</p>
</div>

<h2>历史日报</h2>
{nav_block(d)}

<footer>
  <div class="warnbox">
    <b>免责声明</b>　本报告为 AI 产业链传导规律的统计观察，基于历史相关性，
    <b>不构成任何投资建议</b>，不含买卖推荐。历史传导关系随时可能失效，
    统计显著性不等于未来收益；日频相关性受样本量与市场状态影响较大。
    「AI 归因解读」为模型对量化快照的机制性解释，非事实陈述，同样不构成投资建议。
    据此操作，风险自负。
  </div>
  <p style="margin-top:14px">
    数据来源：westock CLI（腾讯自选股数据接口）· 行情涨跌幅与日线序列<br>
    映射定义：<code>chain-map.json</code>（8 环节 × 中美标的使用权重）·
    引擎：<code>engine.py</code>（滚动回归 + walk-forward 验证）<br>
    {ai_src}红涨绿跌（A股口径）· 每个交易日盘前更新
  </p>
</footer>

</div>
</body>
</html>"""


def main(data=None):
    if data is None:
        with open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8") as f:
            data = json.load(f)

    rdir = os.path.join(ROOT, "reports")
    os.makedirs(rdir, exist_ok=True)

    # 一个交易日一份报告：同日重跑直接覆盖，而不是越堆越多
    name = f'{data["cn_date"]}.html'
    data["_report_name"] = name
    html_doc = render(data)

    with open(os.path.join(rdir, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(html_doc)
    with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(html_doc)

    # 清理同一交易日的旧命名文件（YYYY-MM-DD_HHMM.html），避免一天多份
    stale = [f for f in os.listdir(rdir)
             if f.endswith(".html") and f != name and f.startswith(data["cn_date"] + "_")]
    for f in stale:
        try:
            os.remove(os.path.join(rdir, f))
        except OSError:
            pass

    print(f"rendered reports/{name} + index.html ({len(html_doc)/1024:.0f} KB)"
          + (f"，清理旧报告 {len(stale)} 份" if stale else ""))


if __name__ == "__main__":
    main()
