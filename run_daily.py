# -*- coding: utf-8 -*-
"""每日一键流程：交易日检查 → 采集计算 → AI 归因 → 渲染 → 提交发布。

供 automation 调用：
    python run_daily.py            # 常规（周末自动跳过）
    python run_daily.py --force    # 强制重跑（忽略周末与"无新数据"判断）
    python run_daily.py --no-ai    # 跳过 LLM 归因（快速验证用）

顺序设计：先判断"有没有新交易日数据"，再决定是否写盘/调 LLM/渲染。
这样重复执行是零副作用的——不会白烧一次 LLM 调用，也不会产生重复报告或空提交。
"""
import datetime
import json
import os
import subprocess
import sys

import attrib
import engine
import render

ROOT = os.path.dirname(os.path.abspath(__file__))
GIT = os.environ.get("GIT_BIN", r"C:\Program Files\Git\cmd\git.exe")
OWNER, NAME = "andyxu1234", "ai-chain-transmission"
CLEAN = f"https://github.com/{OWNER}/{NAME}.git"


def git(*args, feed=None):
    r = subprocess.run([GIT, "-C", ROOT, *args], capture_output=True,
                       input=feed.encode("utf-8") if feed else None)
    return (r.returncode,
            r.stdout.decode("utf-8", "replace").strip(),
            r.stderr.decode("utf-8", "replace").strip())


def main(force=False, use_ai=True):
    today = datetime.date.today()
    if not force and today.weekday() >= 5:
        print(f"SKIP 周末非交易日 ({today})")
        return 0

    prev = None
    p = os.path.join(ROOT, "data", "latest.json")
    if os.path.exists(p):
        try:
            prev = json.load(open(p, encoding="utf-8"))
        except Exception:
            prev = None

    data = engine.build()          # 采集 + 计算（不落盘）

    if prev and prev.get("cn_date") == data["cn_date"] and not force:
        print(f"SKIP 无新交易数据（cn_date={data['cn_date']} 未变化）")
        return 0

    if use_ai:
        attrib.enrich(data)        # 失败自动降级为 None，不阻断
    else:
        data["attribution"] = None

    engine.save(data)              # 落盘 data/snapshot-*.json + latest.json
    render.main(data)              # 渲染 index.html + reports/{cn_date}.html

    rc, out, err = git("add", "-A")
    if rc != 0:
        print("git add 失败:", err)
        return 1

    acc = "—" if data["accuracy"] is None else format(data["accuracy"], ".0%")
    msg = f"data: {data['cn_date']} 传导日报（美股锚点 {data['us_date']}，T+1 命中率 {acc}）"

    # 发布：本机 git push 走 HTTP 代理实测不稳定（schannel 断连 / CONNECT 502），
    # 而 api.github.com 更稳，因此默认走 Git Data API 发布（不产生本地临时提交）。
    # 设 USE_GIT_PUSH=1 可强制走 git commit + git push。
    if os.environ.get("USE_GIT_PUSH") == "1":
        rc, out, err = git("commit", "-F", "-", feed=msg + "\n")
        if rc != 0 and "nothing to commit" not in (out + err):
            print("git commit 失败:", (err or out)[:300])
            return 1
        tok = os.environ.get("GITHUB_TOKEN", "")
        url = (f"https://x-access-token:{tok}@github.com/{OWNER}/{NAME}.git" if tok
               else "origin")
        rc, out, err = git("push", url, "main:main")
        print("git push:", rc, (out or err)[:200])
        if rc != 0:
            return 1
    else:
        import publish
        try:
            res = publish.publish(msg)
        except Exception as e:
            print("API 发布失败:", str(e)[:400])
            return 1
        if res["up_to_date"]:
            print(f"SKIP 远端内容已是最新（{res['commit'][:7]}）")
        else:
            print(f"API 发布: 远端 {res['prev'][:7]} -> {res['commit'][:7]}"
                  f"（{res['files']} 文件 / {res['blobs']} blob，tz={res['tz']}）")

    ai = "有" if data.get("attribution") else "无"
    print(f"DONE cn_date={data['cn_date']} us_date={data['us_date']} "
          f"acc={acc} sectors={len(data['signals'])} ai归因={ai} "
          f"url=https://{OWNER}.github.io/{NAME}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv, use_ai="--no-ai" not in sys.argv))
