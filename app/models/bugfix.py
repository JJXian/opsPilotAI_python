"""Bug 修复 Agent 的接口模型。"""

from pydantic import BaseModel, Field


class BugFixRequest(BaseModel):
    """用户粘贴的 Python 异常堆栈与诊断选项。"""

    session_id: str = Field(default="default", max_length=120)
    log: str = Field(min_length=20, max_length=30000, description="Python 异常堆栈或错误日志")
    include_diff: bool = Field(default=False, description="是否生成未应用的建议 Diff")

