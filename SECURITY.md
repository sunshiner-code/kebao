# 安全策略

## 支持的版本

| 版本 | 支持状态 |
|---|---|
| 0.1.x | 支持 |

## 报告漏洞

请**不要**通过公开 Issue 报告安全漏洞。请使用 GitHub 仓库的
[Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
（Security → Report a vulnerability）私下联系维护者。

报告中请尽量包含：

- 漏洞类型与影响范围
- 复现步骤或最小复现示例
- 受影响版本与部署方式
- 建议的修复方向（如有）

我们会在确认后尽快回复，并在修复发布后致谢（如你希望署名）。

## 重点关注的范围

- 认证与授权绕过、权限提升
- JWT 密钥、连接器凭据等敏感信息泄露
- 隐私数据出域、脱敏不完整或审计缺失
- 连接器 SSRF、命令注入、路径穿越
- 文件上传、文档解析、模板渲染导致的远程代码执行
- 提示词注入导致越权工具调用或数据泄露
- 依赖供应链风险

## 部署安全建议

- 生产环境必须设置强随机的 `TECHINS_JWT_SECRET` 与 `TECHINS_CONNECTOR_KEY`。
- 通过 `TECHINS_CORS_ORIGINS` 收紧跨域来源，不要使用默认的 `*`。
- 私域数据只路由到本地或专有部署的模型；不要把私有化模型地址指向公网接口。
- 不要把 `.env`、`data/`、数据库文件或上传材料提交到版本库。
- 定期轮换密钥、升级依赖，并保留审计日志。
