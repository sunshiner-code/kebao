# 贡献指南

感谢你愿意为科技保险智能体业务平台（techins）做出贡献！本文说明本地开发、提交与评审的基本流程。

## 行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 开发环境

- Python 3.12+
- Node.js 22+、pnpm 10+
- 推荐使用 [uv](https://docs.astral.sh/uv/) 管理 Python 依赖

```bash
git clone <仓库地址>
cd kw_system

# 后端
uv sync --dev

# 前端
cd web && pnpm install
```

演示数据与本地运行方式见 [README.md](README.md)。

## 提交 Issue

- 先搜索是否已有相同 Issue，避免重复。
- Bug 报告请包含：复现步骤、期望行为、实际行为、操作系统与版本、相关日志（请先脱敏）。
- 功能建议请说明使用场景与期望效果。

## 提交 Pull Request

1. Fork 仓库并从 `main` 创建分支，分支名建议使用 `feat/xxx`、`fix/xxx`、`docs/xxx`。
2. 保持改动聚焦，一个 PR 只做一件事。
3. 新增或修改行为时补充测试。
4. 提交前在本地运行：

   ```bash
   uv run pytest -q
   cd web && pnpm typecheck && pnpm lint
   ```

5. PR 描述中说明：改了什么、为什么改、如何验证、是否有破坏性变更。
6. 确保 PR 通过 CI。

## 代码规范

### Python

- 目标版本 Python 3.12，使用 `from __future__ import annotations`。
- 遵循现有模块划分：`harness` 负责运行框架，`agents` 负责智能体配置，`services` 负责业务编排，`api` 只做协议转换。
- 业务逻辑不要写进 API 路由；跨模块复用请下沉到 `services` 或 `harness`。
- 新增环境变量时同步更新 `.env.example` 与 README。
- 不要提交真实企业数据、密钥、数据库文件或 `data/` 目录内容。

### 前端

- Next.js App Router + TypeScript + Tailwind CSS。
- 组件放在 `web/src/components`，页面放在 `web/src/app`。
- 使用 `pnpm typecheck` 与 `pnpm lint` 验证。

### 智能体

- 新智能体请复用 `harness` 的主循环、工具注册与评估机制，不要单独实现一套运行逻辑。
- 技能文件（`src/techins/skills/SK-*.md`）使用现有分节结构，写明适用条件、步骤、输出契约与评估要点。
- 涉及模型调用时，通过 `llm` 的任务注册与路由规则声明数据级别，不要直接调用模型 SDK。

## 安全

- 不要在 Issue、PR 或日志中暴露密钥、真实企业数据或个人数据。
- 发现安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告，不要公开提交 Issue。

## 许可证

提交贡献即表示你同意以 [Apache License 2.0](LICENSE) 授权你的贡献。
