# epic-free

> 优雅地领取 Epic Games 每周免费游戏 / Gracefully claim weekly free games from the Epic Games Store.

`epic-free` 是 [`epic-freebies-helper`](https://github.com/Ronchy2000/epic-freebies-helper) 的重构版本：

- **多 LLM 提供方**：支持 `openai`（Chat Completions / Responses API）、`anthropic`（Messages API）、`gemini`（原生 GenAI SDK）三种 provider。GLM / 智谱不再需要单独的 provider — 它使用与 OpenAI 相同的 Chat Completions 格式，GLM 特殊行为（raw-base64 图片编码、thinking 模式）从模型名自动检测。
- **更好的性能**：所有 HTTP 调用（LLM 推理 + 周免游戏列表抓取）复用一个带连接池的 `httpx.AsyncClient`；`get_promotions()` 改为异步，不再阻塞事件循环。
- **干净的工程结构**：标准 `src/` 布局，正确的包内 import，移除冗余且配置已失效的 Celery，仅保留 APScheduler。

## 工作原理

1. 用 Playwright / Camoufox 启动浏览器，自动登录 Epic 账号（账号需**关闭 2FA**）。
2. 拉取本周免费游戏列表，跳过已入库的游戏。
3. 进入商品页点击领取，遇到 hCaptcha 时用大模型解题。
4. 完成结账，游戏进入你的 Epic 游戏库。
5. 通过 APScheduler 定时重跑（北京时间每周四 23:30→周五 03:30 每小时一次，每天 12:00 一次）。

## 快速开始（本地）

需要 Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/)。

```bash
# 1. 安装锁定依赖与经过验证的 Linux 浏览器内核
uv sync --frozen
uv run python scripts/download_browser.py --target ./volumes/camoufox-browser

# 2. 配置账号与 provider
cp .env.example .env
#   编辑 .env：填 EPIC_EMAIL / EPIC_PASSWORD，以及所选 provider 的 *_API_KEY

# 3. 跑一次（ENABLE_APSCHEDULER=false 时只执行一次）
CAMOUFOX_EXECUTABLE_PATH="$PWD/volumes/camoufox-browser/camoufox-bin" uv run epic-free
```

下载脚本支持 Linux x86_64 / arm64，会校验官方发布的 SHA-256。Docker 镜像已内置
Camoufox `0.5.8` 和验证过的内核 `158.0.1-beta.41`，不需要运行本地下载命令。
其他系统请安装对应平台的官方内核，并设置 `CAMOUFOX_EXECUTABLE_PATH`。

## LLM Provider

| `LLM_PROVIDER` | 需要填写 | 默认模型 | 说明 |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `gpt-4.1-mini` | OpenAI 或任意兼容网关；支持 Chat Completions（默认）和 Responses API 两种格式 |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` | Anthropic Messages API（Claude 模型） |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-pro` | 官方 Gemini 或 AiHubMix 中转（`GEMINI_BASE_URL`） |

> **GLM / 智谱**：使用 `openai` provider，将 `OPENAI_BASE_URL` 指向 GLM 端点、`OPENAI_MODEL` 设为 `glm-*` 模型即可。GLM 的特殊行为（raw-base64 图片编码、thinking 模式）从模型名自动检测，无需额外配置。

### OpenAI 两种 API 格式

`openai` provider 通过 `OPENAI_API_FORMAT` 选择线格式：

| 格式 | 值 | 端点 | 适用场景 |
|---|---|---|---|
| Chat Completions（默认） | `chat` | `/chat/completions` | 任何 OpenAI 兼容中转、GLM、Azure 等 |
| Responses API | `responses` | `/responses` | OpenAI 官方 API（Chat Completions 未来将废弃） |

```bash
OPENAI_API_FORMAT=chat       # 默认，兼容所有 OpenAI 兼容端点
OPENAI_API_FORMAT=responses  # 使用 OpenAI Responses API
```

`LLM_PROVIDER` 留空时会根据已填写的 key 自动判断（优先级 `openai → anthropic → gemini`）。四个验证码子模型（`CHALLENGE_CLASSIFIER_MODEL` 等）留空时自动跟随所选 provider 的默认模型。

## GitHub Actions（无需服务器）

没有服务器 / NAS，或租用的机器跑不动 Docker，可以让 GitHub 替你跑：每周四晚到周五凌晨在 GitHub 的免费 runner 上定时领取 3 次（北京时间 周四 23:20 / 周五 00:20 / 01:20），每次独立 runner、命中率叠加。工作流见 [`.github/workflows/claim.yml`](.github/workflows/claim.yml)。

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

**provider 三选一**（`LLM_PROVIDER` 填 `openai` / `anthropic` / `gemini`，并填对应那一组 key；其余 provider 的 Secret 不建即可，空值会被自动忽略）：

| `LLM_PROVIDER` | 需要的 Secret |
|---|---|
| `openai` | `OPENAI_API_KEY`（可选 `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_FORMAT`） |
| `anthropic` | `ANTHROPIC_API_KEY`（可选 `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`） |
| `gemini` | `GEMINI_API_KEY`（可选 `GEMINI_BASE_URL` / `GEMINI_MODEL`） |

### 3. 手动跑一次 / 等定时

