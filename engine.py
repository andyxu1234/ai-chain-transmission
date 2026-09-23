# -*- coding: utf-8 -*-
"""传导引擎：采集行情 → 中美交易日配对 → 环节 β 滚动回归 → 当日传导信号。

核心方法
-------
  x = 美股某环节当日加权涨跌（隔夜）
  y = A股同环节次日涨跌 − 沪深300次日涨跌（超额收益，剥离大盘）
  β = OLS(x, y) 斜率      R² = 可信度

三个必要处理
-----------
1. **剔除未收盘占位行** —— 接口在当前市场未收盘时会返回上一交易日的副本
   （OHLC 四值完全相同）。不剔除会导致美股/ A股串日、β 全错。
2. **交易日配对** —— 美股 D 日收盘（北京 D+1 凌晨）→ 对应 A股 D 之后第一个交易日，
   用 bisect 自然吸收中美假期错位；长假后多个美股日会累积到同一 A股日。
3. **大盘剥离** —— y 先减沪深300同期涨跌，否则测出的是"大盘同步性"而非产业链传导。
"""
import bisect
import json
import os
import re
from datetime import datetime, timedelta, timezone

import ws

ROOT = os.path.dirname(os.path.abspath(__file__))
CN_TZ = timezone(timedelta(hours=8))
ET_TZ = timezone(timedelta(hours=-4))

PRIOR = {"strong": 0.85, "medium": 0.50, "weak": 0.20}
KLINE_LIMIT = 45
REG_WINDOW = 20
MIN_SAMPLES = 8


# ---------------------------------------------------------------- 基础工具

def load_chain_map():
    with open(os.path.join(ROOT, "chain-map.json"), encoding="utf-8") as f:
        return json.load(f)


def fnum(v, d=None):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except Exception:
        return d


def sanitize(rows):
    """升序排列 + 剔除未收盘占位行。"""
    recs = []
    for r in rows:
        if not r.get("date"):
            continue
        recs.append({
            "date": r["date"],
            "last": fnum(r.get("last")),
            "open": fnum(r.get("open")),
            "high": fnum(r.get("high")),
            "low": fnum(r.get("low")),
            "chg": fnum(r.get("change_pct")),
        })
    recs.sort(key=lambda x: x["date"])
    out = []
    for r in recs:
        if out:
            p = out[-1]
            if (r["last"] is not None and r["last"] == p["last"]
                    and r["open"] == p["open"]
                    and r["high"] == p["high"]
                    and r["low"] == p["low"]):
                continue          # 与上一日完全一致 → 未收盘占位，丢弃
        out.append(r)
    return out


def fetch_daily(codes, limit=KLINE_LIMIT):
    """批量拉日线 → {code: [rec...]}"""
    res = {}
    B = 8
    for i in range(0, len(codes), B):
        raw = ws.kline(codes[i:i + B], limit=limit)
        for c, rows in raw.items():
            res[c] = sanitize(rows)
    return res


def ols(xs, ys):
    """一元线性回归，返回 (beta, r2, n)。"""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    beta = sxy / sxx
    r2 = (sxy ** 2) / (sxx * syy)
    return beta, r2, n


def corr(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx ** 0.5 * syy ** 0.5)


def stdev(vals):
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


def weighted(items, idx, date):
    """环节加权涨跌（只用当日有数据的标的，权重再归一）。"""
    tot, wsum = 0.0, 0.0
    for it in items:
        rec = idx.get(it["code"], {}).get(date)
        if not rec or rec["chg"] is None:
            continue
        tot += it["w"] * rec["chg"]
        wsum += it["w"]
    return (tot / wsum) if wsum > 0 else None


def grade(r2, n, used_prior):
    if used_prior or n < MIN_SAMPLES:
        return "样本不足"
    if r2 >= 0.35:
        return "强"
    if r2 >= 0.15:
        return "中"
    if r2 >= 0.05:
        return "弱"
    return "失效"


# ---------------------------------------------------------------- 情绪画像

def parse_market_overview(txt):
    out = {"raw_score": None, "adj_score": None, "dims": [], "date": None}
    m = re.search(r"数据日期\s*`?(\d{4}-\d{2}-\d{2})", txt)
    if m:
        out["date"] = m.group(1)
    m = re.search(r"原始评分\s*\*\*([\d.]+)\*\*\s*/\s*调整评分\s*\*\*([\d.]+)\*\*", txt)
    if m:
        out["raw_score"] = fnum(m.group(1))
        out["adj_score"] = fnum(m.group(2))
    for line in txt.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("维度",) and not cells[0].startswith("-"):
            sc = fnum(cells[1])
            if sc is not None:
                out["dims"].append({"name": cells[0], "score": sc, "state": cells[2]})
    return out


