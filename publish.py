# -*- coding: utf-8 -*-
"""用 GitHub Git Data API 发布（等价于 git push，但不走 git 传输层）。

为什么需要它
-----------
本机 `git push` 走 HTTP 代理，实测频繁报
  `schannel: server closed abruptly` / `CONNECT tunnel failed, response 502`
而 `api.github.com` 更稳定。因此发布改走 REST API。

核心约定：**远端树逐字节等于本地树；本地分支指向远端产出的 sha**
--------------------------------------------------------------
GitHub 的建 commit 接口会保留你传入的时区偏移，但 `GET /git/commits/{sha}` 返回的
`author.date` 一律**归一化成 `...Z`**（UTC 形式）用于展示。这一点很容易误判成
「GitHub 把时间戳写成了 UTC」，实测并非如此 —— 穷举验证结果：

    tz=+0800 且 message 不带尾换行 → 复现远端 sha ✓
    tz=+0000 / -0000 / +0530      → 均不匹配 ✗

所以远端 commit 对象里存的就是 `+0800`，重建时必须按原偏移量拼装。
（同理，`message` 的尾换行也必须逐字节一致 —— 曾经因为 `rstrip("\n")` 造成
「sha 对不上」的假象，被误归因成时区问题。）

策略因此定为：

  1. 本地 `git add -A` → `git write-tree` 拿到权威 tree sha 与全部条目
  2. 从 **git 对象库**取该 tree 引用的全部 blob 上传（不是读工作区！）
  3. POST /git/trees（完整条目、**不带 base_tree**）→ 断言 sha == 本地 tree sha
  4. POST /git/commits（parents = 远端当前 HEAD）→ 拿到远端 sha R
  5. 在本地按 R 的元数据重建 commit 对象（此时 tree 一定存在，安全）
     → `update-ref refs/heads/main R` + `refs/remotes/origin/main R`

第 5 步让本地分支直接指向 R，本地与远端头部永远一致（无 ahead/behind），
且不依赖「让 GitHub 复现本地 sha」这一不可靠前提 —— 只要时区候选能命中就成立，
命中不了就报错退出、不动任何 ref。

三个踩过的坑，都已被结构性消除
-----------------------------
⚠ **不要用 base_tree**。以远端树为底建树时，本地删掉的文件在远端会被保留 ——
   删除永远传播不出去，远端树静默偏离本地树。

⚠ **blob 必须从对象库取**，不能读工作区。重放历史提交时工作区早已是更新的内容，
   读工作区会拿到错的字节，构造出与本地不同的树。

⚠ **tree 一致之前绝不 update-ref**。旧实现在 tree 不一致时仍重建远端 commit 对象，
   于是本地 HEAD 指向一个本地不存在的 tree，`git ls-tree HEAD` 直接报
   `fatal: not a tree object`，整条历史链跟着腐烂。
   现在所有断言都发生在改动任何 ref 之前 —— 远端要么完整更新，要么原地不动。
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
GIT = os.environ.get("GIT_BIN", r"C:\Program Files\Git\cmd\git.exe")
OWNER, NAME = "andyxu1234", "ai-chain-transmission"
BRANCH = "main"
SLUG = f"{OWNER}/{NAME}"

IDENT_RE = re.compile(r"^(author|committer) (.*) <(.*)> (\d+) ([+-]\d{4})$")
# 用于重建远端 commit 对象时试算时区。实测本仓存的是 +0800（提交者本地时区），
# 但 API 返回的 date 是归一化的 Z，无法直接读出真实偏移，故逐级试算兜底。
TZ_CANDIDATES = ["+0800", "+0000", "-0000", "+0900", "+0530", "+0100"]


# ---------------------------------------------------------------- 基础

def api(method, path, body=None, tok=""):
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + tok,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "ai-chain-transmission"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def g(*args, feed=None):
    r = subprocess.run([GIT, "-C", ROOT, *args], capture_output=True,
                       input=feed.encode("utf-8") if feed is not None else None)
    return (r.returncode,
            r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"))


def gb(*args):
    """取二进制输出（blob 内容不能用 utf-8 解码）。"""
    r = subprocess.run([GIT, "-C", ROOT, *args], capture_output=True)
    return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")


def blob_sha(raw):
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()


def iso_to_tz_offset(s):
    """'2026-09-23T11:55:58Z' / '...+08:00' → (utc_ts, 原始 offset 或 None)"""
    s = s.strip()
    m = re.match(r"^(.*?)(?:Z|([+-]\d{2}):?(\d{2}))$", s)
    base = m.group(1) if m else s
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    if not m:
        return ts, None
    if m.group(2) is None:
        return ts, "+0000"
    return ts, f"{m.group(2)}{m.group(3)}"


# ---------------------------------------------------------------- 本地状态

def index_tree():
    rc, out, err = g("write-tree")
    if rc != 0:
        raise RuntimeError(f"git write-tree 失败: {err.strip()[:200]}")
    return out.strip()


def tree_entries(tree_sha):
    rc, out, err = g("ls-tree", "-r", tree_sha)
    if rc != 0:
        raise RuntimeError(f"ls-tree {tree_sha[:7]} 失败: {err.strip()[:200]}")
    entries = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ != "blob":
            continue
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": sha})
    if not entries:
        raise RuntimeError("tree 为空，拒绝发布")
    return entries


def local_ident():
    """取 git 身份 + 当前时间，作为新 commit 的 author/committer。

    同时返回我们写入的时区偏移（如 `+0800`）—— 远端会原样保留它，
    因此重建 commit 对象时应当优先按这个偏移试算，而不是从 API 的 `...Z` 去猜。
    """
    def cfg(k, d):
        rc, out, _ = g("config", "--get", k)
        return out.strip() or d
    name = cfg("user.name", "andyXu1995")
    email = cfg("user.email", "andyXu1995@users.noreply.github.com")
    now = datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0)
    ident = {"name": name, "email": email, "date": now.isoformat()}
    off = f"{now.strftime('%z')[:3]}{now.strftime('%z')[3:]}"   # +0800
    return ident, ident, off


def remote_head(tok):
    code, ref = api("GET", f"/repos/{SLUG}/git/ref/heads/{BRANCH}", tok=tok)
    if code != 200:
        raise RuntimeError(f"读取远端 ref 失败：HTTP {code}")
    return ref["object"]["sha"]


def remote_tree_of(sha, tok):
    code, c = api("GET", f"/repos/{SLUG}/git/commits/{sha}", tok=tok)
    if code != 200:
        raise RuntimeError(f"读取远端 commit {sha[:7]} 失败：HTTP {code}")
    return c["tree"]["sha"]


# ---------------------------------------------------------------- 发布

def upload_blobs(entries, tok):
    """上传 tree 引用的全部 blob（内容取自 git 对象库，天然与 sha 一致）。"""
    done = 0
    for sha in {e["sha"] for e in entries}:
        rc, raw, err = gb("cat-file", "blob", sha)
        if rc != 0:
            raise RuntimeError(f"对象库缺少 blob {sha[:10]}: {err[:150]}")
        actual = blob_sha(raw)
        if actual != sha:
            raise RuntimeError(f"对象库 blob {sha[:10]} 校验失败（实际 {actual[:10]}）")
        code, blob = api("POST", f"/repos/{SLUG}/git/blobs",
                         {"content": base64.b64encode(raw).decode(),
                          "encoding": "base64"}, tok=tok)
        if code != 201 or blob["sha"] != sha:
            raise RuntimeError(f"上传 blob {sha[:10]} 失败：HTTP {code} {str(blob)[:150]}")
        done += 1
    return done


def align_local(remote_sha, tok, tz_hint=None):
    """在本地重建远端 commit 对象，并把本地分支指向它。

    安全性前提：该 commit 的 tree 在本地已存在 —— 由调用方断言。
    `message` 必须逐字节使用 API 返回值（含/不含尾换行都要照搬），
    否则拼出的对象 sha 会与远端不同。
    """
    code, c = api("GET", f"/repos/{SLUG}/git/commits/{remote_sha}", tok=tok)
    if code != 200:
        raise RuntimeError(f"读取远端 commit {remote_sha[:7]} 失败：HTTP {code}")
    tree = c["tree"]["sha"]
    parents = [p["sha"] for p in c.get("parents") or []]
    ts, _off = iso_to_tz_offset(c["author"]["date"])
    msg = c["message"]

    cands = list(TZ_CANDIDATES)
    if tz_hint and tz_hint not in cands:
        cands.insert(0, tz_hint)
    for tz in cands:
        head = f"tree {tree}\n"
        for p in parents:
            head += f"parent {p}\n"
        head += f'author {c["author"]["name"]} <{c["author"]["email"]}> {ts} {tz}\n'
        head += f'committer {c["committer"]["name"]} <{c["committer"]["email"]}> {ts} {tz}\n\n'
        rc, out, _ = g("hash-object", "-t", "commit", "-w", "--stdin", feed=head + msg)
        if rc == 0 and out.strip() == remote_sha:
            g("update-ref", f"refs/heads/{BRANCH}", remote_sha)
            g("update-ref", f"refs/remotes/origin/{BRANCH}", remote_sha)
            return tz
    raise RuntimeError("无法在本地复现远端 commit 对象（时区候选均不匹配），"
                       "本地 ref 未改动")


def publish(message, tok=None, root=False):
    """把当前 index 作为一次提交发布到远端，并让本地分支指向远端产出的 sha。

    root=True：新建**根提交**（无 parent）并强制快进远端 ref。
    仅在需要重建历史时使用 —— 例如远端历史里存在本地拿不到的 tree 对象
    （API 建树 + 旧 base_tree 发布留下的残留），此时继续挂在远端 HEAD 上
    会让本地历史永远带着一个缺失对象。
    """
    tok = tok or os.environ.get("GITHUB_TOKEN", "")
    if not tok:
        raise RuntimeError("GITHUB_TOKEN 缺失")

    tree_sha = index_tree()
    entries = tree_entries(tree_sha)
    rhead = remote_head(tok)

    if not root and remote_tree_of(rhead, tok) == tree_sha:
        g("update-ref", f"refs/remotes/origin/{BRANCH}", rhead)
        return {"up_to_date": True, "commit": rhead, "tree": tree_sha, "files": len(entries)}

    nblobs = upload_blobs(entries, tok)

    code, tree = api("POST", f"/repos/{SLUG}/git/trees", {"tree": entries}, tok=tok)
    if code != 201:
        raise RuntimeError(f"建树失败：HTTP {code} {str(tree)[:150]}")
    if tree["sha"] != tree_sha:
        raise RuntimeError(f"远端 tree {tree['sha'][:8]} != 本地 tree {tree_sha[:8]}"
                           f"，已中止，未改动远端 ref")

    author, committer, tz_hint = local_ident()
    parents = [] if root else [rhead]
    code, commit = api("POST", f"/repos/{SLUG}/git/commits",
                       {"message": message, "tree": tree["sha"],
                        "parents": parents, "author": author, "committer": committer},
                       tok=tok)
    if code != 201:
        raise RuntimeError(f"建 commit 失败：HTTP {code} {str(commit)[:150]}")

    api("PATCH", f"/repos/{SLUG}/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": bool(root)}, tok=tok)
    code, ref = api("GET", f"/repos/{SLUG}/git/ref/heads/{BRANCH}", tok=tok)
    if code != 200 or ref["object"]["sha"] != commit["sha"]:
        raise RuntimeError(f"远端 ref 未指向新 commit（实际 {ref.get('object', {}).get('sha')}）")

    # tree 已在本地（来自 write-tree），重建 commit 对象是安全的
    tz = align_local(commit["sha"], tok, tz_hint=tz_hint)
    return {"up_to_date": False, "commit": commit["sha"], "prev": rhead, "root": root,
            "tree": tree_sha, "files": len(entries), "blobs": nblobs, "tz": tz}


def main():
    tok = os.environ.get("GITHUB_TOKEN", "")
    rhead = remote_head(tok)
    tree_sha = index_tree()
    code, rc_ = api("GET", f"/repos/{SLUG}/git/commits/{rhead}", tok=tok)
    same = code == 200 and rc_["tree"]["sha"] == tree_sha
    print(f"远端 HEAD {rhead[:7]}  tree {rc_.get('tree', {}).get('sha', '?')[:7]}")
    print(f"本地 index tree {tree_sha[:7]}   {'内容一致，无需发布' if same else '有差异，可发布'}")
    if "--dry-run" in sys.argv:
        return 0
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    msg = args[0] if args else None
    if msg is None:
        rc, out, _ = g("log", "-1", "--pretty=%B")
        msg = out.strip()
    r = publish(msg, tok, root="--root" in sys.argv)
    if r["up_to_date"]:
        print("已是最新")
        return 0
    print(f"发布成功{'（根提交，已重建历史）' if r.get('root') else ''} "
          f"远端 {r['prev'][:7]} -> {r['commit'][:7]}"
          f"（{r['files']} 文件 / {r['blobs']} blob，tz={r['tz']}）")
    print("本地 refs 已指向该 commit，无 ahead/behind")
    return 0


if __name__ == "__main__":
    sys.exit(main())
