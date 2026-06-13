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

## GitHub Actions（无需服务器）

没有服务器 / NAS，或租用的机器跑不动 Docker，可以让 GitHub 替你跑：默认每周四在 GitHub 的免费 runner 上定时领取一次。工作流见 [`.github/workflows/claim.yml`](.github/workflows/claim.yml)（参照上游 `epic-freebies-helper` 适配，并补上了 `openai` provider）。

> [!NOTE]
> GitHub runner 用的是机房公网 IP，Epic / hCaptcha 风控更严：验证码成功率比家用住宅 IP 低，单次运行可能 10–20 分钟并伴随多次重试——这是该模式的固有代价，不代表脚本失效。有常驻服务器的话仍建议用下面的 Docker 部署。

### 1. Fork 并启用工作流

- 把本仓库 Fork 到你自己的账号。
- 打开你 Fork 仓库的 `Actions` 页面，找到 **`Claim Epic Free Games (Scheduled)`**，点一次 `Enable workflow`（否则 GitHub 不会让 Fork 的定时任务自动生效）。

> 主仓库 `Autsunset/epic-free` 本身不会跑定时领取（工作流里有 `if` 守卫、也没配 Secrets），只有 Fork 才会运行。

### 2. 配置 Secrets

进入你 Fork 仓库的 `Settings` → `Secrets and variables` → `Actions`，添加：

**必填（账号，务必关闭 2FA）**

| Secret | 示例 |
|---|---|
| `EPIC_EMAIL` | your_epic@example.com |
| `EPIC_PASSWORD` | your_password |

**provider 三选一**（`LLM_PROVIDER` 填 `openai` / `glm` / `gemini`，并填对应那一组 key；其余 provider 的 Secret 不建即可，空值会被自动忽略）：

| `LLM_PROVIDER` | 需要的 Secret |
|---|---|
| `openai` | `OPENAI_API_KEY`（可选 `OPENAI_BASE_URL` / `OPENAI_MODEL`） |
| `glm` | `GLM_API_KEY`（可选 `GLM_BASE_URL` / `GLM_MODEL`） |
| `gemini` | `GEMINI_API_KEY`（可选 `GEMINI_BASE_URL` / `GEMINI_MODEL`） |

### 3. 手动跑一次 / 等定时

- `Actions` → `Claim Epic Free Games (Scheduled)` → `Run workflow` 立刻手动触发；
- 或等每周四北京时间 23:20 自动运行（`cron: '20 15 * * 4'`，可在 `claim.yml` 里改）。

> [!IMPORTANT]
> 受 Epic 风控影响单次运行可能 10–20 分钟并多次重试，**运行结束前不要手动取消**。

### 4. 看结果

日志在该次 run 页面实时可见；底部 `Artifacts` 会打包上传（保留 7 天，没产生文件的包不会出现）：

| Artifact | 内容 |
|---|---|
| `epic-logs-<run_id>` | 运行 / 错误日志 |
| `epic-runtime-<run_id>` | `promotions` 缓存、`purchase_debug` 截图与调试文本 |
| `epic-screenshots-<run_id>` | 登录 / 风控 / 授权阶段的截图 |

看到 `Login success`、`All week-free games are already in the library` 或 `🎉 ...` 即正常。常见前提：账号需**关闭 2FA**（卡在 `/id/login/mfa` 即 2FA 未关）；若卡在 `privacy-policy` 页，先在自己浏览器手动登录确认一次再重跑。

> [!TIP]
> 与 Docker 模式不同，Actions 的 runner 每次跑完即销毁，**不保留登录会话**（每次都重新登录）。这也是机房 IP 下登录验证码偶发失败更明显的原因。

## Docker 部署（推荐）

镜像由 `.github/workflows/docker.yml` 自动构建并发布到 GHCR：`ghcr.io/autsunset/epic-free:latest`。下面的步骤从克隆仓库开始，完整走一遍部署流程。

### 前置要求

- 已安装 **Docker**（≥ 20.10）与 **Docker Compose v2**（`docker compose` 子命令）。
- 一个**关闭了 2FA** 的 Epic 账号（邮箱 + 密码）。
- 一个 LLM provider 的 API Key（见下表）。

### 1. 克隆仓库

```bash
git clone https://github.com/Autsunset/epic-free.git
cd epic-free
```

### 2. 配置凭证

编辑仓库根目录的 `docker-compose.yaml`，修改 `environment` 段。至少要填 Epic 账号和你所选 provider 的密钥：

```yaml
    environment:
      - TZ=Asia/Shanghai

      # ---- Epic 账号（务必关闭 2FA）----
      - EPIC_EMAIL=your_email@example.com
      - EPIC_PASSWORD=your_password

      # ---- 任选一个 LLM provider（默认 openai）----
      - LLM_PROVIDER=openai
      - OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
      - OPENAI_BASE_URL=https://api.openai.com/v1
      - OPENAI_MODEL=gpt-4.1-mini

      # ---- 运行参数 ----
      - BROWSER_BACKEND=auto
      - ENABLE_APSCHEDULER=true   # 容器常驻时设 true，定时重跑
```

三种 provider 的切换方式：

| `LLM_PROVIDER` | 需要填的环境变量 | 默认模型 |
|---|---|---|
| `openai` | `OPENAI_API_KEY`（可选 `OPENAI_BASE_URL` / `OPENAI_MODEL`） | `gpt-4.1-mini` |
| `glm` | `GLM_API_KEY`（可选 `GLM_BASE_URL` / `GLM_MODEL`） | `glm-4.5v` |
| `gemini` | `GEMINI_API_KEY`（可选 `GEMINI_BASE_URL` / `GEMINI_MODEL`） | `gemini-2.5-pro` |

