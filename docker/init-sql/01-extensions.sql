-- CodeRAG PostgreSQL 初始化扩展（均随 postgres:16-alpine 自带）
-- GIN 模糊匹配 / JSONB 索引辅助
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- gen_random_uuid 等
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- 注意：重量级向量检索走 Milvus；PG 不安装 pgvector。
-- 若后续需要在 PG 内做轻量向量运算，再换用 ankane/pgvector 镜像。
