# 提交规范

首次克隆项目后执行：

```bash
./scripts/setup-git-hooks.sh
```

提交信息使用以下格式：

```text
<type>(<scope>): <description>
```

允许的 `type`：`feat`、`fix`、`docs`、`test`、`refactor`、`chore`、`perf`、`ci`、`build`、`revert`、`style`、`security`、`ui`、`debug`、`improve`、`merge`。

示例：

```text
feat(sourcing): 增加联网供应商发现
fix(agent): 修复多轮上下文丢失
test(risk): 增加风险分析测试
docs: 更新开发文档
```

Pull Request 会在 CI 中再次校验提交信息。
