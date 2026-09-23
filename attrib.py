# -*- coding: utf-8 -*-
"""AI 归因解读：把 engine 产出的量化信号交给 LLM，生成"为什么"层面的解读。

分工
----
engine.py 负责**事实**（涨跌幅、β、R²、离散度、ρ、walk-forward 命中与否）；
attrib.py 只负责**解释**（为什么这个环节传导强/失效、昨日偏差说明什么、实盘要警惕什么）。

设计原则
--------
1. **绝不阻断主流程** —— 无 key / 网络失败 / 返回非法 JSON 时一律返回 None，
   日报照常生成，只是少一段解读。
2. **一次调用** —— 8 个环节打包成一次请求，而不是 8 次，省时省钱。
3. **只给事实，不给结论** —— prompt 里塞满已经算好的数字，禁止模型编造未提供的数据。
4. **合规** —— 明确要求不出现买卖建议。
"""
import json
import os
import re
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))

# (provider, key环境变量, base_url环境变量, 默认base_url, model环境变量, 默认model)
# 优先级：DeepSeek（中文好、便宜）> Qwen > OpenAI > SiliconFlow
PROVIDERS = [
    ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
     "https://api.deepseek.com", "DEEPSEEK_MODEL", "deepseek-chat"),
    ("qwen", "QWEN_API_KEY", "QWEN_BASE_URL",
     "https://dashscope.aliyuncs.com/compatible-mode/v1", "QWEN_MODEL", "qwen-plus"),
    ("openai", "OPENAI_API_KEY", "OPENAI_BASE_URL",
     "https://api.openai.com/v1", "OPENAI_CHAT_MODEL", "gpt-4o-mini"),
    ("siliconflow", "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL",
     "https://api.siliconflow.cn/v1", "SILICONFLOW_MODEL", "Qwen/Qwen2.5-72B-Instruct"),
]

# 额外 key 来源（KEY=VALUE 文本文件），按顺序读取；不覆盖已有环境变量
ENV_FILES = [
    os.path.join(ROOT, ".env"),
    os.environ.get("LLM_ENV_FILE", ""),
]

SYSTEM = """你是 AI 产业链跨市场传导研究员的解读助手。用户会给你一份已算好的量化快照，
你要做的是"解释为什么"，不是复述数字。

硬性要求：
1. 只使用用户提供的事实。不得引入未提供的数据、新闻或公司事件。
2. 不要罗列数字，读者已经看过表格了。要给出因果判断和实盘含义。
3. 不使用"建议买入/卖出/加仓/减仓"等表述，不做个股推荐，只做传导机制的研判。
4. 中文，克制、具体、信息密度高。禁止"综上所述""值得关注"这类空话。
5. 严格输出 JSON，不要 markdown 代码块，不要任何额外文字。"""