# ---------------------------------------------------------------- 主流程

def build(asof=None):
    cm = load_chain_map()
    bl = cm["baseline"]
    sectors = cm["sectors"]

    us_codes, cn_codes = set(), set()
    for s in sectors:
        us_codes.update(i["code"] for i in s["us"])
        cn_codes.update(i["code"] for i in s["cn"])
    all_codes = sorted(us_codes | cn_codes | {bl["cn"], bl["cn_growth"]})

    daily = fetch_daily(all_codes)
    idx = {c: {r["date"]: r for r in rows} for c, rows in daily.items()}

    cn_cal = sorted(idx.get(bl["cn"], {}).keys())
    us_cal = sorted({d for c in us_codes for d in idx.get(c, {})})
    if not cn_cal or not us_cal:
        raise RuntimeError("交易日历为空，数据拉取失败")

    hs = {d: idx[bl["cn"]][d]["chg"] for d in idx.get(bl["cn"], {})
          if idx[bl["cn"]][d]["chg"] is not None}

    # 配对：美股 D → A股 D 之后第一个交易日
    pairs = {}
    for d in us_cal:
        i = bisect.bisect_right(cn_cal, d)
        if i < len(cn_cal):
            pairs.setdefault(cn_cal[i], []).append(d)

    last_us = us_cal[-1]
    last_cn = cn_cal[-1]
    target_cn = next((c for c, us in pairs.items() if last_us in us), None)

    signals = []
    for s in sectors:
        us_series = {d: v for d in us_cal if (v := weighted(s["us"], idx, d)) is not None}
        cn_raw = {d: v for d in cn_cal if (v := weighted(s["cn"], idx, d)) is not None}
        cn_ex = {d: cn_raw[d] - hs.get(d, 0.0) for d in cn_raw}

        samples = []
        for cn_date in sorted(pairs):
            if cn_date not in cn_ex:
                continue
            xs = [us_series[d] for d in pairs[cn_date] if d in us_series]
            if not xs:
                continue
            samples.append((cn_date, pairs[cn_date], sum(xs), cn_ex[cn_date]))

        recent = samples[-REG_WINDOW:]
        fit = ols([r[2] for r in recent], [r[3] for r in recent]) if len(recent) >= 3 else None

        x_now = us_series.get(last_us)
        used_prior = fit is None
        if fit:
            beta, r2, n = fit
        else:
            beta, r2, n = PRIOR.get(s["prior"], 0.4), None, len(recent)

        exp_ex = beta * x_now if x_now is not None else None
        st = grade(r2 if r2 is not None else 0.0, n, used_prior)

        disp = stdev([idx[i["code"]][last_us]["chg"] for i in s["us"]
                      if idx.get(i["code"], {}).get(last_us, {}).get("chg") is not None])

        # 逐股跟随意愿：个股超额收益 与 环节美股信号 的相关性
        followers = []
        for it in s["cn"]:
            rows = idx.get(it["code"], {})
            xs2, ys2 = [], []
            for cn_date, _ud, x, _y in recent:
                rec = rows.get(cn_date)
                if not rec or rec["chg"] is None:
                    continue
                xs2.append(x)
                ys2.append(rec["chg"] - hs.get(cn_date, 0.0))
            cur = idx.get(it["code"], {}).get(last_cn)
            followers.append({
                "code": it["code"], "name": it["name"], "w": it["w"],
                "corr": corr(xs2, ys2), "n": len(xs2),
                "last": cur["last"] if cur else None,
                "last_chg": cur["chg"] if cur else None,
                "last_date": cur["date"] if cur else None,
            })
        followers.sort(key=lambda f: -(abs(f["corr"]) if f["corr"] is not None else -1))

        signals.append({
            "id": s["id"], "name": s["name"], "logic": s["logic"], "prior": s["prior"],
            "us_ret": x_now,
            "beta": beta, "r2": r2, "n": n, "used_prior": used_prior,
            "expected_excess": exp_ex,
            "strength": st,
            "dispersion": disp,
            "us_detail": [{
                "code": i["code"], "name": i["name"], "w": i["w"],
                "chg": (idx.get(i["code"], {}).get(last_us) or {}).get("chg"),
                "last": (idx.get(i["code"], {}).get(last_us) or {}).get("last"),
            } for i in s["us"]],
            "cn_detail": [{
                "code": i["code"], "name": i["name"], "w": i["w"],
                "chg": (idx.get(i["code"], {}).get(last_cn) or {}).get("chg"),
                "last": (idx.get(i["code"], {}).get(last_cn) or {}).get("last"),
            } for i in s["cn"]],
            "followers": followers,
            "cn_ret_last": cn_raw.get(last_cn),
            "cn_excess_last": cn_ex.get(last_cn),
            "samples": [{"cn_date": a, "us_dates": b, "x": c, "y": d} for a, b, c, d in recent],
        })

    # 滚动验证（walk-forward）：只用 T-1 及之前的样本拟合 β，去预测 T 日，再看 T 日实际超额。
    # 这是唯一诚实的口径 —— 若用含 T 日的数据拟合再预测 T 日，命中率会虚高。
    by_id = {s["id"]: s for s in signals}
    verify = []
    for s in sectors:
        sig = by_id[s["id"]]
        hist = sig["samples"]
        if len(hist) < 4:
            continue
        train, last = hist[:-1], hist[-1]
        fit = ols([h["x"] for h in train], [h["y"] for h in train])
        if not fit:
            continue
        beta_h = fit[0]
        pred = beta_h * last["x"]
        act = last["y"]
        verify.append({
            "id": sig["id"], "name": sig["name"],
            "us_date": last["us_dates"][-1], "cn_date": last["cn_date"],
            "us_ret": last["x"], "beta": beta_h,
            "pred": pred, "actual": act,
            "hit": (pred > 0) == (act > 0),
        })

    acc = None
    if verify:
        acc = sum(1 for v in verify if v["hit"]) / len(verify)

    try:
        mo = parse_market_overview(ws.market_overview())
    except Exception:
        mo = {"raw_score": None, "adj_score": None, "dims": [], "date": None}

    # 美股大盘指数：CLI 的 kline 不支持美股指数，改用 quote 取展示值（不参与计算）
    try:
        q = ws.quote([bl["us"]]).get(bl["us"], {})
        ixic_disp = {"code": bl["us"], "name": bl["us_name"],
                     "chg": fnum(q.get("change_percent")), "last": fnum(q.get("price"))}
    except Exception:
        ixic_disp = {"code": bl["us"], "name": bl["us_name"], "chg": None, "last": None}

    # 隔夜消息面（每个环节取美股锚点里波动最大的一只）
    news = {}
    for s in sectors:
        sig = by_id[s["id"]]
        cand = [d for d in sig["us_detail"] if d["chg"] is not None]
        if not cand:
            continue
        top = max(cand, key=lambda d: abs(d["chg"]))
        try:
            items = ws.news(top["code"], limit=4)
            news[s["id"]] = [{
                "title": it.get("title", ""), "src": it.get("src", ""),
                "time": (it.get("time", "") or "")[:16], "symbol": top["code"],
                "symbol_name": top["name"],
            } for it in items]
        except Exception:
            news[s["id"]] = []

    signals.sort(key=lambda s: -(abs(s["expected_excess"]) if s["expected_excess"] is not None else -1))

    return {
        "generated_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M"),
        "cn_date": last_cn,
        "us_date": last_us,
        "target_cn_date": target_cn or last_cn,
        "baseline": {
            "hs300": {"code": bl["cn"], "name": bl["cn_name"],
                      "chg": idx.get(bl["cn"], {}).get(last_cn, {}).get("chg"),
                      "last": idx.get(bl["cn"], {}).get(last_cn, {}).get("last")},
            "gem": {"code": bl["cn_growth"], "name": bl["cn_growth_name"],
                    "chg": idx.get(bl["cn_growth"], {}).get(last_cn, {}).get("chg"),
                    "last": idx.get(bl["cn_growth"], {}).get(last_cn, {}).get("last")},
            "ixic": ixic_disp,
        },
        "signals": signals,
        "verify": verify,
        "accuracy": acc,
        "market": mo,
        "news": news,
        "meta": {"us_calendar": us_cal[-6:], "cn_calendar": cn_cal[-6:],
                 "reg_window": REG_WINDOW, "min_samples": MIN_SAMPLES},
    }


def save(data):
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    stamp = data["cn_date"].replace("-", "")
    for name in (f"snapshot-{stamp}.json", "latest.json"):
        with open(os.path.join(ROOT, "data", name), "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)


def main():
    data = build()
    save(data)
    print(f"OK cn_date={data['cn_date']} us_date={data['us_date']} "
          f"acc={data['accuracy']} sectors={len(data['signals'])}")
    return data


if __name__ == "__main__":
    main()
