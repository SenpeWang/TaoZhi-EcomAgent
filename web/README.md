# 前端工程

Vue 3 与 Vite 构建的企业工作台，产物由 `npm run build` 输出到 `dist/`，随后打包进应用镜像。

| 路径 | 用途 |
| --- | --- |
| `src/app/` | 应用外壳：登录门户、工作台概览、会话接口与全局样式 |
| `src/features/` | 业务页面：`administration`、`bi`、`documents`、`observability`、`tasks` |
| `src/shared/` | 共享组合式逻辑与任务流订阅 |
| `src/assets/` | 品牌图标与架构图 |
| `eslint.config.js`、`.prettierrc.json` | 代码与格式规范 |
| `commitlint.config.cjs` | 提交信息校验 |

本地校验入口为仓库根目录的 `make lint-web` 与 `make check-format`。
