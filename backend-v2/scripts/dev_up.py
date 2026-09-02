"""开发机一键拉起 v2 全部进程（当前：backend + code-mcp；后续计划追加）。

用法（backend-v2/ 下）：uv run python scripts/dev_up.py
Ctrl-C 退出并终止全部子进程。Windows GBK 控制台 → stdout 重配 UTF-8。
"""
import subprocess
import sys
from contextlib import suppress

sys.stdout.reconfigure(encoding="utf-8")

PROCS: list[tuple[str, list[str]]] = [
    ("backend", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8010"]),
    ("code-mcp", [sys.executable, "-m", "app.mcp_servers.code_server"]),
    ("doc-mcp", [sys.executable, "-m", "app.mcp_servers.doc_server"]),
    ("graph-mcp", [sys.executable, "-m", "app.mcp_servers.graph_server"]),
]


def main() -> None:
    children: list[subprocess.Popen] = []
    try:
        for name, cmd in PROCS:
            print(f"[dev_up] 启动 {name}: {' '.join(cmd)}")
            children.append(subprocess.Popen(cmd))
        print("[dev_up] 全部启动。Ctrl-C 退出。  health: http://localhost:8010/health  mcp: http://localhost:8110/mcp  doc-mcp: http://localhost:8111/mcp  graph-mcp: http://localhost:8112/mcp")
        for child in children:
            child.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            with suppress(Exception):
                child.terminate()


if __name__ == "__main__":
    main()
