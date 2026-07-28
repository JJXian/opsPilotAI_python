"""Bug 修复 Agent 的接口模型。"""

from pydantic import BaseModel, Field, model_validator


class BugFixApproval(BaseModel):
    """恢复被 human-in-the-loop 中断的 Incident。"""

    approved: bool
    reason: str = Field(default="", max_length=500)


class BugFixRequest(BaseModel):
    """用户粘贴日志或选择预配置服务器日志源的诊断选项。"""

    session_id: str = Field(default="default", max_length=120)
    incident_id: str | None = Field(
        default=None,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$",
        description="稳定的故障 ID；同一 ID 用于幂等调用和断点恢复",
    )
    log: str | None = Field(
        default=None, max_length=30000, description="Python 或 Java/Spring Boot 异常堆栈或错误日志"
    )
    log_source_id: str | None = Field(
        default=None, max_length=64, description="预配置服务器日志源 ID"
    )
    include_diff: bool = Field(default=False, description="是否生成未应用的建议 Diff")
    approval: BugFixApproval | None = Field(default=None, description="高风险步骤的人工审批结果")

    @model_validator(mode="after")
    def validate_log_input(self):
        if self.approval is not None:
            if self.log or self.log_source_id:
                raise ValueError("恢复审批时不要重复提交 log 或 log_source_id。")
            if not self.incident_id:
                raise ValueError("恢复审批必须提供 incident_id。")
            return self
        if bool(self.log and self.log.strip()) == bool(self.log_source_id):
            raise ValueError("请二选一：提供 log，或提供 log_source_id。")
        if self.log is not None and len(self.log.strip()) < 20:
            raise ValueError("日志至少需要 20 个字符。")
        return self
