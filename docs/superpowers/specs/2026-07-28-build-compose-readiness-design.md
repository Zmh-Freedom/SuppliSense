# 构建与 Compose 上线门禁修复设计

## 目标

恢复可重复的生产构建与 Docker Compose 启动链路，使以下命令成为可靠质量门禁：

- 前端 `npm test`
- 前端 `npm run lint`
- 前端 `npm run build`
- 后端相关配置与健康检查测试
- `docker compose --env-file .env.docker config --quiet`

本次只修复构建、配置传递和健康检查，不扩展业务功能，不放宽现有 TypeScript 或 ESLint 规则。

## 已确认根因

### 前端

- 测试源码参与 TypeScript 项目构建，但 Vitest 全局类型没有进入 TypeScript 配置。
- `BrokenComponent` 的返回类型无法作为 JSX 组件使用。
- 多个组件保留未使用的导入、参数或局部变量，触发 `noUnusedLocals` / `noUnusedParameters`。
- `SupplierProfilePage` 的 Recharts tooltip formatter 类型过窄。
- `AssessView`、`ChatView` 和 `ChartRenderer` 存在 React Hooks/不可变性 lint 错误。

### Compose

- Compose 变量插值发生在读取服务 `env_file` 之前；当前 `start.sh` 未用 `--env-file .env.docker`，因此数据库密码无法传入 Compose。
- Redis 服务要求 `REDIS_PASSWORD`，但环境示例和当前键集合没有完整表达该变量；后端 `REDIS_URL` 也必须与密码认证保持一致。
- 后端健康检查使用 `curl`，而 `python:3.12-slim` 镜像没有安装 curl。
- readiness 只检查 MongoDB 和 Redis，依赖不可用时仍返回 HTTP 200，也未检查 PostgreSQL。
- PostgreSQL 初始化错误路径混用了标准 logging 与 structlog 的关键字参数，掩盖原始连接异常。

## 修复方案

### 前端质量门禁

逐项修复源代码问题，不通过关闭规则或排除业务文件绕过检查：

- 为测试环境补齐 Vitest 类型，并明确抛错测试组件的 JSX 返回类型。
- 删除未使用代码，收紧事件和 formatter 类型。
- 将可派生状态从 effect 中移除，交互逻辑放回事件处理器。
- 避免修改来自 React state 的数组；所有消息更新使用新数组或函数式 state 更新。
- 饼图角度数据先以纯函数生成，再渲染 SVG。
- 路由文件的 Fast Refresh 规则按其实际职责做局部 ESLint 配置，不改变生产行为。

### Compose 与环境配置

- `start.sh` 在启动和配置校验时统一使用 `docker compose --env-file .env.docker`。
- `.env.docker.example` 明确包含 `REDIS_PASSWORD`、PostgreSQL连接参数及带认证的 `REDIS_URL` 示例。
- 启动前验证环境文件存在，并先执行 Compose 配置校验；配置错误时不进入构建。
- 后端容器健康检查使用 Python 标准库请求 `/health/live`，不增加系统依赖。

### 健康检查

- `/health/live` 只表示进程存活。
- `/health/ready` 检查 MongoDB、Redis 和 PostgreSQL。
- 任一关键依赖不可用时返回 HTTP 503，并保留各依赖状态。
- Compose 的后端健康检查使用 readiness，确保前端仅在后端真正可服务后启动。
- 修复 PostgreSQL 初始化异常日志，保留原始异常并抛出明确的启动错误。

## 测试策略

采用回归优先：

1. 先以当前失败命令固定 RED：前端 lint/build、Compose config。
2. 为 readiness 的 200/503 行为和 PostgreSQL检查编写失败测试。
3. 最小实现后运行定向测试。
4. 最终运行前端 test/lint/build、后端定向测试、Compose config 和 `git diff --check`。

由于完整后端测试目前依赖真实 MongoDB/PostgreSQL，实施时会让新增健康检查测试完全隔离外部服务；完整集成测试环境作为后续 CI 修复任务处理，不在本次通过跳过测试来掩盖。

## 非目标

- 不处理 TLS、数据库版本化迁移、全量备份和恢复演练。
- 不完成供应商画像后续功能。
- 不改变现有 API 正常响应字段。
- 不新增第三方依赖。

## 验收标准

- `npm test`、`npm run lint`、`npm run build` 均退出码 0。
- 环境键完整时 `docker compose --env-file .env.docker config --quiet` 退出码 0。
- readiness 三项依赖全部健康时返回 200；任一失败时返回 503。
- 后端镜像健康检查不依赖 curl。
- 启动脚本遇到缺失环境文件或配置错误时立即停止并给出明确提示。
