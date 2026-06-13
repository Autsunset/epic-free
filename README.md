# epic-free

> 优雅地领取 Epic Games 每周免费游戏 / Gracefully claim weekly free games from the Epic Games Store.

`epic-free` 是 [`epic-freebies-helper`](https://github.com/Ronchy2000/epic-freebies-helper) 的重构版本：

- **统一的 LLM 提供方**：把原先散落在独立分支的 OpenAI 兼容能力合并进主线，`gemini` / `glm` / `openai` 三种 provider 共用一套配置，`glm` 与 `openai` 共享同一个 OpenAI 兼容客户端。
- **更好的性能**：所有 HTTP 调用（LLM 推理 + 周免游戏列表抓取）复用一个带连接池的 `httpx.AsyncClient`；`get_promotions()` 改为异步，不再阻塞事件循环。
- **干净的工程结构**：标准 `src/` 布局，正确的包内 import，移除冗余且配置已失效的 Celery，仅保留 APScheduler。

## 工作原理

1. 用 Playwright / Camoufox 启动浏览器，自动登录 Epic 账号（账号需**关闭 2FA**）。
2. 拉取本周免费游戏列表，跳过已入库的游戏。
3. 进入商品页点击领取，遇到 hCaptcha 时用大模型（Gemini / GLM / OpenAI）解题。
4. 完成结账，游戏进入你的 Epic 游戏库。
5. 通过 APScheduler 定时重跑（北京时间每周四 23:30→周五 03:30 每小时一次，每天 12:00 一次）。

## 快速开始（本地）

需要 Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/)。

```bash
# 1. 安装依赖（会以可编辑模式安装本包，并拉取 Camoufox 浏览器）
uv sync
uv run camoufox fetch

# 2. 配置账号与 provider
cp .env.example .env
#   编辑 .env：填 EPIC_EMAIL / EPIC_PASSWORD，以及所选 provider 的 *_API_KEY

# 3. 跑一次（ENABLE_APSCHEDULER=false 时只执行一次）
uv run epic-free
```

## LLM Provider

| `LLM_PROVIDER` | 需要填写 | 默认模型 | 说明 |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `gpt-4.1-mini` | OpenAI 或任意兼容网关（需支持 `image_url`） |
| `glm` | `GLM_API_KEY` | `glm-4.5v` | 智谱 GLM，OpenAI 兼容接口 |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-pro` | 官方 Gemini 或 AiHubMix 中转（`GEMINI_BASE_URL`） |

`LLM_PROVIDER` 留空时会根据已填写的 key 自动判断。四个验证码子模型（`CHALLENGE_CLASSIFIER_MODEL` 等）留空时自动跟随所选 provider 的默认模型。

## Docker

镜像由 `.github/workflows/docker.yml` 自动构建并推送到 GHCR：

```bash
docker compose up -d
```

部署前请编辑 `docker-compose.yaml`，填入 Epic 账号、所选 provider 的密钥，并把 `${OWNER}` 改成你的 GitHub 用户名（小写）。首次推送到默认分支后镜像才会生成。

## 相比原项目的改动一览

- 新增 `LLM_PROVIDER=openai`，与 `glm` 共享 `epic_free.llm.openai_compat.OpenAICompatibleClient`。
- 共享 `httpx.AsyncClient`（连接池），取代每次推理新建连接。
- `get_promotions()` 异步化。
- 移除 Celery（其 task 模块路径在原项目中已失效），统一用 APScheduler。
- `src/epic_free/` 包结构，消除 `sys.path` hack。

## 许可证

GPL-3.0-or-later
