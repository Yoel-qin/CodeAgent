"""pipe 冒烟（M5 验收，Task 14）：增量更新 / 重放零副作用 / 死信落档 三场景。

与 smoke_graph 同骨架（sys.path 自举 + stdout reconfigure），但**不起 worker 子进程**
——in-process 调 :func:`run_worker_once`；真 PG + 真 Redis（compose 主栈）。

环境自建（脚本内直接改 settings，进程即弃、无需恢复）：

- 临时 git repo：``%TEMP%/smoke_pipe_xxx/pipe_tmp_smoke``，git init + v1/v2 两个 commit
  （v1 = tests/fixtures/mini_repo 的 5 个 .java + docs/指南.md；v2 = 改 CommitLog 方法体
  + 删 MessageConsumer + 加 BrandNew + 改 指南.md + 新增中文文件名 新配置.md——后者 e2e
  验证 Worker A 的 ``core.quotepath=false`` 修复）；
- ``settings.repos_root`` 指向该 tmp 根（push 事件走 **真 git diff 路径**，不喂 files 载荷）；
- Redis 流用独立 key ``v2:pipe:test:smoke:*``（组 ``v2:pipe:test:smoke:g``），用后 DEL；
- PG 侧行按 repo 名 ``pipe_tmp_smoke`` 精确清理（前缀落 ``pipe_tmp_``，与
  tests/test_pipe_runner 的清理谓词互为兜底）。

三场景门（任务书）：

1. 增量：``run_full_code_ingest`` 基线 → enqueue push(before, after) → drain 至队列空 →
   BrandNew 实体存在 / MessageConsumer 实体不存在 / CommitLog 含 putMessageV2 /
   documents 有 指南.md + 新配置.md 两行 / pipeline_events 全 DONE
   （graph_rebuild 行 path="__repo__"）。
2. 重放：同 push 再投一遍 → drain → 实体/文档指纹（(class_name, method_name, start_line)
   / (doc_name, file_hash) 全集）逐项一致 + skipped>=1 且 processed==0。
3. 死信：enqueue file 事件 path=不存在文件 status=M → run_worker_once ×3（一轮=一次
   尝试）→ depths()["dead"]>=1 且 pipeline_events 有 DEAD 行。

退出码：三场景门全过 → 0，否则 1。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")  # loguru 落 stderr——GBK 控制台安全
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.pipeline.ingest_code import run_full_code_ingest  # noqa: E402
from app.pipeline.queue import RedisStreamQueue  # noqa: E402
from app.pipeline.runner import run_worker_once  # noqa: E402

TAG = "smoke_pipe"
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mini_repo"
REPO = "pipe_tmp_smoke"  # pipe_tmp_ 前缀：test_pipe_runner 的清理谓词顺带兜底本脚本残留
STREAM = "v2:pipe:test:smoke:events"
DEAD_STREAM = "v2:pipe:test:smoke:dead"
GROUP = "v2:pipe:test:smoke:g"
CONSUMER = "smoke-w1"
DEAD_HASH = "deadbeefdeadbeefdeadbeefdeadbeef"  # 场景 3 死信事件的 commit_hash（账本定位）
DEAD_PATH = "no/such/Missing.java"

BRAND_NEW = """package com.example.broker;

/**
 * Mini fixture: brand-new class added in v2.
 */
public class BrandNew {

    public void go(String topic) {
        if (topic == null || topic.isEmpty()) {
            return;
        }
    }
}
"""

GUIDE_V1 = "# 集成指南\n\n## 消息发送\n\n使用 CommitLog.putMessage 写入消息，失败重试。\n"
GUIDE_V2 = (
    "# 集成指南（v2）\n\n## 消息发送\n\n使用 CommitLog.putMessageV2 写入消息，重试上限 16 次。\n\n"
    "## 新增配置\n\n发送重试相关键见 新配置.md。\n"
)
NEW_CONF = "# 新配置\n\n## 发送重试\n\nputMessageV2 的重试上限为 16，超限落 DLQ。\n"


class Gate:
    """场景断言收集器：逐条打印 PASS/FAIL，末尾给该场景 verdict。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fails: list[str] = []

    def check(self, label: str, cond: object, detail: str = "") -> bool:
        ok = bool(cond)
        line = f"[{TAG}]   {'PASS' if ok else 'FAIL'}  {label}"
        if detail:
            line += f"  ({detail})"
        print(line)
        if not ok:
            self.fails.append(label)
        return ok

    @property
    def ok(self) -> bool:
        return not self.fails


