<!-- flow-workbench:business-control-runtime:start -->
## Flow Workbench 业务控制 Runtime 约束

- `flow.finish`、`flow.pause`、`flow.resume`、`flow.rewind`、`flow.abort` 是 MCP 工具，必须通过 Agent Runtime 的 MCP 工具调用。
- 禁止用 Bash、CLI、伪命令、代码块、最终回复或“已调用”文本替代 MCP 回执。
- 不要自行填写 `contextPath`、`runId`、`stage`、`actionIndex`；Workbench 通过 `FLOW_MCP_REQ_PATH` 和 control context 注入。
- 用户 Skill 可保持极简业务意图；Runtime 必须将“调用 MCP 工具 `flow.*`，参数 JSON”落实为真实工具调用。
- `flow.resume` 仅用于解除已有 `waiting` 业务状态；业务成功或失败使用 `flow.finish`。
- `flow.pause/flow.resume` 只是当前任务内的中间澄清，不代表任务完成；当前任务最终必须调用 `flow.finish`、`flow.rewind` 或 `flow.abort`。
<!-- flow-workbench:business-control-runtime:end -->
