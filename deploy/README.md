# 部署 Banyan（阶段一：本机 + Tailscale）

设计 §2、§12 B6。一个容器跑 API、任务 worker 和构建好的前端；数据库是同一 compose 里的 PostgreSQL + pgvector；
llm-steward 与 Ollama 在宿主机上（它们有自己的部署，见各自仓库）。手机经 Tailscale 用 HTTPS 在浏览器里访问（PWA 暂不做）。

## 1. 前提

| 组件 | 状态 | 检查 |
|---|---|---|
| Docker（Desktop / OrbStack） | compose v2.24+ | `docker compose version` |
| llm-steward | 运行中，监听宿主机 `127.0.0.1:8600` | `curl -s http://127.0.0.1:8600/health` |
| Ollama | steward 的抽取与 embedding 用；Banyan 的便宜 role 也可映射到它 | `curl -s http://127.0.0.1:11434/api/version` |
| Tailscale | 本机已登录，手机在同一 tailnet | `tailscale status` |

Ollama 模型（steward 用）：`ollama pull bge-m3:567m && ollama pull qwen3:8b`。本机 Ollama 不是登录项，用
`brew services run ollama` 启动（KB `global/machine/local-services.md`）。

## 2. steward 配置清单

1. 在 steward 建 namespace `banyan`：`deploy/steward/banyan.yaml` 用 `steward ns apply -f` 应用（文件头有命令）。
2. 两把 key（设计 §8.1）：
   - `BANYAN_STEWARD_API_KEY`：scope `read`、`write`（检索、提议条目、反馈、episode、记忆页）。
   - `BANYAN_STEWARD_REVIEW_KEY`：scope `review`，只用于建 collection 与失效条目。
3. 记忆抽取：steward 的 `STEWARD_MEMORY_PROVIDER` 默认 lite-mem，抽取模型走 Ollama `qwen3:8b`。

## 3. 配置 `backend/.env`

从 `backend/.env.example` 复制，至少填：

| 变量 | 说明 |
|---|---|
| `BANYAN_API_TOKEN` | `openssl rand -hex 24`；前端与 API 都用它 |
| `BANYAN_ROLES_FILE` | 容器内路径，例如 `/app/roles.yaml`（见下方挂载），模板 `backend/roles.example.yaml` |
| `BANYAN_STEWARD_URL` | 容器访问宿主机：`http://host.docker.internal:8600` |
| `BANYAN_STEWARD_API_KEY` / `BANYAN_STEWARD_REVIEW_KEY` | 第 2 步的两把 key |
| `BANYAN_SEARCH_PROVIDER` / `BANYAN_SEARCH_API_KEY` | `tavily` + key；不填则没有联网工具 |
| 模型 provider 的 key | roles 文件里 `api_key_env` 指向的变量，例如 `ANTHROPIC_API_KEY` |

仓库根目录的 `.env`（compose 读取）只放 `BANYAN_DB_PASSWORD`（数据库密码）。`BANYAN_DATABASE_URL` 由 compose 指向 `db`
服务，`.env` 里的值在容器里被覆盖。密钥只在这两个 `.env` 里，镜像里没有。

roles 文件含真实模型名，不进 git（`.gitignore`）；把它挂进容器：在 `docker-compose.override.yml` 里给 `banyan` 服务加
`volumes: ["./backend/roles.yaml:/app/roles.yaml:ro"]`。

**Linux 注意**：`host.docker.internal` 指向宿主机，但 steward 与 Ollama 只监听 `127.0.0.1` 时在 Linux 上连不到（Docker Desktop
/ OrbStack 上可以）。Linux 上二选一：
- 让 steward（与 Ollama）也监听 docker 网桥地址（如 `172.17.0.1`），`BANYAN_STEWARD_URL` 用 `http://host.docker.internal:8600`；
- 或给 `banyan` 服务 `network_mode: host`：容器与宿主机共用网络，URL 改为 `http://127.0.0.1:8600`，且 `ports` 不再生效
  （API 直接监听宿主机的 8800，`extra_hosts` 也用不上），数据库地址改为 `127.0.0.1:5472`。

## 4. 启动与升级

```bash
docker compose up -d --build        # 构建镜像（前端 + 后端），迁移数据库，启动 API 与 worker
curl -s http://127.0.0.1:8800/health | jq .status      # ok / degraded；down 表示数据库不可用
docker compose logs -f banyan
```

启动命令先 `banyan migrate`（Alembic 到 head）再 `banyan serve`。升级：`git pull && docker compose up -d --build`。
只要 API（不构建前端）：`BANYAN_BUILD_TARGET=backend docker compose up -d --build`。端口改用 `BANYAN_PORT`。

## 5. 手机访问（Tailscale）

用 Tailscale 给本机的 8800 端口发 HTTPS 证书并只对 tailnet 开放（API token 不走明文）：

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8800
tailscale serve status              # 显示 https://<机器名>.<tailnet>.ts.net
```

手机（已登录同一 tailnet）用浏览器打开该地址，首次进入填 API token。
关闭：`tailscale serve --https=443 off`。不要用 `tailscale funnel`（会公开到互联网）。

## 6. 备份与恢复

数据在 compose 卷 `banyan-db`（学习树、笔记、导出、任务）；知识与记忆在 steward 里，按 steward 的备份方式另行备份。

```bash
docker compose exec db pg_dump -U banyan -d banyan -Fc > banyan-$(date +%F).dump
docker compose exec -T db pg_restore -U banyan -d banyan --clean < banyan-YYYY-MM-DD.dump
```

## 7. 验收（设计 §12 B6）

手机浏览器经 Tailscale 完成一遍：建主题 → 大纲 → 讲解 → 支线提问（含快速提问）→ 反馈 → 笔记 → 导出 HTML。