`docker-compose.yaml` 里已用注释列出了 GLM / Gemini 的对照配置，取消注释、填上对应 key 即可。`LLM_PROVIDER` 留空时会根据已填的 key 自动判断。

> 密码里如果含 `$`、`\`、`#`、`` ` `` 等特殊字符，请用单引号包裹整行：`EPIC_PASSWORD='ab$cd'`。

### 3. 拉取镜像并启动

```bash
docker compose up -d
```

首次启动会从 GHCR 拉取镜像（压缩后约 1 GB，含 Playwright + Camoufox 运行时；解压后磁盘占用更大），随后容器以后台守护方式运行：立刻跑一次领取，并按计划定时重跑。

### 4. 查看运行状态与日志

```bash
docker compose ps                 # 查看容器状态
docker compose logs -f            # 实时跟踪日志（Ctrl+C 退出，不影响容器）
docker compose logs --tail=200    # 只看最近 200 行
```

看到 `Login success`、`Free games collection completed`、`🎉 ...` 即代表正常；`Authentication failed` 通常是账号密码错误或触发了 2FA。

### 5. 停止 / 重启 / 更新到最新版

```bash
docker compose down                      # 停止并移除容器
docker compose restart                   # 重启容器
docker compose pull && docker compose up -d   # 拉取最新镜像并重新部署
```

### 6. 数据持久化

`docker-compose.yaml` 把宿主机的 `./volumes/` 挂载到容器内 `/app/volumes/`，其中保存：

- `user_data/` —— 浏览器 profile 与 Epic 登录会话（保留后下次可免登录）。
- `logs/` —— 运行日志 / 错误日志。
- `runtime/` —— 截图、promotions 缓存、领取过程调试快照。
- `screenshots/` `record/` `hcaptcha/` —— 验证码相关的截图、录像与缓存。

删除 `./volumes/` 会清空会话，下次需要重新登录。

> 录像、调试截图与 hCaptcha 缓存会随运行累积。容器默认每次运行会清理超过 30 天的旧文件,可用 `RECORD_RETENTION_DAYS`（录像）和 `RUNTIME_RETENTION_DAYS`（调试截图 + 缓存）调整保留天数,设 `0` 表示永久保留。

---

### 本地构建镜像（可选）

如果你 fork 了本仓库、GHCR 拉不到镜像、或想自行修改后构建，可以直接本地构建：

```bash
# 方式 A：用 docker compose 本地构建并启动
docker compose up -d --build

# 方式 B：单独构建镜像
docker build -t epic-free:local .
docker run -d --name epic-free \
  --shm-size=2g \
  -e EPIC_EMAIL=your_email@example.com \
  -e EPIC_PASSWORD=your_password \
  -e LLM_PROVIDER=openai \
  -e OPENAI_API_KEY=sk-xxxx \
  -e ENABLE_APSCHEDULER=true \
  -v "$PWD/volumes:/app/volumes" \
  epic-free:local
```

> Fork 本仓库后，需要把 `docker-compose.yaml` 里的 `image: ghcr.io/autsunset/epic-free:latest` 改成你自己的 GHCR 路径（`ghcr.io/<你的用户名小写>/epic-free:latest`），并 push 一次到默认分支触发你自己的镜像构建。

## 相比原项目的改动一览

- 新增 `LLM_PROVIDER=openai`，与 `glm` 共享 `epic_free.llm.openai_compat.OpenAICompatibleClient`。
- 共享 `httpx.AsyncClient`（连接池），取代每次推理新建连接。
- `get_promotions()` 异步化。
- 移除 Celery（其 task 模块路径在原项目中已失效），统一用 APScheduler。
- `src/epic_free/` 包结构，消除 `sys.path` hack。

## 致谢

本项目站在前人的肩膀上，向上游作者致以诚挚谢意：

- **[QIN2DIM/epic-awesome-gamer](https://github.com/QIN2DIM/epic-awesome-gamer)** —— 本项目所归属的整个技术谱系的起点。Epic 免费游戏自动化领取的整体流程、hCaptcha 的 AI 解题思路，以及本项目依赖的 [`hcaptcha-challenger`](https://github.com/QIN2DIM/hcaptcha-challenger) 库，均源自 QIN2DIM 的工作。`epic_free/epic/auth.py` 与 `epic_free/epic/store.py` 中的浏览器自动化逻辑可追溯到该项目。
- **[Ronchy2000/epic-freebies-helper](https://github.com/Ronchy2000/epic-freebies-helper)** —— 本重构项目的直接前身。多 LLM provider（Gemini / GLM）支持、领取流程的工程化加固（设备弹窗处理、结账确认、GLM 响应归一化等），以及 Docker / GitHub Actions 的部署方案，均建立在它的基础之上。`epic-free` 即为它的重构与整合。

如无特别说明，本项目代码遵循其来源项目的许可证（GPL-3.0-or-later）。

## 许可证

本项目基于 [GPL-3.0-or-later](./LICENSE) 协议开源。因为本项目是上述 GPL-3.0 项目的衍生作品，按协议要求同样以 GPL-3.0-or-later 发布。

```
epic-free — refactor of epic-freebies-helper
Copyright (C) 2026 Autsunset

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
```

完整协议文本见仓库根目录的 [LICENSE](./LICENSE) 文件。
