# -*- coding: utf-8 -*-
"""异常与错误码。

分层原则：
  - 输入层错误（IN_*）   -> 阻断，不消耗 LLM 额度
  - 生成层错误（GEN_*）   -> 重试 + 降级
  - 校验层错误（VAL_*）   -> 回喂修复，最多 N 次
  - 系统层错误（SYS_*）   -> 直接抛出

每个错误都带 code / message / hint / where，前端可直接展示 hint。
"""

from __future__ import annotations


class SGError(Exception):
    """带错误码的基类。"""

    code = "SYS_UNKNOWN"
    stage = "system"

    def __init__(self, message: str, *, hint: str = "", where: str = "", **ctx):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.where = where
        self.ctx = ctx

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "hint": self.hint,
            "where": self.where,
            "context": self.ctx,
        }

    def __str__(self) -> str:  # pragma: no cover
        return f"[{self.code}] {self.message}" + (f" ({self.where})" if self.where else "")


# ---------- 输入层 ----------
class BlueprintError(SGError):
    code = "IN_BLUEPRINT"
    stage = "input"


class RefNotFoundError(SGError):
    code = "IN_REF_NOT_FOUND"
    stage = "input"


class CardinalityError(SGError):
    code = "IN_CARDINALITY"
    stage = "input"


class CompatibilityError(SGError):
    code = "IN_INCOMPATIBLE"
    stage = "input"


class ConstraintBlockedError(SGError):
    code = "IN_CONSTRAINT_BLOCKED"
    stage = "input"


# ---------- 生成层 ----------
class LLMError(SGError):
    code = "GEN_LLM_FAILED"
    stage = "generate"


class LLMTimeoutError(LLMError):
    code = "GEN_LLM_TIMEOUT"


class LLMRateLimitError(LLMError):
    code = "GEN_LLM_RATE_LIMIT"


class LLMBadResponseError(LLMError):
    code = "GEN_LLM_BAD_RESPONSE"


class NotConfiguredError(SGError):
    code = "GEN_NO_LLM"
    stage = "generate"


# ---------- 校验层 ----------
class ValidationError(SGError):
    code = "VAL_FAILED"
    stage = "validate"


# ---------- 系统层 ----------
class LibraryError(SGError):
    code = "SYS_LIBRARY"
    stage = "system"
