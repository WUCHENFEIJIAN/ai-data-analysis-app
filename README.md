# AI 数据分析应用

一个面向业务人员与数据分析师的 AI 数据分析工作台。用户可以在独立项目空间中上传 CSV 或 Excel 数据，用自然语言描述分析目标，由 Agent 生成分析计划与 Python 脚本，在隔离的 Docker 沙箱中执行，并把指标、发现、图表和建议整理成可追溯、可离线查看的 HTML 报告。

系统强调“结论可验证”：报告中的指标、原子结论、证据和图表通过显式语义契约关联，服务端在发布前执行 Metric、Claim、Evidence、Semantic 与 Editorial 校验，减少口径错配、无证据结论和图表误导。

## 使用场景

- 销售与电商：销售趋势、品类贡献、渠道结构、客单价和转化表现分析。
- 运营分析：活动复盘、用户行为分层、异常波动定位和经营周报生成。
- 供应链与库存：库存结构、周转、缺货风险和服务水平分析。
- 通用表格分析：快速理解陌生 CSV/Excel、生成数据画像、指标与可视化报告。
- 分析原型验证：使用 Mock Provider 验证分析流程，或接入 OpenAI-compatible 模型完成真实推理。

## 主要功能

- 以 Project Workspace 隔离数据、消息、脚本、分析结果和报告。
- 上传 CSV、XLSX、XLS，自动生成字段、样本、缺失值和统计画像。
- 通过结构化 Agent Action 生成分析计划、脚本、Findings 与 Artifacts。
- 在无网络、限时、限 CPU/内存/PID、只读根文件系统的 Docker 沙箱中运行模型生成的 Python。
- 支持分析运行、停止、重试、继续执行以及 SSE 实时进度展示。
- 通过 Report Editor 管线装配、校验并渲染确定性 HTML 报告。
- 报告内嵌离线图表运行时和本地图片，下载后无需外网即可查看。
- 提供模型预设、OpenAI-compatible 模型配置与连接测试。
- 前后端包含单元测试、接口测试、浏览器渲染测试和视觉验收脚本。

## 快速开始

### 环境要求

- Python 3.12
- Node.js 22 或更高版本
- Docker Desktop / Docker Engine

### 1. 配置环境变量

```powershell
Copy-Item .env.example .env
```

如需真实模型分析，请在 `.env` 中填写：

```dotenv
LLM_API_BASE=https://your-openai-compatible-endpoint/v1
LLM_API_KEY=your-api-key
LLM_MODEL=your-model-name
```

没有配置模型时，健康检查、项目管理、文件上传和 Mock 测试仍可使用，但不能开始真实分析。

### 2. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
Set-Location frontend
npm ci
Set-Location ..
```

macOS/Linux 可将 Python 命令替换为 `./.venv/bin/python`。

### 3. 构建分析沙箱

```powershell
docker build -f docker/Dockerfile.analysis -t ai-analysis-sandbox:latest docker
```

### 4. 启动服务

推荐使用 Docker Compose：

```powershell
docker compose up --build
```

也可以分别启动：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

```powershell
Set-Location frontend
npm run dev
```

访问 <http://localhost:3000>，后端健康检查位于 <http://localhost:8000/api/health>。

> Windows 下请用单进程 Uvicorn 启动后端，不要添加 `--reload`。分析沙箱依赖异步子进程和 Docker daemon，reload 子进程可能导致运行不稳定。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Next.js 15、React 19、TypeScript、Tailwind CSS、Monaco Editor |
| 后端 | Python 3.12、FastAPI、Pydantic、SQLAlchemy、Uvicorn |
| 数据处理 | pandas、NumPy、openpyxl、xlrd |
| Agent / 模型 | OpenAI-compatible Chat Completions、结构化 Action、有限重试 |
| 执行隔离 | Docker、只读根文件系统、资源与超时限制 |
| 数据存储 | SQLite、基于文件系统的 Project Workspace |
| 报告 | 服务端确定性 HTML Renderer、离线图表运行时 |
| 测试 | pytest、Vitest、Testing Library、Playwright |
| 本地编排 | Docker Compose |

## 配置说明

常用环境变量见 `.env.example`：

- `DATABASE_URL`：SQLAlchemy 数据库连接，默认使用 SQLite。
- `WORKSPACE_ROOT`：项目工作区根目录。
- `SKILL_ROOT`：数据分析 Skill 目录。
- `FRONTEND_ORIGIN`：允许跨域访问后端的前端地址，多个地址用逗号分隔。
- `NEXT_PUBLIC_API_BASE_URL`：浏览器访问的 API 基础地址。
- `LLM_*`：模型端点、密钥、模型名、超时、重试和输出 token 上限。
- `PYTHON_*`：分析脚本的超时、内存和 CPU 限制。
- `SANDBOX_IMAGE`：分析沙箱镜像名。

不要提交 `.env`、数据库、Workspace、生成报告或日志；仓库仅保留 `.env.example` 作为配置模板。

## 测试与质量检查

后端：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m black --check .
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_sandbox_integration.py --ignore=tests/test_report_browser_rendering.py
```

