# -*- coding: utf-8 -*-
"""LLM 适配层。零第三方依赖（urllib + threading）。

只做三件事：
  1. 发请求（OpenAI 兼容协议，兼容 DeepSeek / 通义 / 智谱 / 本地 vLLM / Ollama）
  2. 重试与错误分类（超时 / 限流 / 服务端 / 客户端错误，处置方式不同）
  3. 从返回里抠出 JSON（模型偶尔会裹代码块或加前后缀）

不做：prompt 拼接（在 renderer）、结构校验（在 validator）。
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from .errors import LLMBadResponseError, LLMError, LLMRateLimitError, LLMTimeoutError, NotConfiguredError

def _config_path() -> Path:
    """打包成 exe 后写到用户目录（程序目录可能不可写）。"""
    import sys
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "ScriptGenerator" / "config.json"
    return Path(__file__).resolve().parents[1] / "config.local.json"


# 界面里填的模型配置存这里。**不进版本库**（见 .gitignore）。
CONFIG_PATH = _config_path()


def load_saved_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        d = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(patch: dict) -> dict:
    """合并保存。api_key 传空字符串表示"不改"，传 null 表示"清除"。"""
    cur = load_saved_config()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    for k in ("base_url", "api_key", "model", "timeout", "max_retries",
              "temperature", "max_tokens", "json_mode"):
        if k not in patch:
            continue
        v = patch[k]
        if k == "api_key":
            if v is None:
                cur.pop(k, None)
            elif str(v).strip() and not str(v).startswith("***"):
                cur[k] = str(v).strip()
            continue
        if v == "" or v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    CONFIG_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cur


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: int = 120
    max_retries: int = 3
    temperature: float = 0.8
    json_mode: bool = True
    max_tokens: int = 4000

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.environ.get("SG_LLM_BASE_URL", "").rstrip("/"),
            api_key=os.environ.get("SG_LLM_API_KEY", ""),
            model=os.environ.get("SG_LLM_MODEL", ""),
            timeout=int(os.environ.get("SG_LLM_TIMEOUT", "120")),
            max_retries=int(os.environ.get("SG_LLM_MAX_RETRIES", "3")),
            temperature=float(os.environ.get("SG_LLM_TEMPERATURE", "0.8")),
            json_mode=os.environ.get("SG_LLM_JSON_MODE", "1") != "0",
            max_tokens=int(os.environ.get("SG_LLM_MAX_TOKENS", "4000")),
        )

    @classmethod
    def from_sources(cls) -> "LLMConfig":
        """优先级：环境变量 > config.local.json > 默认值。

        环境变量优先，是为了让 CI / 容器部署能覆盖本地文件；
        界面上改的配置落到文件，重启后仍然生效。
        """
        cfg = cls.from_env()
        saved = load_saved_config()
        if saved:
            if not cfg.base_url and saved.get("base_url"):
                cfg.base_url = str(saved["base_url"]).rstrip("/")
            if not cfg.api_key and saved.get("api_key"):
                cfg.api_key = str(saved["api_key"])
            if not cfg.model and saved.get("model"):
                cfg.model = str(saved["model"])
            for k, cast in (("timeout", int), ("max_retries", int),
                            ("temperature", float), ("max_tokens", int)):
                if os.environ.get(f"SG_LLM_{k.upper()}"):
                    continue
                if saved.get(k) not in (None, ""):
                    try:
                        setattr(cfg, k, cast(saved[k]))
                    except (TypeError, ValueError):
                        pass
            if saved.get("json_mode") is not None and not os.environ.get("SG_LLM_JSON_MODE"):
                cfg.json_mode = bool(saved["json_mode"])
        return cfg

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def describe(self) -> dict:
        return {
            "configured": self.configured,
            "base_url": self.base_url or "(未设置)",
            "model": self.model or "(未设置)",
            "api_key": ("***" + self.api_key[-4:]) if self.api_key else "(未设置)",
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }


def extract_json(text: str) -> dict:
    """从模型输出里抠出第一个完整 JSON 对象。"""
    if not text or not text.strip():
        raise LLMBadResponseError("模型返回为空", hint="检查模型是否被内容过滤拦截")
    s = text.strip()
    # 去代码块
    m = re.search(r"```(?:json)?\s*(.+?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 括号配对扫描，容忍前后缀
    start = s.find("{")
    if start < 0:
        raise LLMBadResponseError("返回里没有 JSON 对象", hint="可降低 temperature 或强化『只输出 JSON』指令")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except json.JSONDecodeError as e:
                    raise LLMBadResponseError(f"JSON 不合法: {e}", hint="重试一次；连续失败则降级为模板出稿") from e
    raise LLMBadResponseError("JSON 括号不闭合（可能被 max_tokens 截断）",
                              hint="调大 SG_LLM_MAX_TOKENS，或缩短单集长度")


class LLMClient:
    """OpenAI 兼容 client。"""

    def __init__(self, cfg: LLMConfig | None = None):
        self.cfg = cfg or LLMConfig.from_sources()

    # ---- 对外 ----
    def chat_json(self, system: str, user: str, *, label: str = "") -> dict:
        raw = self.chat(system, user, label=label)
        return extract_json(raw)

    def chat(self, system: str, user: str, *, label: str = "") -> str:
        if not self.cfg.configured:
            raise NotConfiguredError(
                "未配置 LLM",
                hint="设置 SG_LLM_BASE_URL / SG_LLM_API_KEY / SG_LLM_MODEL，或使用 template 模式出稿",
                where=label,
            )
        payload = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        if self.cfg.json_mode:
            payload["response_format"] = {"type": "json_object"}

        last: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                return self._post(payload, label=label)
            except _NoRetry as e:
                raise e.err from None
            except (LLMTimeoutError, LLMRateLimitError, LLMError) as e:
                last = e
                if attempt >= self.cfg.max_retries:
                    break
                # 400 且是 json_mode 不支持 -> 关掉再试一次
                if getattr(e, "ctx", {}).get("unsupported_json_mode"):
                    payload.pop("response_format", None)
                    self.cfg.json_mode = False
                    continue
                sleep = min(20.0, (2 ** attempt) * 1.2) + random.uniform(0, 0.8)
                time.sleep(sleep)
        raise last or LLMError("LLM 调用失败", where=label)

    # ---- 内部 ----
    def _post(self, payload: dict, *, label: str) -> str:
        url = f"{self.cfg.base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001
                pass
            if e.code == 429:
                raise LLMRateLimitError(f"限流 429: {detail}", hint="降低并发或稍后重试", where=label) from None
            if e.code in (401, 403):
                raise _NoRetry(NotConfiguredError(
                    f"鉴权失败 {e.code}: {detail}", hint="检查 SG_LLM_API_KEY 是否有效", where=label))
            if e.code == 400 and "response_format" in detail:
                err = LLMError(f"模型不支持 json mode: {detail}", where=label)
                err.ctx["unsupported_json_mode"] = True
                raise err from None
            if 500 <= e.code < 600:
                raise LLMError(f"服务端错误 {e.code}: {detail}", hint="重试；持续失败则降级", where=label) from None
            raise _NoRetry(LLMError(f"客户端错误 {e.code}: {detail}",
                                    hint="检查 base_url 是否为 .../v1 形式", where=label))
        except (socket.timeout, TimeoutError):
            raise LLMTimeoutError(f"超时（{self.cfg.timeout}s）", hint="调大 SG_LLM_TIMEOUT 或缩短单集", where=label) from None
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise LLMTimeoutError(f"连接超时: {reason}", where=label) from None
            raise LLMError(f"网络错误: {reason}", hint="检查 base_url 与网络代理", where=label) from None
        except json.JSONDecodeError as e:
            raise LLMBadResponseError(f"响应不是 JSON: {e}", where=label) from e

        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMBadResponseError(f"响应结构异常: {str(data)[:300]}", where=label) from None


class _NoRetry(Exception):
    def __init__(self, err: Exception):
        self.err = err
        super().__init__(str(err))


def probe() -> dict:
    """探活：给 UI 一个「现在能不能真生成」的答复。"""
    cfg = LLMConfig.from_sources()
    d = cfg.describe()
    d["source"] = ("env" if os.environ.get("SG_LLM_API_KEY")
                   else ("file" if CONFIG_PATH.is_file() and load_saved_config().get("api_key")
                         else "none"))
    d["config_path"] = CONFIG_PATH.name
    return d


def test_connection(cfg: LLMConfig | None = None) -> dict:
    """用一次最小调用验证连不连得上。返回结构化结果，不抛异常。"""
    conf = cfg or LLMConfig.from_sources()
    if not conf.configured:
        return {"ok": False, "error": {"code": "GEN_NO_LLM",
                                       "message": "配置不完整（base_url / api_key / model 都要填）"}}
    small = replace(conf, max_tokens=32, temperature=0.0, max_retries=1, json_mode=False)
    t0 = time.time()
    try:
        out = LLMClient(small).chat("你只回答两个字。", "回复：可用", label="connectivity-test")
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
                "sample": (out or "").strip()[:60], "model": conf.model}
    except Exception as e:  # noqa: BLE001  测试接口不该因任何异常 500
        err = e.to_dict() if hasattr(e, "to_dict") else {"code": "GEN_UNKNOWN", "message": str(e)[:200]}
        return {"ok": False, "error": err, "latency_ms": int((time.time() - t0) * 1000)}