def _load_env_files():
    """把 .env 里的 key 灌进 os.environ（不覆盖已存在的）。"""
    for path in ENV_FILES:
        if not path or not os.path.exists(path):
            continue
        try:
            for line in open(path, encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            continue


def pick_provider():
    _load_env_files()
    for name, key_env, base_env, base_def, model_env, model_def in PROVIDERS:
        key = os.environ.get(key_env, "").strip()
        if key:
            return {"name": name, "key": key,
                    "base": os.environ.get(base_env, base_def).strip().rstrip("/"),
                    "model": os.environ.get(model_env, model_def).strip()}
    return None


def _chat(prov, system, user, timeout=120):
    body = {
        "model": prov["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        prov["base"] + "/chat/completions", method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + prov["key"],
                 "Content-Type": "application/json",
                 "User-Agent": "ai-chain-transmission"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        # 部分端点不支持 response_format，去掉重试一次
        if e.code in (400, 422) and "response_format" in detail:
            body.pop("response_format", None)
            req2 = urllib.request.Request(
                prov["base"] + "/chat/completions", method="POST",
                data=json.dumps(body).encode("utf-8"),
                headers={"Authorization": "Bearer " + prov["key"],
                         "Content-Type": "application/json",
                         "User-Agent": "ai-chain-transmission"})
            with urllib.request.urlopen(req2, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]
        raise RuntimeError(f"HTTP {e.code} {detail}")


def _parse(txt):
    """宽容地抽出 JSON 对象。"""
    if not txt:
        return None
    txt = txt.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    try:
        return json.loads(txt)
    except Exception:
        pass
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def build_payload(d):
    """把 snapshot 压成给模型的事实清单（去掉冗余字段，控制 token）。"""
    sig = []
    for s in d["signals"]:
        us_top = sorted([x for x in s["us_detail"] if x["chg"] is not None],
                        key=lambda x: -abs(x["chg"]))[:3]
        fol = [f for f in s["followers"] if f["corr"] is not None][:3]
        sig.append({
            "id": s["id"], "环节": s["name"], "先验": s["prior"],
            "传导逻辑": s["logic"],
            "美股隔夜加权涨跌%": s["us_ret"],
            "β": None if s["beta"] is None else round(s["beta"], 3),
            "R²": None if s["r2"] is None else round(s["r2"], 3),
            "样本数": s["n"], "传导强度": s["strength"],
            "预期超额%": None if s["expected_excess"] is None else round(s["expected_excess"], 3),
            "锚点离散度": None if s["dispersion"] is None else round(s["dispersion"], 2),
            "锚点个股": [{"名称": x["name"], "涨跌%": x["chg"]} for x in us_top],
            "A股跟随意愿top3": [{"名称": f["name"], "ρ": round(f["corr"], 2),
                                "最新涨跌%": f["last_chg"]} for f in fol],
            "相关新闻标题": [n["title"] for n in (d.get("news", {}).get(s["id"]) or [])][:3],
        })
    ver = [{"环节": v["name"], "预测超额%": round(v["pred"], 2),
            "实际超额%": round(v["actual"], 2), "是否命中": v["hit"]} for v in d.get("verify", [])]
    b = d["baseline"]
    return {
        "A股日": d["cn_date"], "美股锚点日": d["us_date"],
        "预判对象日": d.get("target_cn_date"),
        "大盘": {"沪深300涨跌%": b["hs300"]["chg"], "创业板指涨跌%": b["gem"]["chg"],
                 "纳斯达克涨跌%": b["ixic"]["chg"]},
        "昨日walk-forward验证": ver,
        "命中率": d.get("accuracy"),
        "A股情绪画像": (d.get("market") or {}).get("dims") or [],
        "环节": sig,
    }


USER_TMPL = """以下是 {cn_date}（美股锚点 {us_date}）的 AI 产业链传导量化快照。请输出 JSON：

{{
  "overview": "3~5 句。今日传导格局的总体判断：信号最强的环节是什么、其强度是否可信（R² 与样本数）；传导\"失效\"的环节暴露了什么结构性问题；大盘环境是否支持信号落地；昨日 walk-forward 的偏差说明 β 是否正在漂移。要有判断，不要复述数字。",
  "sectors": {{
    "<环节id>": "1~2 句。这个环节为什么传导成这样——把 R² 高低、锚点离散度、个股 ρ、相关新闻联系起来给出机制解释；若数据不足以归因就直说。"
  }},
  "pivot": "1~2 句。今日最值得盯的一个反直觉或高信息量的点（例如先验与实测背离、负 β、传导失效），说明它意味着什么。"
}}

必须为全部 8 个环节 id 各写一条 sectors 解读。JSON 里不要出现 markdown。

事实快照：
{payload}"""


def enrich(d, verbose=True):
    """给 snapshot 附加 attribution 字段。失败返回原 data（不解构主流程）。"""
    prov = pick_provider()
    if not prov:
        if verbose:
            print("attrib: 未找到可用的 LLM key，跳过 AI 归因")
        d["attribution"] = None
        return d

    payload = build_payload(d)
    user = USER_TMPL.format(cn_date=d["cn_date"], us_date=d["us_date"],
                            payload=json.dumps(payload, ensure_ascii=False, indent=1))
    try:
        raw = _chat(prov, SYSTEM, user)
        obj = _parse(raw)
        if not obj or not obj.get("overview"):
            raise RuntimeError("返回内容无法解析为预期 JSON")
        d["attribution"] = {
            "provider": prov["name"], "model": prov["model"],
            "overview": str(obj.get("overview", "")).strip(),
            "pivot": str(obj.get("pivot", "")).strip(),
            "sectors": {k: str(v).strip()
                        for k, v in (obj.get("sectors") or {}).items()},
        }
        if verbose:
            print(f"attrib: {prov['name']}/{prov['model']} 已生成 "
                  f"overview={len(d['attribution']['overview'])}字 "
                  f"sectors={len(d['attribution']['sectors'])}/8")
    except Exception as e:
        d["attribution"] = None
        if verbose:
            print(f"attrib: 生成失败（已降级，不影响日报）: {str(e)[:200]}")
    return d


def main():
    p = os.path.join(ROOT, "data", "latest.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    enrich(d)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    return d


if __name__ == "__main__":
    main()