# ── 环境搭建 / 清理 ─────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True)
    return proc.stdout.decode("utf-8", errors="replace").strip()


def _build_repo(root: Path) -> tuple[Path, str]:
    """建仓 + commit v1（基线树：5 个 .java + docs/指南.md），返回 (repo 目录, v1 sha)。

    注意 v1 commit 后即返回——**基线 ingest 必须发生在 v2 变更之前**，否则 M/A/D
    处理形同虚设（基线已含 v2 内容，断言会平凡通过）。
    """
    repo = root / REPO
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("README.md"))
    docs = repo / "docs"
    docs.mkdir()
    (docs / "指南.md").write_text(GUIDE_V1, encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "smoke@v2.local")
    _git(repo, "config", "user.name", "smoke")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v1")
    return repo, _git(repo, "rev-parse", "HEAD")


def _commit_v2(repo: Path) -> str:
    """工作树变更到 v2 并 commit（改 CommitLog / 删 MessageConsumer / 加 BrandNew
    + 改 指南.md / 加中文文件名 新配置.md），返回 v2 sha。"""
    # 改：方法名替换（putMessage → putMessageV2）
    target = repo / "com" / "example" / "broker" / "CommitLog.java"
    target.write_text(
        target.read_text(encoding="utf-8").replace("putMessage", "putMessageV2"),
        encoding="utf-8",
    )
    # 删：MessageConsumer 整文件
    (repo / "com" / "example" / "client" / "MessageConsumer.java").unlink()
    # 增：新 java + 中文文件名新 md + 改既有 md
    (repo / "com" / "example" / "broker" / "BrandNew.java").write_text(BRAND_NEW, encoding="utf-8")
    (repo / "docs" / "指南.md").write_text(GUIDE_V2, encoding="utf-8")
    (repo / "docs" / "新配置.md").write_text(NEW_CONF, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "v2")
    return _git(repo, "rev-parse", "HEAD")


def _clean_redis() -> None:
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        r.delete(STREAM, DEAD_STREAM)
    finally:
        r.close()


def _clean_pg(engine) -> None:
    """按 repo 精确清 PG 侧行（先子后父：边→度量→实体→文档三表→账本）。"""
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM call_edges WHERE caller_id IN "
            "(SELECT id FROM code_entities WHERE repo = :r) "
            "OR callee_id IN (SELECT id FROM code_entities WHERE repo = :r)"), {"r": REPO})
        conn.execute(text(
            "DELETE FROM code_metrics WHERE entity_id IN "
            "(SELECT id FROM code_entities WHERE repo = :r)"), {"r": REPO})
        for table in ("code_entities", "doc_sections", "media_chunks", "documents",
                      "pipeline_events"):
            conn.execute(text(f"DELETE FROM {table} WHERE repo = :r"), {"r": REPO})


# ── 消费 / 指纹 ─────────────────────────────────────────────────────


def _drain(queue: RedisStreamQueue) -> dict:
    """循环 run_worker_once 至一轮零消费（队列空），返回累计 stats。"""
    total = {"processed": 0, "skipped": 0, "retried": 0, "dead": 0}
    for _ in range(10):
        stats = run_worker_once(queue, max_events=50)
        for k, v in stats.items():
            total[k] += v
        if not any(stats.values()):
            break
    return total


def _fingerprints(engine) -> tuple[set, set]:
    """(实体 (class_name, method_name, start_line) 全集, 文档 (doc_name, file_hash) 全集)。"""
    with Session(engine) as s:
        ents = set(s.execute(text(
            "SELECT class_name, method_name, start_line FROM code_entities WHERE repo = :r"),
            {"r": REPO}).all())
        docs = set(s.execute(text(
            "SELECT doc_name, file_hash FROM documents WHERE repo = :r"), {"r": REPO}).all())
    return ents, docs


