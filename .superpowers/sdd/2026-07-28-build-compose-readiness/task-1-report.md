# Task 1 Report: 修复 TypeScript 生产构建

## Status

DONE_WITH_CONCERNS

## 修改文件与关键决策

- `frontend/tsconfig.app.json`
  - 将 `compilerOptions.types` 设置为 `["vite/client", "vitest/globals"]`，让 Vitest 全局 `vi` 参与 TypeScript 编译。
- `frontend/src/__tests__/AppErrorBoundary.test.tsx`
  - 将 `BrokenComponent` 明确声明为 `function BrokenComponent(): never`，保留其抛错行为。
- `frontend/src/components/Dashboard.tsx`
  - 删除未使用的 `getRiskLevel` 导入。
- `frontend/src/components/SentimentPanel.tsx`
  - 删除未使用的 `isStale` 变量，保留现有舆情展示与刷新逻辑。
- `frontend/src/components/SupplierProfilePage.tsx`
  - 删除未使用的风险颜色/等级导入。
  - 删除 `OverviewTab`、`RiskTab`、`FinancialTab` 中未使用的 props 及对应调用参数，不改变实际展示逻辑。
  - 将 tooltip formatter 改为 `formatter={(value) => [\`${Number(value ?? 0)} 分\`, '风险评分']}`，兼容 Recharts 可空值类型。

## RED 命令及失败摘要

命令：`cd frontend && npm run build`

退出码：2

失败摘要：

- `AppErrorBoundary.test.tsx` 找不到全局 `vi`。
- `BrokenComponent` 返回类型推断为 `void`，不能作为 JSX 组件。
- `Dashboard.tsx` 的 `getRiskLevel` 未使用。
- `SentimentPanel.tsx` 的 `isStale` 未使用。
- `SupplierProfilePage.tsx` 的 `getRiskBg`、`getRiskLevel`、`financial`、`name`、`risk` 等声明未使用。
- `SupplierProfilePage.tsx` 的 Recharts formatter 参数不能从 `number` 收窄为可空的 `ValueType`。

## GREEN 命令、退出码和通过数量

- `npm test && npm run build`：退出码 0。
  - Vitest：2 个测试文件通过，6 个测试通过。
  - Vite 生产构建：成功。
- `npx eslint src/__tests__/AppErrorBoundary.test.tsx src/components/Dashboard.tsx src/components/SentimentPanel.tsx src/components/SupplierProfilePage.tsx`：退出码 0。
- `git diff --check`：退出码 0。

## Commit SHA

待提交后填写。

## Concerns

全量 `npm run lint` 退出码 1，报告 17 个错误、5 个警告，全部位于本任务简报未列出的既有文件：`AssessView.tsx`、`ChartRenderer.tsx`、`ChatView.tsx`、`LoginPage.tsx`、`routes.tsx`。根据“只修改简报列出的文件”约束未处理这些无关 lint 问题；本任务涉及文件的定向 lint 已通过。