前端：

```powershell
Set-Location frontend
npm test
npm run typecheck
npm run lint
npm run build
```

Docker 隔离和浏览器报告测试需要本机 Docker daemon / Chromium，详见测试文件中的前置条件。

## 场景故障排查

### 页面提示无法连接后端

1. 访问 `/api/health` 确认 FastAPI 正常运行。
2. 检查 `NEXT_PUBLIC_API_BASE_URL` 是否为浏览器可访问的地址，而不是容器内部主机名。
3. 跨域部署时，把前端完整 Origin 加入 `FRONTEND_ORIGIN`；不要包含路径。
4. 修改 `NEXT_PUBLIC_*` 后重新构建前端，它们会在构建时写入浏览器包。

### 无法开始分析或模型连接失败

1. 检查 `LLM_API_BASE`、`LLM_API_KEY` 和 `LLM_MODEL` 是否同时配置。
2. 确认模型服务兼容 `/chat/completions`，并能从后端主机访问。
3. 对默认开启深度推理、容易在 JSON 前耗尽输出预算的模型，可设置 `LLM_THINKING_ENABLED=false`。
4. 超时可适当提高 `LLM_TIMEOUT_SECONDS`，但不要超过配置允许范围。

### 分析脚本提示 Docker runtime unavailable

1. 确认 Docker Desktop / Docker Engine 已启动，当前用户可执行 `docker version`。
2. 确认已构建 `ai-analysis-sandbox:latest`，或让 `SANDBOX_IMAGE` 指向存在的镜像。
3. 使用 Compose 时确认 Docker socket 与工作区挂载符合宿主机权限要求。
4. Windows 下避免用 Uvicorn `--reload`。

### 上传失败

- 默认单文件上限为 50 MiB，可通过 `MAX_UPLOAD_BYTES` 调整。
- 确认格式为 CSV、XLSX 或 XLS，文件没有被其他程序独占锁定。
- 检查 `WORKSPACE_ROOT` 是否可写以及磁盘空间是否充足。

### 报告为空、图表缺失或重新生成后仍显示旧内容

- 查看分析进度消息和运行详情，确认 Findings 与 Artifacts 已成功生成。
- 检查 Workspace 的 `logs/`，超长 stdout/stderr 会落盘供诊断。
- 报告校验失败时先修复指标口径、Claim/Evidence 关联或图表字段引用，再重试生成。
- 浏览器仍显示旧报告时执行硬刷新；前端会通过文件 revision 刷新预览 iframe。

### SQLite 锁定或数据丢失

- 本地开发避免同时启动多个后端实例操作同一个 SQLite 文件。
- `data/` 和 `workspaces/` 是运行数据，应挂载到持久卷并定期备份。
- Cloudflare Containers 的本地磁盘是临时磁盘；完整云部署需要外部持久化方案，不能把生产数据只保存在容器文件系统。

## 安全边界

- Workspace 路径经过规范化校验，项目删除只允许合法的 `pj_` ID。
- FastAPI 主进程不直接执行模型生成的 Python，执行发生在受限 Docker 沙箱。
- 沙箱禁网、限制 CPU/内存/PID/超时，且不挂载 Docker socket、`.env` 或其他项目。
- Report Editor 只接收显式分析上下文；最终 HTML 经过服务端校验并使用受限 iframe 预览。
- CSV 样本、模型重试上下文和进程输出都有长度限制。

## 云部署说明

前端可以部署到 Cloudflare Workers；完整后端还需要可运行 FastAPI、Docker-in-Docker 和持久存储的容器平台。Cloudflare Containers 当前属于付费 Beta 功能，部署前需要开通相应套餐，并为 SQLite 与 Workspace 接入持久化方案。仅部署前端不会提供可用的 AI 分析能力。

## 当前限制

- 真实分析依赖用户提供的 OpenAI-compatible 模型服务。
- 当前数据层以单机 SQLite 和文件 Workspace 为主，不适合无改造的多实例水平扩容。
- 尚未提供协作权限、外部数据库、联网搜索、RAG、Notebook、在线 Python IDE、PPTX/Word/PDF 导出。
- Cloudflare Containers 部署需要付费账号与额外的持久化设计。
