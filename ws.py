# -*- coding: utf-8 -*-
"""westock CLI 封装：子进程调用 + Markdown 表格解析。

统一 UTF-8 解码（PowerShell 直调会乱码），统一返回 dict 列表。
"""
import os
import subprocess

WS = os.environ.get("WESTOCK_BIN", r"C:\Users\20534\.local\bin\westock.exe")


class WSError(RuntimeError):
    pass


def call(*args, timeout=180):
    """执行 westock 子命令，返回 stdout 文本（UTF-8）。"""
    if not os.path.exists(WS):
        raise WSError(f"westock CLI not found: {WS}")
    r = subprocess.run([WS, *args], capture_output=True, timeout=timeout)
    out = r.stdout.decode("utf-8", errors="replace")
    err = r.stderr.decode("utf-8", errors="replace")
    if r.returncode != 0 and not out.strip():
        raise WSError(f"westock {' '.join(args)} -> rc={r.returncode}: {err.strip()[:300]}")
    return out


def parse_tables(txt):
    """解析 markdown 输出中所有表格，返回 list[list[dict]]。"""
    blocks, cur = [], []
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("|"):
            cur.append([c.strip() for c in s.strip("|").split("|")])
        else:
            if len(cur) >= 2:
                blocks.append(cur)
            cur = []
    if len(cur) >= 2:
        blocks.append(cur)

    tables = []
    for b in blocks:
        header = b[0]
        rows = []
        for r in b[1:]:
            if all(set(c) <= set("-: ") for c in r):   # 分隔行
                continue
            if len(r) == len(header):
                rows.append(dict(zip(header, r)))
        if rows:
            tables.append(rows)
    return tables


def quote(codes, date=None):
    """批量行情 → {code: row}。"""
    if not codes:
        return {}
    args = ["quote", ",".join(codes)]
    if date:
        args += ["--date", date]
    tabs = parse_tables(call(*args))
    return {r["code"]: r for r in (tabs[0] if tabs else []) if "code" in r}


def kline(codes, limit=45, period="day"):
    """批量日线 → {code: [row...]}（按日期升序）。"""
    if not codes:
        return {}
    tabs = parse_tables(call("kline", ",".join(codes), "--period", period, "--limit", str(limit)))
    rows = tabs[0] if tabs else []
    res = {}
    if rows and "code" in rows[0]:
        for r in rows:
            res.setdefault(r["code"], []).append(r)
    elif rows:
        res[codes[0]] = rows
    for v in res.values():
        v.sort(key=lambda r: r.get("date", ""))
    return res


def news(code, limit=6):
    """个股新闻 → list[dict]。"""
    tabs = parse_tables(call("news", "list", code, "--limit", str(limit)))
    return tabs[0] if tabs else []


def market_overview():
    """A股市场情绪画像（原始文本）。"""
    return call("market-overview")