# ── 三场景 ──────────────────────────────────────────────────────────


def _scene_incremental(engine, v1: str, v2: str) -> bool:
    g = Gate("场景1 增量更新（真 git diff 路径）")
    queue = RedisStreamQueue(stream=STREAM, dead=DEAD_STREAM, group=GROUP, consumer=CONSUMER)
    queue.enqueue("push", {"repo": REPO, "before": v1, "after": v2})
    stats = _drain(queue)
    print(f"[{TAG}]   worker stats: {stats}  depths: {queue.depths()}")

    g.check("无重试 / 无死信", stats["retried"] == 0 and stats["dead"] == 0)
    g.check("processed >= 6（5 file + graph_rebuild）", stats["processed"] >= 6,
            f"processed={stats['processed']}")

    with Session(engine) as s:
        rows = s.execute(text(
            "SELECT class_name, method_name FROM code_entities WHERE repo = :r"),
            {"r": REPO}).all()
        doc_names = {r[0] for r in s.execute(text(
            "SELECT doc_name FROM documents WHERE repo = :r"), {"r": REPO}).all()}
        ledger = s.execute(text(
            "SELECT event_kind, path, status FROM pipeline_events WHERE repo = :r"),
            {"r": REPO}).all()
    classes = {r[0] for r in rows}
    methods = {r[1] for r in rows}

    g.check("新增 BrandNew 实体存在", "BrandNew" in classes)
    g.check("被删 MessageConsumer 实体不存在", "MessageConsumer" not in classes)
    g.check("改文件含新方法 putMessageV2", "putMessageV2" in methods)
    g.check("旧方法 putMessage 已被删旧重建清除", "putMessage" not in methods)
    g.check("documents 有 docs/指南.md", "docs/指南.md" in doc_names)
    g.check("documents 有 docs/新配置.md（中文名 quotepath e2e）", "docs/新配置.md" in doc_names,
            f"docs={sorted(doc_names)}")
    g.check("pipeline_events 全 DONE 且非空", bool(ledger) and all(r[2] == "DONE" for r in ledger),
            f"rows={len(ledger)}")
    gr = [r for r in ledger if r[0] == "graph_rebuild"]
    g.check("graph_rebuild 账本行 path=__repo__", len(gr) == 1 and gr[0][1] == "__repo__")
    return g.ok


def _scene_replay(engine, v1: str, v2: str) -> bool:
    g = Gate("场景2 重放零副作用（同 push 再投一遍）")
    ents_before, docs_before = _fingerprints(engine)
    queue = RedisStreamQueue(stream=STREAM, dead=DEAD_STREAM, group=GROUP, consumer=CONSUMER)
    queue.enqueue("push", {"repo": REPO, "before": v1, "after": v2})
    stats = _drain(queue)
    print(f"[{TAG}]   worker stats: {stats}  depths: {queue.depths()}")
    ents_after, docs_after = _fingerprints(engine)

    g.check("processed == 0（无重复 ingest）", stats["processed"] == 0, f"stats={stats}")
    g.check("skipped >= 1（账本去重生效）", stats["skipped"] >= 1, f"skipped={stats['skipped']}")
    g.check("无死信", stats["dead"] == 0)
    g.check("实体指纹逐项一致", ents_before == ents_after,
            f"n={len(ents_before)} -> {len(ents_after)}")
    g.check("文档指纹逐项一致", docs_before == docs_after,
            f"n={len(docs_before)} -> {len(docs_after)}")
    return g.ok


