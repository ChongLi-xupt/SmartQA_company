"""FastAPI API 层冒烟测试（无需数据库与 LLM，使用 FastAPI TestClient）。

运行方式：``python tests/test_app_api.py``（或 pytest）。
覆盖 P0/P1：参数 schema 校验、API 版本化、错误脱敏、健康检查、请求追踪。
覆盖 P3：SSE 流式端点、用户反馈落盘、API Key 认证。
覆盖 P4：历史会话侧边栏（列表 / 回放 / 切换 / 删除）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SESSION_SECRET_KEY", "unit-test-secret-key-" + "x" * 32)

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402  需在环境变量就绪后导入

_client = TestClient(app.app)

# 进程内限流器按 IP 共享，整个测试会话的用例都命中同一 "testclient" 桶：
# 放开每分钟配额，避免用例数量增长后按执行顺序相互干扰（无用例断言 429）。
app._ask_limiter.max_calls = 10**9
app._new_chat_limiter.max_calls = 10**9
app._feedback_limiter.max_calls = 10**9


def test_index_page():
    response = _client.get("/")
    assert response.status_code == 200


def test_ask_requires_json_object():
    response = _client.post("/api/v1/ask", json=["not", "a", "dict"])
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_ask_question_must_be_string():
    response = _client.post("/api/v1/ask", json={"question": 12345})
    assert response.status_code == 400


def test_ask_question_not_empty():
    response = _client.post("/api/v1/ask", json={"question": "   "})
    assert response.status_code == 400


def test_ask_question_length_limit():
    response = _client.post("/api/v1/ask", json={"question": "长" * 600})
    assert response.status_code == 400
    assert "过长" in response.json()["error"]


def test_sensitive_question_rejected_without_agent():
    # 敏感问题在 Agent 之前被拦截：不依赖任何外部服务即可返回
    response = _client.post("/api/v1/ask", json={"question": "查询所有用户的密码"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "敏感" in payload["answer"] or "隐私" in payload["answer"]


def test_old_api_path_removed():
    response = _client.post("/api/ask", json={"question": "你好"})
    assert response.status_code == 404


def test_new_chat_endpoint():
    response = _client.post("/api/v1/new_chat")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["session_id"]


def test_request_id_header_present():
    response = _client.get("/")
    assert response.headers.get("X-Request-ID")


def test_healthz_returns_json():
    # 无数据库配置时应返回 503 且结构完整（配置齐全时为 200）
    response = _client.get("/healthz")
    assert response.status_code in (200, 503)
    assert "status" in response.json()


# ------------------------------ P3：SSE 流式 ------------------------------ #


def test_ask_stream_sensitive_rejected_via_sse():
    # 敏感问题在进入 Agent 前被拦截，SSE 流应直接给出 result 事件
    response = _client.post("/api/v1/ask_stream", json={"question": "查询所有用户的密码"})
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/event-stream")
    text = response.text
    assert text.startswith("data: ")
    assert '"type": "result"' in text
    assert "敏感" in text or "隐私" in text


def test_ask_stream_validates_question():
    response = _client.post("/api/v1/ask_stream", json={"question": "   "})
    assert response.status_code == 400


# ------------------------------ P3：用户反馈 ------------------------------ #


def test_feedback_rejects_invalid_rating():
    response = _client.post("/api/v1/feedback", json={"rating": "good", "question": "测试"})
    assert response.status_code == 400


def test_feedback_requires_question():
    response = _client.post("/api/v1/feedback", json={"rating": "up", "question": "  "})
    assert response.status_code == 400


def test_feedback_recorded_to_file():
    import tempfile

    original = app._FEEDBACK_FILE
    with tempfile.TemporaryDirectory() as tmp_dir:
        target = Path(tmp_dir) / "feedback.jsonl"
        app._FEEDBACK_FILE = target
        try:
            response = _client.post(
                "/api/v1/feedback",
                json={"rating": "up", "question": "一共有多少用户？", "sql": "SELECT COUNT(*) FROM user"},
            )
            assert response.status_code == 200
            assert response.json()["ok"] is True
            # 在临时目录清理前完成读取断言
            lines = target.read_text(encoding="utf-8").strip().splitlines()
        finally:
            app._FEEDBACK_FILE = original
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["rating"] == "up"
    assert record["question"] == "一共有多少用户？"


# ------------------------------ P3：API Key 认证 ------------------------------ #


def test_api_key_enforced_when_configured():
    original = app._API_KEYS
    app._API_KEYS = frozenset({"unit-test-key"})
    try:
        # 未携带密钥：/api/v1/* 一律 401
        response = _client.post("/api/v1/ask", json={"question": "你好"})
        assert response.status_code == 401
        # 错误密钥同样拒绝
        response = _client.post(
            "/api/v1/ask", json={"question": "你好"}, headers={"X-API-Key": "wrong"}
        )
        assert response.status_code == 401
        # 正确密钥放行（敏感拦截在 Agent 前，无需外部服务）
        response = _client.post(
            "/api/v1/ask",
            json={"question": "查询所有用户的密码"},
            headers={"X-API-Key": "unit-test-key"},
        )
        assert response.status_code == 200
        # 非 /api 路径（前端页面）不受密钥限制
        assert _client.get("/").status_code == 200
    finally:
        app._API_KEYS = original


# ------------------------------ P4：历史会话侧边栏 ------------------------------ #


def _seed_payload(question: str, answer: str) -> dict:
    """构造一轮完整回答 payload（结构与精简后的 /ask 响应一致）。"""
    return {
        "ok": True,
        "question": question,
        "answer": answer,
        "data": {"columns": [], "column_aliases": {}, "rows": [], "row_count": 0, "truncated": False},
        "chart": {"chartable": False, "chart_type": None, "reason": "", "column_kinds": []},
    }


def _seed_session(session_id: str = "testsession123"):
    app._history_store.append(
        session_id, "一共有多少用户？", _seed_payload("一共有多少用户？", "共 100 个用户。")
    )
    return session_id


def test_sessions_list_contains_seeded_session():
    session_id = _seed_session()
    try:
        response = _client.get("/api/v1/sessions")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["current_session_id"]
        ids = [s["session_id"] for s in payload["sessions"]]
        assert session_id in ids
        # 会话标题取自首条问题
        seeded = next(s for s in payload["sessions"] if s["session_id"] == session_id)
        assert seeded["title"] == "一共有多少用户？"
        assert seeded["message_count"] == 2
    finally:
        app._history_store.delete_session(session_id)


def test_session_messages_full_replay():
    session_id = _seed_session()
    try:
        response = _client.get(f"/api/v1/sessions/{session_id}/messages")
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "human"
        assert messages[0]["content"] == "一共有多少用户？"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["payload"]["answer"] == "共 100 个用户。"
    finally:
        app._history_store.delete_session(session_id)


def test_activate_switches_current_session():
    session_id = _seed_session()
    try:
        response = _client.post(f"/api/v1/sessions/{session_id}/activate")
        assert response.status_code == 200
        current = _client.get("/api/v1/sessions").json()["current_session_id"]
        assert current == session_id
    finally:
        app._history_store.delete_session(session_id)


def test_delete_session_removes_from_list():
    session_id = _seed_session()
    response = _client.delete(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 200
    ids = [s["session_id"] for s in _client.get("/api/v1/sessions").json()["sessions"]]
    assert session_id not in ids


def test_delete_active_session_creates_new_one():
    session_id = _seed_session()
    try:
        _client.post(f"/api/v1/sessions/{session_id}/activate")
        response = _client.delete(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200
        payload = response.json()
        # 删除的是当前活动会话：响应携带新会话 ID
        assert payload["session_id"]
        assert payload["session_id"] != session_id
        current = _client.get("/api/v1/sessions").json()["current_session_id"]
        assert current == payload["session_id"]
    finally:
        app._history_store.delete_session(session_id)


def test_sessions_reject_invalid_session_id():
    response = _client.get("/api/v1/sessions/" + "x" * 100 + "/messages")
    assert response.status_code == 400


def test_sessions_filtered_by_user_id():
    # 不同用户的会话隔离展示：/sessions?user_id=xxx 仅返回该用户的会话
    app._history_store.append("user-a-session", "alice 的问题", _seed_payload("alice 的问题", "回答A"), user_id="alice")
    app._history_store.append("user-b-session", "bob 的问题", _seed_payload("bob 的问题", "回答B"), user_id="bob")
    try:
        response = _client.get("/api/v1/sessions", params={"user_id": "alice"})
        assert response.status_code == 200
        ids = [s["session_id"] for s in response.json()["sessions"]]
        assert "user-a-session" in ids
        assert "user-b-session" not in ids
        # 不携带 user_id 时返回全部（兼容旧客户端）
        all_ids = [s["session_id"] for s in _client.get("/api/v1/sessions").json()["sessions"]]
        assert "user-a-session" in all_ids and "user-b-session" in all_ids
    finally:
        app._history_store.delete_session("user-a-session")
        app._history_store.delete_session("user-b-session")


def test_ask_rejects_overlong_user_id():
    response = _client.post(
        "/api/v1/ask", json={"question": "你好", "user_id": "u" * 100}
    )
    assert response.status_code == 400
    assert "user_id" in response.json()["error"]


# ---------------- 上游集成：双问题交给智能体与透传回显 ---------------- #


class _FakeQA:
    """捕获问题输入的测试桩（不依赖 LLM/数据库）。"""

    def __init__(self, captured: dict):
        self._captured = captured

    def ask(self, question, chat_history=None, resolved_question=None):
        self._captured["question"] = question
        self._captured["resolved_question"] = resolved_question
        return {
            "answer": "商品A 总销量最高。",
            "sql": None,
            "data": {
                "columns": ["name"],
                "rows": [["商品A"]],
                "row_count": 1,
                "truncated": False,
            },
            "intermediate_steps": [],
        }


def test_compose_user_input_leaves_choice_to_agent():
    # 两个问题一并交给智能体判断取舍；未提供/相同时直接用原始问题
    from smart_qa import SmartQA

    assert SmartQA._compose_user_input("一共有多少用户？", None) == "一共有多少用户？"
    assert SmartQA._compose_user_input("一共有多少用户？", "一共有多少用户？") == "一共有多少用户？"
    composed = SmartQA._compose_user_input(
        "其中哪个销量最高？", "在刚才查询的销量最高前10个商品中，哪个商品的总销量最高？"
    )
    assert "用户原始问题：其中哪个销量最高？" in composed
    assert "上游改写后的独立问题：在刚才查询的销量最高前10个商品中" in composed
    assert "请判断哪个问题可以直接使用" in composed


def test_ask_passes_both_questions_and_echoes_passthrough(monkeypatch):
    captured = {}
    monkeypatch.setattr(app, "get_qa", lambda: _FakeQA(captured))
    session_id = _client.post("/api/v1/new_chat").json()["session_id"]
    resolved = "在刚才查询的销量最高前10个商品中，哪个商品的总销量最高？"
    try:
        response = _client.post(
            "/api/v1/ask",
            json={
                "request_id": "REQ001",
                "conversation_id": "C001",
                "turn_id": "T001",
                "trace_id": "TR001",
                "user_id": "001",
                "question": "其中哪个销量最高？",
                "resolved_question": resolved,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        # 原始问题与改写问题一并下传，由智能体判断哪个可用作输入
        assert captured["question"] == "其中哪个销量最高？"
        assert captured["resolved_question"] == resolved
        assert payload["ok"] is True
        assert payload["question"] == "其中哪个销量最高？"
        # 透传参数原封不动回显
        assert payload["request_id"] == "REQ001"
        assert payload["conversation_id"] == "C001"
        assert payload["turn_id"] == "T001"
        assert payload["trace_id"] == "TR001"
        assert payload["user_id"] == "001"
        assert payload["resolved_question"] == resolved
    finally:
        app._history_store.delete_session(session_id)


def test_ask_without_new_fields_stays_slim(monkeypatch):
    # 未携带透传参数的旧客户端：响应不出现 null 占位字段
    captured = {}
    monkeypatch.setattr(app, "get_qa", lambda: _FakeQA(captured))
    session_id = _client.post("/api/v1/new_chat").json()["session_id"]
    try:
        response = _client.post("/api/v1/ask", json={"question": "一共有多少用户？"})
        payload = response.json()
        assert captured["question"] == "一共有多少用户？"
        assert captured["resolved_question"] is None
        for field in (
            "request_id", "conversation_id", "turn_id",
            "trace_id", "user_id", "resolved_question",
        ):
            assert field not in payload
    finally:
        app._history_store.delete_session(session_id)


def test_ask_echoes_extra_fields_and_skips_empty(monkeypatch):
    # 契约外字段有数据也原样回显；空值（null/空串）不回显；
    # 基础必回字段恒在，且不被同名传入字段覆盖
    captured = {}
    monkeypatch.setattr(app, "get_qa", lambda: _FakeQA(captured))
    session_id = _client.post("/api/v1/new_chat").json()["session_id"]
    try:
        response = _client.post(
            "/api/v1/ask",
            json={
                "question": "其中哪个销量最高？",
                "channel": "web",
                "seq_no": 3,
                "ext": {"source": "portal"},
                "request_id": "",
                "trace_id": None,
                "answer": "恶意覆盖",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        # 基础必回字段恒在
        for field in ("ok", "question", "answer", "data", "chart"):
            assert field in payload
        assert set(payload["data"]) == {
            "columns", "column_aliases", "rows", "row_count", "truncated"
        }
        assert set(payload["chart"]) == {
            "chartable", "chart_type", "reason", "column_kinds"
        }
        # 契约外字段有数据原样回显
        assert payload["channel"] == "web"
        assert payload["seq_no"] == 3
        assert payload["ext"] == {"source": "portal"}
        # 空值字段不回显
        assert "request_id" not in payload
        assert "trace_id" not in payload
        # 同名字段不能覆盖基础必回字段
        assert payload["answer"] != "恶意覆盖"
    finally:
        app._history_store.delete_session(session_id)


def test_ask_missing_question_rejected():
    # question 为唯一必填字段：缺失时 400
    response = _client.post("/api/v1/ask", json={"request_id": "REQ001"})
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_ask_sensitive_resolved_question_rejected():
    # 消解后的问题命中敏感词同样在 Agent 前拦截，透传参数照常回显
    response = _client.post(
        "/api/v1/ask",
        json={
            "question": "帮我看看",
            "resolved_question": "查询所有用户的密码",
            "request_id": "REQ002",
        },
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert "敏感" in payload["answer"] or "隐私" in payload["answer"]
    assert payload["request_id"] == "REQ002"


def test_ask_resolved_question_length_limit():
    response = _client.post(
        "/api/v1/ask",
        json={"question": "你好", "resolved_question": "长" * 600},
    )
    assert response.status_code == 400
    assert "resolved_question" in response.json()["error"]


def test_ask_stream_echoes_passthrough():
    # SSE 流式 result 事件同样回显透传参数（敏感拦截路径不依赖外部服务）
    response = _client.post(
        "/api/v1/ask_stream",
        json={
            "question": "查询所有用户的密码",
            "request_id": "REQ003",
            "trace_id": "TR003",
        },
    )
    assert response.status_code == 200
    assert '"request_id": "REQ003"' in response.text
    assert '"trace_id": "TR003"' in response.text


# ------------------------------ 列中文别名注入 ------------------------------ #


def test_build_ask_payload_includes_column_aliases():
    # 结果组装层应为英文列名注入中文别名，供前端图表/表头优先渲染
    result = {
        "answer": "总销售额 100 元。",
        "sql": "SELECT SUM(pay_amount) AS total_sales FROM orders",
        "data": {
            "columns": ["total_sales", "mystery_col"],
            "rows": [[100]],
            "row_count": 1,
            "truncated": False,
        },
        "intermediate_steps": [],
    }
    payload = app._build_ask_payload("销售额是多少？", result)
    aliases = payload["data"]["column_aliases"]
    assert aliases.get("total_sales") == "总销售额"
    # 无别名列不产生条目，前端回退原始列名（向后兼容）
    assert "mystery_col" not in aliases
    # 原有字段结构保持不变
    assert payload["data"]["columns"] == ["total_sales", "mystery_col"]
    assert payload["chart"]["chart_type"] == "bar"


def test_build_ask_payload_prefers_agent_aliases():
    # Agent 结合问题意图上报的别名优先于配置兜底
    result = {
        "answer": "销售额 100 元。",
        "sql": "SELECT SUM(pay_amount) AS total_sales FROM orders",
        "data": {
            "columns": ["total_sales"],
            "rows": [[100]],
            "row_count": 1,
            "truncated": False,
        },
        "suggested_column_aliases": {"total_sales": "销售额"},
        "intermediate_steps": [],
    }
    payload = app._build_ask_payload("分析一下销售额", result)
    assert payload["data"]["column_aliases"]["total_sales"] == "销售额"


def test_build_ask_payload_slim_structure():
    # 响应体只保留问答结果与绘图数据：SQL/过程/用量/耗时/错误占位一律不回传
    result = {
        "answer": "总销售额 100 元。",
        "sql": "SELECT SUM(pay_amount) AS total_sales FROM orders",
        "data": {
            "columns": ["total_sales"],
            "rows": [[100]],
            "row_count": 1,
            "truncated": False,
        },
        "intermediate_steps": [
            {"tool": "sql_db_execute", "input": {"query": "SELECT 1"}, "output": "x" * 400}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "tool_calls": 3},
        "duration_ms": 1234,
    }
    payload = app._build_ask_payload("销售额是多少？", result)
    assert set(payload) == {"ok", "question", "answer", "data", "chart"}
    assert set(payload["data"]) == {
        "columns", "column_aliases", "rows", "row_count", "truncated"
    }
    assert set(payload["chart"]) == {"chartable", "chart_type", "reason", "column_kinds"}
    # 回答正文按「数据表格 → 简短解释」组织，表头优先中文别名
    assert payload["answer"].startswith("| 总销售额 |\n|---|\n| 100 |")
    assert payload["answer"].endswith("总销售额 100 元。")


def test_build_ask_payload_answer_table_row_cap():
    # 内嵌表格最多 20 行，超出附行数提示，避免撑大回答
    result = {
        "answer": "共 50 行。",
        "data": {
            "columns": ["n"],
            "rows": [[i] for i in range(50)],
            "row_count": 50,
            "truncated": False,
        },
        "intermediate_steps": [],
    }
    payload = app._build_ask_payload("有多少行？", result)
    table_lines = [line for line in payload["answer"].splitlines() if line.startswith("|")]
    assert len(table_lines) == 22  # 表头 + 分隔行 + 20 数据行
    assert "仅展示前 20 行，共 50 行。" in payload["answer"]


def test_postprocess_answer_keeps_conclusion_only():
    # 回答后处理：剥离 sql 代码块、Markdown 表格、过程叙述与结尾客套，只留核心结论
    from smart_qa import SmartQA

    raw = (
        "根据查询结果，最近 7 天共新增 100 个用户。\n"
        "```sql\nSELECT COUNT(*) FROM users\n```\n"
        "我查询了 users 表并统计了数量。\n"
        "希望以上回答对您有帮助。"
    )
    assert SmartQA._postprocess_answer(raw) == "最近 7 天共新增 100 个用户。"
    # 模型自行输出的数据表格由系统统一嵌入，后处理剥离避免重复
    raw_table = "| 子阵 | 发电量 |\n|---|---|\n| C03 | 285600 |\n子阵C03 表现最优。"
    assert SmartQA._postprocess_answer(raw_table) == "子阵C03 表现最优。"
    # 正常结论不被误伤
    assert SmartQA._postprocess_answer("共 1,234 个用户。") == "共 1,234 个用户。"
    # 全文仅 sql 代码块时回退原文，不返回空回答
    assert SmartQA._postprocess_answer("```sql\nSELECT 1\n```") == "```sql\nSELECT 1\n```"


def test_extract_column_aliases_from_steps():
    # 取最后一次上报；兼容 dict 与 JSON 字符串两种工具输入形态
    from smart_qa import SmartQA

    steps = [
        {"tool": "sql_db_execute", "input": {"query": "SELECT 1"}, "output": "{}"},
        {"tool": "report_column_aliases", "input": {"aliases": {"a": "甲"}}, "output": "ok"},
        {"tool": "report_column_aliases", "input": json.dumps({"aliases": {"b": "乙"}}), "output": "ok"},
        {"tool": "report_column_aliases", "input": "not-json", "output": "ok"},
    ]
    assert SmartQA._extract_column_aliases(steps) == {"b": "乙"}
    assert SmartQA._extract_column_aliases([]) == {}


# ------------------------------ LLM 并发控制 ------------------------------ #


def test_ask_busy_when_llm_concurrency_exhausted():
    # 并发名额耗尽：快速返回 503「服务繁忙」（模拟本地模型推理服务过载）
    import threading

    original_semaphore, original_timeout = app._llm_semaphore, app._LLM_QUEUE_TIMEOUT
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()  # 占用唯一名额，模拟并发已满
    app._llm_semaphore = semaphore
    app._LLM_QUEUE_TIMEOUT = 1
    try:
        response = _client.post("/api/v1/ask", json={"question": "一共有多少用户？"})
        assert response.status_code == 503
        assert "繁忙" in response.json()["error"]
    finally:
        app._llm_semaphore, app._LLM_QUEUE_TIMEOUT = original_semaphore, original_timeout


def test_ask_allowed_when_concurrency_slot_available():
    # 未满员：请求正常执行（敏感拦截在 Agent 前，无需外部服务）
    import threading

    original_semaphore = app._llm_semaphore
    app._llm_semaphore = threading.BoundedSemaphore(1)
    try:
        response = _client.post("/api/v1/ask", json={"question": "查询所有用户的密码"})
        assert response.status_code == 200
        assert response.json()["ok"] is True
    finally:
        app._llm_semaphore = original_semaphore


def test_ask_stream_busy_when_llm_concurrency_exhausted():
    # 流式端点同样受限：满员时下发 error 事件后关流，不阻塞 HTTP 响应头
    import threading

    original_semaphore, original_timeout = app._llm_semaphore, app._LLM_QUEUE_TIMEOUT
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    app._llm_semaphore = semaphore
    app._LLM_QUEUE_TIMEOUT = 1
    try:
        response = _client.post(
            "/api/v1/ask_stream", json={"question": "一共有多少用户？"}
        )
        assert response.status_code == 200
        assert '"type": "error"' in response.text
        assert "繁忙" in response.text
    finally:
        app._llm_semaphore, app._LLM_QUEUE_TIMEOUT = original_semaphore, original_timeout


def test_max_tokens_from_env_parsing():
    # LLM_MAX_TOKENS：留空不限制；正整数生效；非法值启动即报错（ConfigError）
    import smart_qa

    original = os.environ.pop("LLM_MAX_TOKENS", None)
    try:
        assert smart_qa._max_tokens_from_env() is None
        os.environ["LLM_MAX_TOKENS"] = "2048"
        assert smart_qa._max_tokens_from_env() == 2048
        os.environ["LLM_MAX_TOKENS"] = "abc"
        try:
            smart_qa._max_tokens_from_env()
            raise AssertionError("非法 LLM_MAX_TOKENS 应抛 ConfigError")
        except smart_qa.ConfigError:
            pass
        os.environ["LLM_MAX_TOKENS"] = "0"
        try:
            smart_qa._max_tokens_from_env()
            raise AssertionError("非正数 LLM_MAX_TOKENS 应抛 ConfigError")
        except smart_qa.ConfigError:
            pass
    finally:
        os.environ.pop("LLM_MAX_TOKENS", None)
        if original is not None:
            os.environ["LLM_MAX_TOKENS"] = original


if __name__ == "__main__":
    import sys

    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}")
    print(f"\n共 {failures} 个失败" if failures else "\n全部通过")
    sys.exit(1 if failures else 0)
