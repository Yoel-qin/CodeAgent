# frontend 镜像（开发模式：vite dev server + HMR；生产构建另行加 stage）
FROM node:22-alpine

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH

RUN corepack enable && corepack prepare pnpm@11.11.0 --activate

WORKDIR /app

COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* .npmrc ./
RUN pnpm install --frozen-lockfile || pnpm install

COPY . /app

EXPOSE 5173
CMD ["pnpm", "run", "dev", "--host", "0.0.0.0"]
