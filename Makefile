.PHONY: help up dev down ps migrate migrate-new test lint dev-up

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── 基础设施（Docker = infra only，应用进程跑主机本地） ──────────────

up: ## 启动全部基础设施（PG/Redis/MinIO/ES/Etcd/Milvus/Attu）
	docker compose up -d

dev: ## 仅启动核心基础设施（本地开发）
	docker compose up -d postgres redis minio elasticsearch milvus etcd

down: ## 停止全部基础设施
	docker compose down

ps: ## 查看服务状态
	docker compose ps

# ── backend-v2（v1 的 compose exec 目标已随 v1 退役删除） ────────────

migrate: ## 执行数据库迁移（backend-v2）
	cd backend-v2 && uv run alembic upgrade head

migrate-new: ## 新建迁移（用法: make migrate-new m=xxx）
	cd backend-v2 && uv run alembic revision --autogenerate -m "$(m)"

test: ## 运行后端测试（backend-v2）
	cd backend-v2 && uv run pytest -q

lint: ## 代码检查（backend-v2）
	cd backend-v2 && uv run ruff check .

dev-up: ## 一键拉起 backend-v2 + 四个 MCP server
	cd backend-v2 && uv run python scripts/dev_up.py
