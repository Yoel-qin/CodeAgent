"""开发机一键拉起 v2 全部进程（backend + code/doc/graph/common 四个 MCP server）。

用法（backend-v2/ 下）：uv run python scripts/dev_up.py [--with-frontend]
  --with-frontend  追加拉起 frontend-v2（pnpm dev，:5300，代理 /api→:8010；需先 pnpm install）
Ctrl-C 退出并终止全部子进程。Windows GBK 控制台 → stdout 重配 UTF-8。
"""
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PROCS: list[tuple[str, list[str]]] = [
    ("backend", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8010"]),
    ("code-mcp", [sys.executable, "-m", "app.mcp_servers.code_server"]),
    ("doc-mcp", [sys.executable, "-m", "app.mcp_servers.doc_server"]),
    ("graph-mcp", [sys.executable, "-m", "app.mcp_servers.graph_server"]),
    ("common-mcp", [sys.executable, "-m", "app.mcp_servers.common_server"]),
]


def main() -> None:
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        return
    if "--with-frontend" in args:
        PROCS.append(("frontend", ["pnpm", "dev"]))
    children: list[subprocess.Popen] = []
    try:
        for name, cmd in PROCS:
            print(f"[dev_up] 启动 {name}: {' '.join(cmd)}")
            kwargs: dict = {}
            if name == "frontend":
                kwargs["cwd"] = str(Path(__file__).resolve().parents[2] / "frontend-v2")
                kwargs["shell"] = sys.platform == "win32"  # win32 需 shell 解析 pnpm.cmd
            children.append(subprocess.Popen(cmd, **kwargs))
        print("[dev_up] 全部启动。Ctrl-C 退出。  health: http://localhost:8010/health  mcp: http://localhost:8110/mcp  doc-mcp: http://localhost:8111/mcp  graph-mcp: http://localhost:8112/mcp  common-mcp: http://localhost:8113/mcp")
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
