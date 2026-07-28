# 动态 Bugfix Agent 学习指南

## 1. 目标

Bugfix Agent 不再执行固定四步模板。它把一次故障建模为稳定的 Incident，并在服务端
安全边界内动态收集证据、修正假设、增删步骤，最后输出带终止原因的修复建议。

## 2. 状态机

```text
Planner
  -> Executor
      -> Approval interrupt（仅高风险步骤）
      -> Replanner
          -> Executor（继续或替换计划）
          -> Report（证据充分、预算耗尽、超时或需要补充信息）
```

Planner 输入异常类型、服务语言、仓库堆栈帧和原始日志，输出 `PlannedStep` 列表。每一步
只能引用服务端注册的工具。Replanner 每轮读取新增证据和剩余计划，可以：

- `continue`：保留剩余计划；
- `replace`：删除无效步骤并增加新的证据收集步骤；
- `finish`：证据充分，提前生成报告；
- `clarify`：缺少只能由用户提供的信息，停止并提出具体问题。

## 3. 受控工具

| 工具 | 风险 | 行为 |
| --- | --- | --- |
| `analyze_logs` | low | 解析异常和关键错误行 |
| `inspect_frames` | low | 读取仓库内堆栈帧上下文 |
| `search_code` | low | 在受控文件类型中搜索源码和配置 |
| `inspect_git` | low | 读取相关文件提交历史 |
| `inspect_releases` | low | 读取最近标签、提交时间和发布线索 |
| `inspect_metrics` | medium | 查询固定 Prometheus 地址的当前告警 |
| `find_tests` | low | 自动发现相关测试 |
| `run_tests` | high | 只运行自动发现的测试，必须人工审批 |
| `verify` | low | 汇总已确认事实、冲突和证据缺口 |

风险等级来自服务端注册表，不能由 Planner 修改。工具不接受任意 Shell、主机、路径或测试
参数。

## 4. 失控防护

- 最大执行步骤：`BUGFIX_MAX_STEPS`，默认 8；
- 估算 Token 预算：`BUGFIX_TOKEN_BUDGET`，默认 12000；
- Incident 超时：`BUGFIX_TIMEOUT_SECONDS`，默认 120 秒；
- 可重试读取工具：`BUGFIX_TOOL_RETRY_ATTEMPTS`，默认 2 次；
- 工具超时：`BUGFIX_TOOL_TIMEOUT_SECONDS`，默认 20 秒；
- 终止原因：`completed`、`evidence_sufficient`、`max_steps`、`token_budget`、
  `timeout`、`needs_human_input`、`human_rejected` 或 `no_actionable_plan`。

每次工具调用根据 `incident_id + tool + 受控参数` 生成 SHA-256 幂等键。工具结果保存在
LangGraph 状态中；服务重启后，PostgreSQL Checkpointer 能恢复计划、证据、缓存和审批
状态。

## 5. Human-in-the-loop

高风险步骤进入 LangGraph `interrupt`，在审批前不会执行。SSE 会返回：

```json
{
  "type": "approval_required",
  "data": {
    "incident_id": "INC-2026-001",
    "step_id": "run-tests",
    "tool": "run_tests",
    "risk": "high"
  }
}
```

批准后使用同一个 Incident 恢复：

```bash
curl -N -X POST http://127.0.0.1:9900/api/bugfix \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "bugfix-demo",
    "incident_id": "INC-2026-001",
    "approval": {
      "approved": true,
      "reason": "允许执行自动发现的白名单测试"
    }
  }'
```

拒绝时 Replanner 会删除该高风险步骤，并根据剩余证据决定继续诊断还是输出报告。

## 6. 断点恢复

首次请求应指定稳定的 `incident_id`：

```bash
curl -N -X POST http://127.0.0.1:9900/api/bugfix \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "bugfix-demo",
    "incident_id": "INC-2026-001",
    "log": "Traceback ...",
    "include_diff": false
  }'
```

图使用 `bugfix:<incident_id>` 作为 thread_id，而不是每次生成随机 ID。重复提交已完成的
Incident 会返回缓存报告；节点异常或服务重启后会从 Checkpointer 的下一节点继续。

状态查询：

```bash
curl http://127.0.0.1:9900/api/bugfix/incidents/INC-2026-001
```

## 7. 面试时应准确说明

Planner 和 Replanner 是 LLM 决策，但执行权限不属于 LLM。模型只能提出结构化步骤，
服务端负责工具白名单、风险等级、参数构造、预算、幂等、重试、缓存和审批。这样既保留
动态诊断能力，也避免模型获得任意代码执行或生产变更权限。
