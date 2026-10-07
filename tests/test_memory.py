"""会话记忆 / 摘要压缩 / FAQ 语义缓存 单元测试（不依赖真实 LLM 与远端 embedding）。"""

import json

import pytest

from ecom_copilot.config import get_settings
from ecom_copilot.llm import HashingEmbedding
from ecom_copilot.memory import (
    HistoryInjector,
    SessionStore,
    Summarizer,
    estimate_tokens,
    get_faq_cache,
    get_session_store,
)
from ecom_copilot.memory.cache import FaqCache
from ecom_copilot.schemas.research import ResearchReport, ResearchTask


# ───────────────────────── 工具 ─────────────────────────


class _FakeSettings:
    """Summarizer 专用假配置：不读全局 env，行为确定。"""

    def __init__(self, has_llm: bool = False, threshold: int = 3000) -> None:
        self.has_llm = has_llm
        self.memory_summary_threshold = threshold


class _StubLLM:
    def chat(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        return "【偏好场景】膜切机客户【已确认结论】MC-500 切幅 320mm（MC500规格书）【未决问题】C5 适配【用户纠错】无"


def _long_history(rounds: int = 8, chars: int = 300) -> list:
    """构造超过摘要阈值的假历史（每条约 1800 汉字 ≈ 1080 token）。"""
    history = []
    for i in range(rounds):
        history.append({"role": "user", "content": f"第{i}轮问题：" + "膜切机参数细节" * chars})
        history.append({"role": "assistant", "content": f"第{i}轮回答：" + "官方口径如下" * chars})
    return history


# ───────────────────────── SessionStore ─────────────────────────


def test_session_store_add_get_clear(tmp_path):
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    assert store.backend == "sqlite"
    store.add_message("u1", "user", "MC-500 切幅多少", user_id="u1", org_tag="客服组", task_id="t1")
    store.add_message("u1", "assistant", "MC-500 切幅 320mm（[0]）", user_id="u1", task_id="t1")
    store.add_message("u1", "user", "那它支持切 C5 吗", user_id="u1", task_id="t2")

    full = store.get_history("u1")
    assert [m["role"] for m in full] == ["user", "assistant", "user"]  # 时间正序
    assert all("created_at" in m and m["content"] for m in full)

    recent = store.get_history("u1", limit=2)
    assert [m["role"] for m in recent] == ["assistant", "user"]  # 最近 2 条且保持正序

    assert store.clear_session("u1") == 3
    assert store.get_history("u1") == []
    assert store.add_message("u1", "system", "非法角色") is False


def test_session_store_jsonl_fallback(tmp_path):
    db_dir = tmp_path / "broken-db"
    db_dir.mkdir()  # sqlite3.connect 打开目录必然失败 → 降级 JSONL
    store = SessionStore(db_path=db_dir, jsonl_path=tmp_path / "sessions.jsonl")
    assert store.backend == "jsonl"
    store.add_message("u2", "user", "UV 打印机堵头怎么办")
    store.add_message("u2", "assistant", "先执行喷头清洗流程")
    store.add_message("u3", "user", "别的问题")  # 其他 session 不应串扰

    history = store.get_history("u2")
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert store.get_history("u3")[0]["content"] == "别的问题"
    assert store.clear_session("u2") == 2
    assert store.get_history("u2") == []
    assert store.get_history("u3"), "清除 u2 不应影响 u3"


def test_get_session_store_singleton():
    assert get_session_store() is get_session_store()


# ───────────────────────── 摘要压缩 Summarizer ─────────────────────────


def test_estimate_tokens_heuristic():
    assert estimate_tokens("你好世界") == int(4 * 0.6)  # 中文按 字数*0.6
    assert estimate_tokens("hello world") == 2  # 英文按词数
    assert estimate_tokens("") == 0


def test_summarizer_below_threshold_keeps_history():
    summarizer = Summarizer(_FakeSettings(has_llm=False, threshold=10 ** 9))
    history = [{"role": "user", "content": "短问题"}, {"role": "assistant", "content": "短回答"}]
    assert summarizer.maybe_compress(history) == history


def test_summarizer_truncation_without_llm():
    """超阈值且 LLM 不可用：降级为仅保留最近 4 轮截断。"""
    summarizer = Summarizer(_FakeSettings(has_llm=False, threshold=100))
    history = _long_history(rounds=8)
    result = summarizer.maybe_compress(history)
    assert len(result) == 8  # 最近 4 轮 × (user+assistant)
    assert result[-1]["content"] == history[-1]["content"]
    assert not any("[历史摘要]" in m["content"] for m in result)


def test_summarizer_compress_with_llm(monkeypatch):
    """超阈值且有 LLM：旧轮次压缩为一条 [历史摘要] 消息 + 最近 2 轮原文。"""
    import ecom_copilot.memory.summarizer as sm

    monkeypatch.setattr(sm, "get_llm", lambda: _StubLLM())
    summarizer = Summarizer(_FakeSettings(has_llm=True, threshold=100))
    history = _long_history(rounds=6)
    result = summarizer.maybe_compress(history)
    assert result[0]["role"] == "assistant"
    assert result[0]["content"].startswith("[历史摘要]")
    assert len(result) == 5  # 1 条摘要 + 最近 2 轮 × 2 条
    assert result[-1]["content"] == history[-1]["content"]


def test_summarizer_llm_failure_degrades(monkeypatch):
    """LLM 调用抛异常：静默降级为截断，绝不抛出。"""
    import ecom_copilot.memory.summarizer as sm

    def _boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(sm, "get_llm", _boom)
    summarizer = Summarizer(_FakeSettings(has_llm=True, threshold=100))
    history = _long_history(rounds=6)
    result = summarizer.maybe_compress(history)
    assert 0 < len(result) < len(history)
    assert not any("[历史摘要]" in m["content"] for m in result)


# ───────────────────────── HistoryInjector ─────────────────────────


def test_history_injector_build_and_empty(tmp_path, monkeypatch):
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    store.add_message("u1", "user", "MC-500 和 MC-300 有什么区别")
    store.add_message("u1", "assistant", "MC-500 切幅 320mm，MC-300 切幅 210mm（[0]）")
    monkeypatch.setattr("ecom_copilot.memory.session.get_session_store", lambda: store)

    block = HistoryInjector.build("u1", "那它支持切 C5 吗")
    assert block.startswith("【会话历史】(近 1 轮)")
    assert "用户：MC-500 和 MC-300 有什么区别" in block
    assert "助手：MC-500 切幅 320mm" in block
    assert HistoryInjector.build("no-such-session", "x") == ""


def test_history_injector_compression_triggers(tmp_path, monkeypatch):
    """超长假历史 + LLM 不可用：注入走截断降级，块内轮数被压缩。"""
    import ecom_copilot.memory.summarizer as sm

    def _boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(sm, "get_llm", _boom)
    store = SessionStore(db_path=tmp_path / "long.sqlite")
    for i in range(8):
        store.add_message("u9", "user", f"第{i}轮问题：" + "膜切机参数细节" * 300)
        store.add_message("u9", "assistant", f"第{i}轮回答：" + "官方口径如下" * 300)
    monkeypatch.setattr("ecom_copilot.memory.session.get_session_store", lambda: store)

    block = HistoryInjector.build("u9", "继续追问")
    assert block.startswith("【会话历史】(近 4 轮)")  # 8 轮被压缩为最近 4 轮
    assert "第7轮" in block and "第0轮" not in block


# ───────────────────────── FAQ 语义缓存 ─────────────────────────


@pytest.fixture()
def faq_cache(tmp_path, monkeypatch):
    """降级后端（HashingEmbedding）驱动的 FaqCache，避免真实远端 embedding。"""
    monkeypatch.setattr("ecom_copilot.memory.cache.get_embedding_provider",
                        lambda *a, **k: HashingEmbedding(dim=64))
    return FaqCache(db_path=tmp_path / "faq_cache.sqlite")


def test_faq_cache_put_and_lookup_hit(faq_cache):
    citations = [{"index": 0, "doc_name": "MC500 规格书", "quote": "切幅 320mm", "source": "spec_sheet"}]
    assert faq_cache.put("MC-500 膜切机的切幅参数是多少", "MC-500 切幅 320mm（[0]）", citations) is True
    hit = faq_cache.lookup("MC-500 膜切机的切幅参数是多少")
    assert hit is not None
    assert hit["answer"] == "MC-500 切幅 320mm（[0]）"
    assert hit["score"] >= get_settings().faq_cache_sim_threshold
    assert hit["citations"][0]["doc_name"] == "MC500 规格书"


def test_faq_cache_lookup_miss(faq_cache):
    faq_cache.put("MC-500 膜切机的切幅参数是多少", "切幅 320mm", [])
    assert faq_cache.lookup("今天附近有哪些餐厅推荐") is None  # 语义无关 → 未命中


def test_faq_cache_overwrite_same_question(faq_cache):
    faq_cache.put("C3 防窥膜的防窥角度", "30 度", [])
    faq_cache.put("C3 防窥膜的防窥角度", "更新：防窥角 30 度（[2]）", [{"index": 2}])
    assert faq_cache.count() == 1  # 同问题覆盖更新而非重复写入
    assert faq_cache.lookup("C3 防窥膜的防窥角度")["answer"].startswith("更新")


def test_faq_cache_disabled_degrades_silently(tmp_path):
    broken = tmp_path / "not-a-db-dir"
    broken.mkdir()  # 连接目录必然失败 → 缓存禁用
    cache = FaqCache(db_path=broken)
    assert cache.enabled is False
    assert cache.lookup("任意问题") is None
    assert cache.put("任意问题", "答案", []) is False
    assert cache.count() == 0


def test_get_faq_cache_singleton():
    assert get_faq_cache() is get_faq_cache()


# ───────────────────────── workflow 层集成（纯函数级单测） ─────────────────────────


def test_workflow_cache_hit_skips_pipeline(monkeypatch):
    """FAQ 缓存命中：构造最小成功 state，答案带缓存标注与原引用。"""
    from ecom_copilot.agents import workflow as wf_mod
    from ecom_copilot.agents.workflow import ResearchWorkflow

    class _StubCache:
        def lookup(self, question, sim_threshold=None):
            if "MC-500" in question:
                return {"question": question, "answer": "MC-500 切幅 320mm",
                        "citations": [{"index": 0, "doc_name": "MC500 规格书", "quote": "切幅 320mm"}],
                        "score": 0.97}
            return None

    monkeypatch.setattr(wf_mod, "get_faq_cache", lambda: _StubCache())
    wf = ResearchWorkflow.__new__(ResearchWorkflow)  # 绕过检索引擎等重初始化
    wf.settings = get_settings()

    state = wf._cache_hit_state(ResearchTask(question="MC-500 的切幅参数"))
    assert state is None  # Legacy global FAQ lacks ACL/revision; cannot bypass publication gate.
    assert wf._cache_hit_state(ResearchTask(question="完全无关的其他问题")) is None


def test_workflow_record_turn_writes_session(tmp_path, monkeypatch):
    """任务完成写回本轮 Q-A，且正常答案回写 FAQ 缓存。"""
    from ecom_copilot.agents import workflow as wf_mod
    from ecom_copilot.agents.workflow import ResearchWorkflow

    class _PutSpyCache:
        def __init__(self):
            self.calls = []

        def put(self, question, answer, citations):
            self.calls.append((question, answer, citations))
            return True

    spy = _PutSpyCache()
    store = SessionStore(db_path=tmp_path / "turn.sqlite")
    monkeypatch.setattr(wf_mod, "get_session_store", lambda: store)
    monkeypatch.setattr(wf_mod, "get_faq_cache", lambda: spy)
    wf = ResearchWorkflow.__new__(ResearchWorkflow)
    wf.settings = get_settings()

    task = ResearchTask(question="MC-500 切幅多少", user_id="u-turn", org_tag="客服组")
    task.status = __import__("ecom_copilot.schemas.research",fromlist=["TaskStatus"]).TaskStatus.COMPLETED
    wf._record_turn(task, {"report": ResearchReport(executive_summary="MC-500 切幅 320mm（[0]）")})

    history = store.get_history(wf_mod._session_scope(task))
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "MC-500 切幅多少"), ("assistant", "MC-500 切幅 320mm（[0]）")]
    assert len(spy.calls) == 0

    # 拒答报告：写会话但不回写 FAQ 缓存
    refusal_report = ResearchReport(title="暂时无法回答", executive_summary="抱歉，暂时无法回答")
    wf._record_turn(task, {"report": refusal_report})
    assert len(spy.calls) == 0
