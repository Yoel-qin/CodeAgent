.PHONY: help up dev down restart logs ps migrate migrate-new backend-shell frontend-dev ingest test lint

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## 启动全部服务（基础设施 + 后端 + 前端）
	docker compose up -d

up-gpu: ## 启动全部服务 + 本地模型服务（GPU）
	docker compose --profile gpu up -d

dev: ## 仅启动基础设施（本地开发前后端）
	docker compose up -d postgres redis minio elasticsearch milvus etcd

down: ## 停止全部
	docker compose down

restart: ## 重启后端
	docker compose restart backend

logs: ## 查看后端日志
	docker compose logs -f backend

ps: ## 查看服务状态
	docker compose ps

migrate: ## 执行数据库迁移
	docker compose exec backend uv run alembic upgrade head

migrate-new: ## 新建迁移（用法: make migrate-new m=xxx）
	docker compose exec backend uv run alembic revision --autogenerate -m "$(m)"

backend-shell: ## 进入后端容器
	docker compose exec backend bash

ingest: ## 全量入库（示例仓库）
	docker compose exec backend uv run python scripts/ingest.py

test: ## 运行后端测试
	docker compose exec backend uv run pytest -q

lint: ## 代码检查
	docker compose exec backend uv run ruff check .
