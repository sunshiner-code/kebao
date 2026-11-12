# 科技保险智能体业务平台（techins）

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-16-black.svg)](https://nextjs.org/)

面向科技企业的保险业务平台：六个专业智能体覆盖科技保险从产品设计、产品发布、承保、在保风险监测、事故响应到定损理赔的全生命周期。

- **产品设计智能体**：风险评估（AG-RA）、条款设计（AG-CD）、精算定价（AG-AP）。默认从产品开发背景和客户需求出发，路径是「必要追问 → 圈定险种和待保障风险 → 产品规则与条款 → 两种预配置费率」。
- **产品服务智能体**：承保风控（AG-UW）、风险预警（AG-RW）、定损理赔（AG-CL），由「投保 → 核保出单 → 监测方案 → 预警研判 → 事故响应 → 预报案 → 定损理赔 → 结案回流」的业务事件驱动。

> **开源说明**：本仓库只包含可运行的源代码、测试与演示数据生成器。真实企业数据、运行数据库、生产环境配置、内部需求与设计文档均不在本仓库中。仓库内的险种、条款、费率与演示企业均为示例内容，不构成任何真实的保险产品、法律或精算意见。

## 功能特性

- **智能体运行框架（Harness）**：主循环、待办、技能两层加载、工具注册与守卫、子智能体、后台任务、信箱协议、人审节点、上下文压缩、评估器、反思器、记忆与经验自进化、决策策略。
- **模型路由与隐私计算**：按「推理任务类型 × 数据级别」路由；云端模型只接收脱敏数据，私有材料与图像走私有化多模态模型；出域二次扫描与审计；任一模型不可用时沿兜底链回退，最终由内置推理引擎兜底。
- **知识库与冷热启动**：险种目录、风险分类体系、因子字典、关键风险指标库、预案与服务商目录、条款模块库、混合检索、险种成熟度评分。
- **精算与分析算法**：广义线性模型、梯度提升树、信度模型、蒙特卡洛、A/B 测试、五路反欺诈融合、感知哈希与损伤识别、KRI 与时序异常检测、变点检测、健康指数、因果推断。
- **完整业务闭环**：产品发布与版本、承保出单、批改续保、监测调度、客户授权、配置中心、多级审批、评测与自进化、经营/风险/效能看板、定时任务、审计。
- **角色与权限**：产品设计、产品服务、系统管理三类角色，支持组织、岗位、数据范围与多级审批。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12、FastAPI、SQLAlchemy、Pydantic、SSE |
| 存储 | SQLite（默认，零配置）或 MySQL 8.4 |
| 前端 | Next.js 16、React 19、Tailwind CSS 4、shadcn/ui、SWR、Recharts |
| 模型 | 任意 OpenAI 兼容接口（云端 + 私有化部署），可完全离线使用内置推理引擎 |
| 工具链 | uv / pip、pnpm、pytest |

## 目录结构

```
src/techins/
  harness/        智能体运行框架：主循环、待办、技能加载、工具注册与守卫、子智能体、后台任务、
                  信箱协议、人审节点、上下文压缩、评估器、反思器、记忆与经验自进化、决策策略
  llm/            推理任务注册、模型注册与路由规则、模型网关（任务类型 × 数据级别路由、兜底链）
  privacy/        数据分级识别、脱敏与回填（占位符映射库）、出域二次扫描与审计
  knowledge/      险种目录、风险分类体系、因子字典、关键风险指标库、预案与服务商目录、混合检索、成熟度评分
  analytics/      定价、反欺诈、图像分析、风险检测、因果推断
  agents/         六个智能体的配置包（工具、推理任务、评估量表、反思策略、人审节点）
  skills/         技能文件 SK-01…SK-06 与共享技能 SK-S1…SK-S3
  services/       设计流水线、产品发布与版本、承保出单、批改续保、监测调度、客户授权、
                  配置中心、审批流、评测与自进化、看板、定时任务
  seed/           演示数据生成器（企业、私有材料、经验数据、演示投保与理赔材料、仿真图像）
  api/            FastAPI 接口（认证、运行与事件流、人审、产物、业务资源、运维管理）
web/              Next.js + Tailwind + shadcn/ui 前端
scripts/          冒烟测试、演示数据引导与迁移脚本
tests/            pytest 测试
```

运行数据（SQLite 数据库、上传材料、运行工作区、隐私映射库）在首次启动时自动创建到 `data/`，该目录已被 `.gitignore` 忽略。

## 快速开始

### 环境要求

- Python 3.12+
- Node.js 22+ 与 pnpm 10+
- （可选）MySQL 8.4，或直接使用默认 SQLite

### 1. 安装后端依赖

推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --dev
```

也可以使用 venv + pip：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e .
```

### 2. 配置模型（可选）

```bash
cp .env.example .env
```

不配置任何模型也能完整运行：系统会使用内置推理引擎完成全部推理任务。接入云端或私有化模型的方式见下文「配置说明」。

### 3. 初始化演示数据

```bash
# 使用内置推理引擎快速跑通全链路（约 1–2 分钟，无需联网）
TECHINS_DISABLE_LLM=1 .venv/bin/python scripts/bootstrap_demo.py
```

该脚本会创建演示企业、产品、投保单、保单、监测方案、预警、事故与理赔等数据，并跑通产品设计与服务流程。

### 4. 启动后端

```bash
# 方式一：直接启动
.venv/bin/uvicorn techins.api.app:app --host 127.0.0.1 --port 8742

# 方式二：使用安装后的命令行入口
.venv/bin/techins
```

### 5. 启动前端

```bash
cd web
pnpm install
pnpm dev --port 4317
```

打开 <http://localhost:4317>，使用演示账号登录（密码均为 `demo1234`）：

| 账号 | 角色 | 主要功能 |
|---|---|---|
| `designer` | 产品设计人员（一级审批） | 产品开发入口：背景与需求、风险建议、圈定、产品规则与条款 |
| `actuary` | 产品设计人员（二级审批） | 同一条产品开发入口；额外负责选定拟采用费率并审批发布 |
| `service` | 产品服务人员（一级审批） | 核保、预警处置、事故响应、理赔 |
| `senior` | 产品服务人员（二级审批） | 核保决定、赔付审批 |
| `admin` | 系统管理员（三级审批） | 全部功能、模型路由、隐私与审计、经验评审 |

### 6. 使用 MySQL（可选）

```bash
cp .env.example .env
# 在 .env 中填写 MYSQL_PASSWORD、MYSQL_ROOT_PASSWORD 等
docker compose up -d
```

填写 `MYSQL_PASSWORD` 后应用会自动连接本机 MySQL；也可通过 `TECHINS_DATABASE_URL` 直接指定连接串。留空时使用 `data/techins.db`。

## 配置说明

所有配置通过环境变量或项目根目录的 `.env` 读取，完整清单见 [`.env.example`](.env.example)。

| 变量 | 说明 |
|---|---|
| `CLOUD_LLM_BASE_URL` / `CLOUD_LLM_API_KEY` / `CLOUD_LLM_MODEL` | 云端大模型（OpenAI 兼容接口），用于规划、综合推理、长文本、评审与反思，只接收脱敏后的数据 |
| `CLOUD_LLM_EXTRA` | 云端模型附加参数（JSON） |
| `PRIVATE_LLM_BASE_URL` / `PRIVATE_LLM_API_KEY` / `PRIVATE_LLM_MODEL` | 私有化部署模型（文本与多模态共用），处理私有材料、图像与高敏数据 |
| `PRIVATE_LLM_EXTRA` | 私有化模型附加参数（JSON） |
| `TECHINS_DISABLE_LLM=1` | 关闭大模型，全部推理任务由内置推理引擎完成 |
| `TECHINS_DISABLE_RW_LLM=1` | 只关闭风险预警的大模型 |
| `TECHINS_DATA_DIR` | 运行数据目录，默认 `<项目根>/data` |
| `TECHINS_DATABASE_URL` | 数据库连接串，优先级高于 MySQL 环境变量与 SQLite 默认值 |
| `MYSQL_*` | MySQL 连接参数（`docker compose up -d` 可直接启动本地实例） |
| `TECHINS_JWT_SECRET` | JWT 签名密钥，生产环境必须设置为强随机值 |
| `TECHINS_CORS_ORIGINS` | 允许的跨域来源，逗号分隔，默认 `*`；生产环境请收紧 |
| `TECHINS_MONITOR_TICK_SECONDS` / `TECHINS_SIM_MINUTES_PER_TICK` | 常驻监测的调度间隔与每次推进的仿真时长 |
| `TECHINS_DEMO_BOOTSTRAP=1` | 启动时在后台自动创建演示数据 |

> 私有化模型的接口地址应指向本地部署的服务。若临时指向公网接口进行联调，私域数据会离开本地环境，只能用于测试数据。

路由规则与模型可在「系统管理 → 云私计算与路由」中查看与启停。

## 测试

```bash
# 单元测试与集成测试（使用临时目录与 SQLite，不读取 .env 中的 MySQL）
.venv/bin/pytest -q

# 产品设计流水线冒烟测试（需求描述 → 圈定 → 产品规则与条款 → 两种费率，自动处理人审卡片）
TECHINS_DATABASE_URL= TECHINS_DATA_DIR=/tmp/techins-test TECHINS_DISABLE_LLM=1 \
  .venv/bin/python scripts/smoke_pipeline.py

# 全生命周期引导（创建全部演示数据并打印每个运行的评估结果）
TECHINS_DATABASE_URL= TECHINS_DATA_DIR=/tmp/techins-demo TECHINS_DISABLE_LLM=1 \
  .venv/bin/python scripts/bootstrap_demo.py
```

前端检查：

```bash
cd web
pnpm typecheck
pnpm lint
```

> `TECHINS_DATABASE_URL=`（显式置空）用于让脚本使用临时 SQLite，避免连接 `.env` 中配置的 MySQL。

## 生产部署要点

1. **密钥**：设置强随机的 `TECHINS_JWT_SECRET` 与 `TECHINS_CONNECTOR_KEY`，不要使用默认值。
2. **数据库**：使用 MySQL 等持久化数据库，并把 `TECHINS_DATA_DIR` 挂载到持久卷；不要提交或打包 `data/`。
3. **跨域**：通过 `TECHINS_CORS_ORIGINS` 限定前端域名，不要在生产环境使用 `*`。
4. **模型**：私域数据只路由到私有化部署模型；如无模型条件，可设置 `TECHINS_DISABLE_LLM=1` 使用内置推理引擎。
5. **演示数据**：生产环境不要设置 `TECHINS_DEMO_BOOTSTRAP=1`，并按需替换知识库中的示例条款与费率。
6. **反向代理**：前端与后端通过反向代理暴露同一域名，前端将 `/api` 转发到后端 `8742` 端口。
7. **合规**：正式上线前请完成保险条款、费率、数据安全与隐私合规审核。

## 隐私与数据分级

平台按 L0–L3 对数据分级：L0 公开、L1 内部、L2 敏感商业、L3 高敏（个人信息、核心技术秘密）。模型网关按数据级别选择云端或私有化模型，敏感数据出域前会经过脱敏、二次扫描与审计。仓库中的演示企业、材料与金额均为合成数据。

## 免责声明

- 本项目中的险种、条款、费率、精算结果与理赔结论均为示例，仅供技术演示与学习，不构成保险产品、法律、税务或精算意见。
- 在将本项目用于真实业务前，请自行替换为经合规审核的数据与规则，并对模型输出进行人工复核。
- 本项目按「现状」提供，不附带任何明示或暗示的担保。

## 贡献

欢迎提交 Issue 与 Pull Request。开始之前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。
