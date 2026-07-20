# Bug 修复 Agent 学习指南

## 1. 它解决什么问题

Bug 修复 Agent 面向研发排障：用户粘贴 Python Traceback 后，系统从日志中的文件、行号和函数名出发，在当前项目内读取代码、搜索相关实现、查看 Git 历史和测试，再生成一份可审查的修复建议。

它默认是只读的：不会修改项目文件、运行测试命令、创建分支或提交 PR。即使用户勾选“生成建议 Diff”，也只会返回文本草案。

## 2. 工作流

```text
Python 异常堆栈
  -> Planner 解析异常类型、文件、行号、函数
  -> Executor 读取异常帧附近代码
  -> Replanner 判断是否继续收集证据
  -> Executor 搜索源码与配置
  -> Executor 读取 Git 历史并发现相关测试
  -> Executor 汇总可确认事实与不确定项
  -> Replanner 生成根因、修改建议、风险和测试建议
```

其中 Planner、Executor、Replanner 仍然遵循原有 AIOps 的 Plan-Execute-Replan 思路，但执行器不再让模型自由选择系统工具，而是只运行固定的只读步骤。

## 3. 关键模块

| 文件 | 作用 |
| --- | --- |
| `app/api/bugfix.py` | `POST /api/bugfix` 的 SSE 接口 |
| `app/models/bugfix.py` | 堆栈、会话 ID、建议 Diff 选项的请求模型 |
| `app/services/bugfix_service.py` | Planner、Executor、Replanner 状态图和报告生成 |
| `app/services/bugfix_tools.py` | 路径隔离、堆栈解析、代码读取、源码搜索、Git 与测试发现 |
| `static/index.html`、`static/app.js` | “Bug 修复”按钮、堆栈输入面板和流式步骤展示 |

## 4. 安全边界

`bugfix_repository_root` 默认是项目当前目录。所有堆栈路径都会先被解析并校验：只有位于该目录内的文件才允许读取；`../../` 等越界路径会被拒绝。

允许的动作：

- 读取源码上下文。
- 用 `rg` 搜索源码与配置。
- 读取 `git log` 历史。
- 查找 `tests/` 下名称关联的测试文件。

禁止的动作：

- 写入、删除或重命名文件。
- 执行测试、部署、重启或 Git 写操作。
- 接受用户指定的任意服务器文件系统路径。

## 5. 如何使用

1. 打开页面后点击右上角“Bug 修复”。
2. 粘贴完整 Python Traceback，至少应包含 `File "...", line N, in ...` 和最后的异常行。
3. 默认点击“开始定位”。需要查看补丁草案时，再勾选“生成建议 Diff”。
4. 等待报告生成。页面会显示执行计划和每一步完成状态，最终报告包含：疑似根因、代码证据、修改建议、风险、测试建议和不确定项。

也可以直接调用接口：

```bash
curl -N -X POST http://127.0.0.1:9901/api/bugfix \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "demo",
    "log": "Traceback (most recent call last):\\n  File \\"app/example.py\\", line 12, in run\\n    ...\\nValueError: invalid input",
    "include_diff": false
  }'
```

## 6. 如何验证

使用项目内真实文件路径构造一段示例堆栈，例如：

```text
Traceback (most recent call last):
  File "app/services/bugfix_service.py", line 196, in _execute_step
    raise ValueError(f"未知诊断步骤: {kind}")
ValueError: 未知诊断步骤: invalid
```

预期结果：

- 执行过程显示四步只读诊断计划。
- 报告中的“代码证据”包含 `app/services/bugfix_service.py:196`。
- “执行边界”明确写明未修改文件、未执行修复命令。
- PostgreSQL `agent_audit_logs` 表出现 `bugfix_parse`、`bugfix_execute`、`bugfix_report` 等事件。

## 7. 后续演进

下一阶段可以接入 GitHub/GitLab，在人工确认后创建临时分支、应用建议 Diff、运行白名单测试并创建 Draft PR。这个阶段应继续保持“确认前只读”的边界，并为每一次写操作保存审计记录。
