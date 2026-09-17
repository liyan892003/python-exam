"""Pydantic 请求/响应 schema。"""
from pydantic import BaseModel, Field, field_validator


class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    # ID 号：纯数字（学号 / 工号），至少 3 位
    email: str = Field(min_length=3, max_length=32, pattern=r"^\d{3,}$")
    password: str = Field(min_length=6, max_length=128)
    role: str = Field(default="student", pattern="^(teacher|student)$")


class LoginIn(BaseModel):
    email: str  # 兼容老账号（email）+ 新纯数字 ID
    password: str


# —— AI 出题输出校验 ——
class AIChoice(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    is_correct: bool


class AIQuestionOut(BaseModel):
    """DeepSeek 返回 JSON 的严格校验模型。"""
    stem: str = Field(min_length=4)
    choices: list[AIChoice]
    explanation: str = Field(default="")

    @field_validator("choices")
    @classmethod
    def check_choices(cls, v: list[AIChoice]) -> list[AIChoice]:
        correct = [c for c in v if c.is_correct]
        if len(v) < 2 or len(v) > 6:
            raise ValueError(f"选项数必须 2-6 个，收到 {len(v)}")
        if len(correct) != 1:
            raise ValueError(f"必须恰好 1 个正确选项，收到 {len(correct)}")
        return v
