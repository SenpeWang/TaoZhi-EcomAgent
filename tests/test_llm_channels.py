"""多模型通道构建的单元测试：百炼通道按配置启用并按优先级加入候选池。"""
from __future__ import annotations

from types import SimpleNamespace

from taozhi.ai.router import build_sensenova_endpoints


def _settings(**overrides):
    base = dict(
        base_url="https://token.sensenova.cn/v1",
        api_key="test-primary",
        model="sensenova-6.8-flash",
        llm_direct_connection=True,
        sensenova_cn_secondary_api_key="",
        sensenova_overseas_base_url="https://token.sensenova.ai/v1",
        sensenova_overseas_api_key="",
        sensenova_overseas_model="sensenova-6.8-flash",
        sensenova_overseas_direct_connection=False,
        dots_base_url="https://note3-prev-api.askdiandian.com/v1",
        dots_api_key="",
        dots_model="dots3-note-prev",
        dots_direct_connection=True,
        bailian_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        bailian_api_key="",
        bailian_model="deepseek-v4.1-flash",
        bailian_direct_connection=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_bailian_channel_appears_when_key_configured():
    """配置百炼密钥后，通道以国内区域、指定模型加入候选池。"""
    eps = build_sensenova_endpoints(_settings(bailian_api_key="test-bailian"))
    ids = [e.endpoint_id for e in eps]
    assert "bailian" in ids
    channel = next(e for e in eps if e.endpoint_id == "bailian")
    assert channel.model == "deepseek-v4.1-flash"
    assert channel.region == "domestic"
    assert channel.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_bailian_channel_skipped_without_key():
    """未配置密钥时该通道不进入候选池，避免无效重试。"""
    eps = build_sensenova_endpoints(_settings(bailian_api_key=""))
    assert "bailian" not in [e.endpoint_id for e in eps]