def _scene_dead_letter(engine) -> bool:
    g = Gate("场景3 死信落档（不存在文件 ×3 次尝试）")
    queue = RedisStreamQueue(stream=STREAM, dead=DEAD_STREAM, group=GROUP, consumer=CONSUMER)
    queue.enqueue("file", {"repo": REPO, "commit_hash": DEAD_HASH,
                           "path": DEAD_PATH, "status": "M"})
    for i in range(3):
        stats = run_worker_once(queue, max_events=1)  # 一轮 = 一次尝试
        print(f"[{TAG}]   第 {i + 1}/3 轮: {stats}  depths: {queue.depths()}")

    depths = queue.depths()
    with Session(engine) as s:
        row = s.execute(text(
            "SELECT status FROM pipeline_events "
            "WHERE repo = :r AND commit_hash = :h AND path = :p"),
            {"r": REPO, "h": DEAD_HASH, "p": DEAD_PATH}).scalar_one_or_none()

    g.check("死信流 dead >= 1", depths["dead"] >= 1, f"depths={depths}")
    g.check("pipeline_events 有 DEAD 行", row == "DEAD", f"status={row!r}")
    g.check("主队列无 pending 残留（全 ack）", depths["pending"] == 0)
    return g.ok


# ── main ────────────────────────────────────────────────────────────


def main() -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="smoke_pipe_"))
    settings.repos_root = str(tmp_root)  # in-process 直改：worker/expand_push 调用时读
    engine = create_engine(settings.postgres_dsn_sync)
    print(f"[{TAG}] 临时 repo 根: {tmp_root}")
    print(f"[{TAG}] Redis 流: {STREAM} | 死信: {DEAD_STREAM} | 组: {GROUP}")
    _clean_redis()
    _clean_pg(engine)

    gates: list[tuple[str, bool]] = []
    try:
        repo, v1 = _build_repo(tmp_root)
        t0 = time.perf_counter()
        with Session(engine) as s:
            base = run_full_code_ingest(s, repo=REPO, repo_dir=repo)
            s.commit()
            base_classes = {r[0] for r in s.execute(text(
                "SELECT DISTINCT class_name FROM code_entities WHERE repo = :r"), {"r": REPO}).all()}
        print(f"[{TAG}] 基线全量 ingest: {base}  ({time.perf_counter() - t0:.1f}s)")
        print(f"[{TAG}] 基线类集: {sorted(base_classes)}")
        # 基线必须是 v1 树：MessageConsumer 在、BrandNew 不在——否则下面的 M/A/D
        # 断言会平凡通过（Task 14 自检：基线 ingest 先于 v2 变更）
        if "MessageConsumer" not in base_classes or "BrandNew" in base_classes:
            print(f"[{TAG}] FAIL: 基线不是 v1 树，M/A/D 验证无效（先建基线再做 v2 变更）")
            return 1

        v2 = _commit_v2(repo)
        print(f"[{TAG}] v1={v1[:12]} -> v2={v2[:12]}")

        scenes = (
            ("场景1/3 增量更新", lambda: _scene_incremental(engine, v1, v2)),
            ("场景2/3 重放零副作用", lambda: _scene_replay(engine, v1, v2)),
            ("场景3/3 死信落档", lambda: _scene_dead_letter(engine)),
        )
        for name, fn in scenes:
            print(f"\n[{TAG}] ========== {name} ==========")
            t = time.perf_counter()
            ok = fn()
            gates.append((name, ok))
            print(f"[{TAG}] {name}: {'PASS' if ok else 'FAIL'}  ({time.perf_counter() - t:.1f}s)")
    finally:
        _clean_redis()
        _clean_pg(engine)
        engine.dispose()
        if len(gates) == 3 and all(ok for _, ok in gates):
            shutil.rmtree(tmp_root, ignore_errors=True)
            print(f"[{TAG}] 清理完成：Redis 流已 DEL，PG repo={REPO} 行已清，tmp 已删")
        else:
            print(f"[{TAG}] 未全过：tmp 保留供排查 -> {tmp_root}")

    print(f"\n[{TAG}] ========== 汇总 ==========")
    for name, ok in gates:
        print(f"[{TAG}] {name}: {'PASS' if ok else 'FAIL'}")
    passed = len(gates) == 3 and all(ok for _, ok in gates)
    print(f"[{TAG}] {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
