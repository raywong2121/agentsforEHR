"""经 Dify 控制台调用 deepseek-v4-flash（用登录态 + model_config 覆盖，不需要裸 API key）。

复用 CODEX 在 11-05 已验证的路径：
  POST /console/api/apps/{app_id}/chat-messages
  body.model_config.model = {provider: langgenius/deepseek/deepseek, name: deepseek-v4-flash, ...}
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("DIFY_BASE_URL", "http://127.0.0.1:18080")
APP_ID = os.environ.get("DIFY_APP_ID", "")
PROVIDER = "langgenius/deepseek/deepseek"
MODEL = "deepseek-v4-flash"


def _login():
    mgmt = os.environ.get("DIFY_MANAGEMENT_MODULE")
    if not mgmt:
        raise RuntimeError(
            "设置 DIFY_MANAGEMENT_MODULE 为本地 Dify 控制台源码包路径"
            "（其中包含 management.Api 登录实现，本仓库不含该模块）")
    sys.path.insert(0, mgmt)
    from management import Api
    email = os.environ.get("DIFY_EMAIL", "")
    password = os.environ.get("DIFY_PASSWORD", "")
    if not password:
        raise RuntimeError("缺少 DIFY_PASSWORD 环境变量（Dify 控制台密码，不落盘）")
    return Api(email, password)


def chat(system: str, user: str, max_tokens: int = 512, json_mode: bool = False) -> dict:
    api = _login()
    completion = {"temperature": 0, "max_tokens": max_tokens, "thinking": False}
    if json_mode:
        completion["response_format"] = "json_object"
    body = {
        "query": user,
        "inputs": {},
        "response_mode": "streaming",
        "model_config": {
            "model": {"provider": PROVIDER, "name": MODEL, "mode": "chat",
                      "completion_params": completion},
            "pre_prompt": system,
            "prompt_type": "simple",
            "user_input_form": [],
            "agent_mode": {"enabled": False, "tools": []},
            "external_data_tools": [],
            "dataset_configs": {"datasets": {"datasets": []}},
            "suggested_questions_after_answer": {"enabled": False},
            "speech_to_text": {"enabled": False},
            "text_to_speech": {"enabled": False},
        },
    }
    req = urllib.request.Request(
        BASE + "/console/api/apps/" + APP_ID + "/chat-messages",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "Cookie": api.headers.get("Cookie", ""),
                 "X-CSRF-Token": api.headers.get("X-CSRF-Token", "")},
        method="POST")
    with api.opener.open(req, timeout=130) as resp:
        raw = resp.read()

    # 解析 SSE
    text = ""
    usage = {}
    message_id = None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("event") == "error":
            raise RuntimeError("flash_debug_error: " + json.dumps(ev)[:300])
        if ev.get("event") in ("message", "agent_message"):
            text += ev.get("answer", "")
        elif ev.get("event") == "message_replace":
            text = ev.get("answer", "")
        elif ev.get("event") == "message_end":
            usage = (ev.get("metadata") or {}).get("usage") or {}
            message_id = ev.get("message_id")
    if not text.strip():
        raise RuntimeError("empty flash response (model returned no text)")
    return {"text": text, "usage": usage, "message_id": message_id}


if __name__ == "__main__":
    r = chat("你是助手。", "只回复四个字：调用成功", max_tokens=32)
    print("text:", r["text"].strip())
    print("usage:", r["usage"])
