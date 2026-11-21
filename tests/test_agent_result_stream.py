"""调研端点故障与知识问答质量闸的正文发布契约。"""
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessageChunk

from taozhi.ai.client import LLMClient
from taozhi.ai.router import CircuitBreakerRegistry, SensenovaEndpoint
from taozhi.ai.text_stream import OutputDeliveryError, stream_answer
from taozhi.application.rag import specialists
from taozhi.domain.rag_verification import cited_paragraphs


def test_wrapped_report_keeps_internal_code_only():
    """围栏与空白跨越分片时，草稿和归档正文完全一致。"""
    chunks = [" \n`", "``markdown\n  ", "# 结果\n", "```python\nx = 1\n```\n", "完毕\n`", "``\n "]
    parts = []
    message = stream_answer((AIMessageChunk(content=c) for c in chunks), parts.append, parts.clear)
    assert "".join(parts) == message.content == "# 结果\n```python\nx = 1\n```\n完毕"


def test_delivery_failure_does_not_retry_model():
    """持久化失败可能发生在提交后，直接终止本执行以避免重复正文或将端点熔断。"""
    client = LLMClient(registry=CircuitBreakerRegistry())
    def unavailable(_text):
        raise OSError("无法写入任务")
    try:
        with patch.object(client, "_client_for_endpoint") as provider, pytest.raises(OutputDeliveryError):
            provider.return_value.bind.return_value.stream.return_value = [AIMessageChunk(content="报告正文")]
            client.chat("调研", on_delta=unavailable, on_reset=lambda: None)
        provider.assert_called_once()
    finally:
        client.close()


def test_report_failover_replaces_partial_output():
    """候选端点在交付部分正文后断开，下一次尝试必须从空草稿开始。"""
    client = LLMClient(registry=CircuitBreakerRegistry())
    endpoints = [
        SensenovaEndpoint(endpoint_id=label, label=label, region="domestic", base_url="https://example.test", api_key="test", model="test")
        for label in ("primary", "secondary")
    ]
    events = []
    def first_stream(*args, **kwargs):
        yield AIMessageChunk(content="旧的草稿")
        raise TimeoutError("connect timeout")
    def second_stream(*args, **kwargs):
        assert events == ["旧的草稿", None]
        yield AIMessageChunk(content="新报告")
        yield AIMessageChunk(content="<thinking>不完整的思考")
    try:
        with (patch("taozhi.ai.client.build_sensenova_endpoints", return_value=endpoints),
              patch.object(client._registry, "select_candidates", return_value=endpoints),
              patch.object(client, "_client_for_endpoint") as provider):
            provider.return_value.bind.return_value.stream.side_effect = [first_stream(), second_stream()]
            report = client.chat("公开调研", on_delta=events.append, on_reset=lambda: events.append(None))
        assert events == ["旧的草稿", None, "新报告"]
        assert report == "新报告"
        assert [attempt["outcome"] for attempt in client.last_route_events] == ["failed", "ok"]
    finally:
        client.close()


@pytest.mark.parametrize("review,hard,expected_deltas", [(False, False, 2), (True, False, 0), (False, True, 0)])
def test_only_verified_answer_paragraphs_are_streamed(monkeypatch, review, hard, expected_deltas):
    """核验后的结论以最终引文格式发布，审核、硬阻断、重复与无依据结论不会进入草稿。"""
    evidence = [{"title": "规格表", "quote": "型号为甲，交期三天。", "level": 1}]
    source = [{"document_id": "doc"}]
    def finding(claim, quote):
        return {"role": "product", "claim": claim, "quote": quote, "evidence_index": 0}
    findings = [finding("型号为甲", "型号为甲"), finding("无依据", "无依据"), finding("型号为甲", "型号为甲"), finding("交期三天", "交期三天")]
    state = dict(job_id="task", run_version=2, question="请注明出处", hard_block=hard, selected_agents=["product"],
                 external_blocked=False, sources=source, evidence=evidence, findings=findings, business=[])
    monkeypatch.setattr(specialists, "validate_task_execution", lambda *_: (None, {"payload": {"require_review": review}}))
    deltas = []
    def publish(job_id, version, text, sources):
        assert (job_id, version, sources) == ("task", 2, source)
        deltas.append(text)
    monkeypatch.setattr(specialists, "publish_answer_delta", publish)
    verified = specialists.quality_node(state)
    answer = "".join(cited_paragraphs(state["question"], evidence, verified["findings"]))
    assert answer == "型号为甲（来源：《规格表》）[1]\n\n交期三天（来源：《规格表》）[1]"
    assert len(deltas) == expected_deltas
    if deltas:
        assert "".join(deltas) == answer