- `Actions` → `Claim Epic Free Games (Scheduled)` → `Run workflow` 立刻手动触发；
- 或等每周自动运行：北京时间 周四 23:20 / 周五 00:20 / 01:20 共 3 次（`cron` 在 `claim.yml` 里，可改）。

> [!IMPORTANT]
> 受 Epic 风控影响单次运行可能 10–20 分钟并多次重试，**运行结束前不要手动取消**。

### 4. 看结果

日志在该次 run 页面实时可见；底部 `Artifacts` 会打包上传（保留 7 天，没产生文件的包不会出现）：

| Artifact | 内容 |
|---|---|
| `epic-logs-<run_id>` | 运行 / 错误日志 |
| `epic-runtime-<run_id>` | `promotions` 缓存、`purchase_debug` 截图与调试文本 |
| `epic-screenshots-<run_id>` | 登录 / 风控 / 授权阶段的截图 |

`Login success` 只代表初步登录响应；还应看到 `Epic store session verification success` 或 `Authentication completed`，确认商店会话可用。领取结果以 `All week-free games are already in the library` 或已确认的领取日志为准。若卡在账号验证或 `privacy-policy` 页，请先在自己的浏览器完成相应操作再重跑，不要仅凭初步登录日志判断领取成功。

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
| `openai` | `OPENAI_API_KEY`（可选 `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_FORMAT`） | `gpt-4.1-mini` |
| `anthropic` | `ANTHROPIC_API_KEY`（可选 `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`） | `claude-sonnet-5` |
| `gemini` | `GEMINI_API_KEY`（可选 `GEMINI_BASE_URL` / `GEMINI_MODEL`） | `gemini-2.5-pro` |

> **GLM / 智谱**：设 `LLM_PROVIDER=openai`，`OPENAI_BASE_URL` 指向 GLM 端点（如 `https://open.bigmodel.cn/api/paas/v4`），`OPENAI_MODEL` 设为 `glm-4.5v` 即可。GLM 特殊行为从模型名自动检测。

`docker-compose.yaml` 里已用注释列出了 Anthropic / Gemini 的对照配置，取消注释、填上对应 key 即可。`LLM_PROVIDER` 留空时会根据已填的 key 自动判断。

> 密码里如果含 `$`、`\`、`#`、`` ` `` 等特殊字符，请用单引号包裹整行：`EPIC_PASSWORD='ab$cd'`。

### 3. 拉取镜像并启动

```bash
docker compose up -d
```

首次启动会从 GHCR 拉取镜像（包含浏览器运行时，体积较大），随后容器以后台守护方式运行：立刻跑一次领取，并按计划定时重跑。镜像在构建时安装锁定依赖，启动时不会自动重新同步或升级依赖。

### 4. 查看运行状态与日志

```bash
docker compose ps                 # 查看容器状态
docker compose logs -f            # 实时跟踪日志（Ctrl+C 退出，不影响容器）
docker compose logs --tail=200    # 只看最近 200 行
```

以 `Authentication completed` 和 `Free games collection completed` 确认完整流程；`Login success` 只代表初步账号认证。`Authentication failed` 还可能来自浏览器兼容性、验证码或商店 SSO 交接，不能一律当成密码错误。

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

### 7. 登录故障排查

- 登录同时兼容邮箱、密码同页的新表单和先填邮箱再填密码的分步表单；提交按钮可以是 `#continue` 或 `#sign-in`。
- 邮箱页移到屏幕外的自动填充密码框不算真正的密码步骤；若邮箱提交就触发验证码，会先解题，再等待真实密码表单。
- 登录后的账号页若没有旧版确认弹窗，会直接继续验证商店会话，不再一直等待旧版 CSRF 通知。
- 账号中心登录后还会进入商店自己的 SSO 入口；若出现已保存账号选择页，只选择唯一匹配配置邮箱的账号，不会默认点击其他账号。
- 只对已经渲染的验证码挑战框解题；普通勾选框或挑战框切换期间不会被当成可操作的图片题，避免重复解题和空 iframe 错误。
- 若旧版本反复等待 `#sign-in` 超时，而失败截图里已经弹出验证码，应更新登录代码；这不等于大模型接口报错。
- `Solving login challenge attempt` 表示已进入解题流程；`Login challenge solver failed` 会记录解题异常。模型连接测试通过，不代表实际登录、验证码验证或领取已经成功。
- 即使 hCaptcha 日志显示成功，Epic 页面仍可能提示 `Incorrect response. Please refresh the page.`。程序会明确报告验证响应被拒绝并结束本轮重试；应先检查正常浏览器能否登录，再排查自动化环境，不能把解题成功当成登录成功。

对于支持相关字段的 Chat Completions 网关，可以设置 `OPENAI_REASONING_EFFORT=low`
和 `OPENAI_MAX_TOKENS=4096`，控制验证码请求的推理档位和输出上限；默认不发送这两个字段，
不支持时应保持为空 / `0`。这些设置仅影响本服务，不修改网关的全局路由。

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

- 新增 `LLM_PROVIDER=anthropic`（Anthropic Messages API，Claude 模型）。
- 新增 `OPENAI_API_FORMAT=responses`（OpenAI Responses API 支持）。
- `glm` 合并进 `openai` provider — GLM 使用与 OpenAI 相同的 Chat Completions 格式，特殊行为从模型名自动检测。
- 提取共享 LLM 客户端逻辑到 `llm/base.py`（`_BaseAsyncModels` 基类）。
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
