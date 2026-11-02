"""自研多智能体全栈设计规范验收测试（Multi-Agent Architecture Tests）。

覆盖：
1. 双轨记忆轨迹（Transcript Memory: compact vs full line-indexing）
2. 上下文压实引擎（Context Compaction & <CONTEXT_SUMMARY>）
3. 跨会话长效记忆与学习存储（LongTermMemoryStore & /learn）
4. 规范分层发现引擎（RulesEngine & <user_rules>）
5. 渐进式技能系统（SkillRegistry & <skills>）
6. 标准斜杠指令（SlashCommandRegistry & <slash_commands>）
7. 智能体生命周期管理器与级联销毁（SubagentManager）
8. 反应式定时调度器与条件取消（AgentScheduler）
9. 工件元数据与富排版辅助器（ArtifactMetadata, Alerts, Mermaid, Carousel）
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from taozhi.core.agent import (
    AgentScheduler,
    ArtifactMetadata,
    ContextCompactor,
    LongTermMemoryStore,
    Message,
    MessageBus,
    MessageType,
    RulesEngine,
    Skill,
    SkillRegistry,
    SlashCommandRegistry,
    SubagentContext,
    SubagentManager,
    SubagentResult,
    SubagentRunner,
    SubagentState,
    TranscriptLogger,
    WorkspaceMode,
)


def test_transcript_logger_dual_track(tmp_path: Path):
    """测试紧凑日志与全量日志双轨并行及行索引对齐。"""
    log_dir = tmp_path / ".system_generated" / "logs"
    logger = TranscriptLogger(log_dir)

    # 1. 记录短步骤
    s1 = logger.append_step(
        source="USER_EXPLICIT",
        step_type="USER_INPUT",
        content="帮我调研智能保温杯",
    )
    assert s1.step_index == 1

    # 2. 记录超长步骤（触发紧凑日志截断）
    long_content = "X" * 1200
    long_thinking = "T" * 800
    s2 = logger.append_step(
        source="MODEL",
        step_type="PLANNER_RESPONSE",
        content=long_content,
        thinking=long_thinking,
    )
    assert s2.step_index == 2

    # 校验紧凑文件
    compact_steps = logger.read_compact_steps()
    assert len(compact_steps) == 2
    assert "truncated_fields" in compact_steps[1]
    assert "content" in compact_steps[1]["truncated_fields"]
    assert "thinking" in compact_steps[1]["truncated_fields"]

    # 校验全量文件按行精确索引
    full_step_1 = logger.read_full_step_by_index(1)
    assert full_step_1["content"] == "帮我调研智能保温杯"

    full_step_2 = logger.read_full_step_by_index(2)
    assert full_step_2["content"] == long_content
    assert full_step_2["thinking"] == long_thinking
    assert "truncated_fields" not in full_step_2


def test_long_term_memory_and_rules(tmp_path: Path):
    """测试长效记忆库持久化学习规则与实体知识三元组。"""
    store = LongTermMemoryStore(storage_dir=tmp_path / "memory")

    # 1. 沉淀规则
    r1 = store.learn_rule("每次回复用户称呼为Senpe", category="style")
    assert r1["text"] == "每次回复用户称呼为Senpe"

    # 重复学习不应冗余追加
    store.learn_rule("每次回复用户称呼为Senpe", category="style")
    assert len(store.get_rules(category="style")) == 1

    # 2. 存储事实
    store.remember_fact("智能保温杯", "wholesale_min_price", 18.5)
    store.remember_fact("智能保温杯", "top_supplier", "永康市某某五金制品厂")

    facts = store.query_facts(subject="智能保温杯")
    assert len(facts) == 2

    # 3. 模糊检索
    res = store.search("Senpe")
    assert len(res["rules"]) == 1


def test_rules_engine_discovery_and_formatting(tmp_path: Path):
    """测试分层规范文档发现、去重与 <user_rules> 提示词格式化。"""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("# 交流原则\n每次回复叫用户Senpe\n", encoding="utf-8")

    sub_dir = root / "src" / "package"
    sub_dir.mkdir(parents=True)

    engine = RulesEngine(start_dir=sub_dir)
    discovered = engine.discover_rules()
    assert len(discovered) >= 1
    assert any("AGENTS.md" in str(d.path) for d in discovered)

    prompt_block = engine.build_rules_prompt_block()
    assert "<user_rules>" in prompt_block
    assert "</user_rules>" in prompt_block
    assert "Senpe" in prompt_block


def test_skill_registry_progressive_disclosure(tmp_path: Path):
    """测试技能解析、渐进式上下文生成与完整指令按需加载。"""
    skills_dir = tmp_path / ".agents" / "skills"
    sample_skill = skills_dir / "ecom-market-research"
    sample_skill.mkdir(parents=True)

    skill_md = sample_skill / "SKILL.md"
    skill_md.write_text(
        "---\nname: ecom-market-research\ndescription: 全网电商多维度市场调研技能\n---\n\n# 详细操作指南\n1. 调取搜索工具\n",
        encoding="utf-8",
    )

    registry = SkillRegistry(workspace_root=tmp_path)
    skill = registry.get("ecom-market-research")
    assert skill is not None
    assert skill.description == "全网电商多维度市场调研技能"
    assert "详细操作指南" in skill.read_instructions()

    prompt_block = registry.build_skills_prompt_block()
    assert "<skills>" in prompt_block
    assert "- ecom-market-research" in prompt_block


def test_slash_command_registry(tmp_path: Path):
    """测试斜杠指令解析、执行分发与 <slash_commands> 上下文生成。"""
    mem_store = LongTermMemoryStore(storage_dir=tmp_path / "mem")
    reg = SlashCommandRegistry(memory_store=mem_store)

    prompt_block = reg.build_slash_commands_prompt_block()
    assert "<slash_commands>" in prompt_block
    assert "- /compact:" in prompt_block
    assert "- /learn:" in prompt_block

    # 1. 解析并执行 /learn
    cmd, arg = reg.parse("/learn 推送到github一定要询问Senpe")
    assert cmd is not None
    res = cmd.execute(arg, {})
    assert res["status"] == "ok"
    assert len(mem_store.get_rules()) == 1

    # 2. 解析 /plan
    cmd2, arg2 = reg.parse("/plan 电商爆品拆解三步法")
    assert cmd2 is not None
    res2 = cmd2.execute(arg2, {})
    assert res2["phase"] == "planning"


def test_subagent_manager_and_cascade_kill(tmp_path: Path):
    """测试智能体生命周期登记、状态查询与级联终止。"""
    bus = MessageBus()
    mgr = SubagentManager(bus=bus)

    p_ctx = SubagentContext(conversation_id="parent_01", root_dir=tmp_path)
    c_ctx = SubagentContext(conversation_id="child_01", parent_id="parent_01", root_dir=tmp_path)

    mgr.register_subagent(p_ctx, type_name="main_agent", role="调研协同总指挥")
    mgr.register_subagent(c_ctx, type_name="web_agent", role="数据采集", parent_id="parent_01")

    assert len(mgr.list_subagents()) == 2

    # 发送交互消息
    ok = mgr.send_input("child_01", "请优先关注天猫销量前五")
    assert ok is True

    # 级联销毁父级，子级也应当被同步取消
    killed = mgr.kill_subagent("parent_01")
    assert killed is True

    status_c = mgr.get_status("child_01")
    assert status_c["state"] == SubagentState.FAILED.value


def test_agent_scheduler_timer_and_condition_cancel():
    """测试反应式定时器调度与基于条件变量的提前取消。"""
    bus = MessageBus()
    scheduler = AgentScheduler(bus=bus)

    # 1. 注册一个单次定时器（0.3秒后唤醒）
    job_id = scheduler.schedule_timer("定时检测库存水位", duration_seconds=1)

    # 反应式等待接收通知
    msg = bus.wait_for_message("coordinator", timeout=2.5)
    assert msg is not None
    assert msg.sender_id == "scheduler"
    assert msg.content == "定时检测库存水位"

    # 2. 测试条件取消（TimerCondition）
    job_id_2 = scheduler.schedule_timer(
        "若无消息则超时告警",
        duration_seconds=5,
        timer_condition="sensor_subagent",
    )

    bus.post(
        Message(
            sender_id="sensor_subagent",
            recipient_id="coordinator",
            msg_type=MessageType.PROGRESS,
            content="传感器数据已送达",
        )
    )

    time.sleep(0.1)
    jobs = scheduler.list_jobs()
    job_entry = next((j for j in jobs if j["jobId"] == job_id_2), None)
    assert job_entry is not None
    assert job_entry["isCancelled"] is True


def test_context_artifact_formatting_helpers(tmp_path: Path):
    """测试工件 Markdown 富排版（Alerts, Mermaid, Carousel, FileLink）。"""
    ctx = SubagentContext(conversation_id="art_test", root_dir=tmp_path)

    # 1. Alerts
    alert_md = ctx.build_alert("IMPORTANT", "出刀战报中的 roleId 需与 siege.roleId 做类型兼容转换")
    assert "> [!IMPORTANT]" in alert_md

    # 2. Mermaid
    mermaid_md = ctx.build_mermaid("flowchart TD", "A[用户请求] --> B[总协调]")
    assert "```flowchart TD" in mermaid_md

    # 3. Carousel
    carousel_md = ctx.build_carousel(["Slide 1: 前期调研", "Slide 2: 方案落地"])
    assert "````carousel" in carousel_md
    assert "<!-- slide -->" in carousel_md

    # 4. File Link
    link = ctx.format_file_link(tmp_path / "summary.md", start_line=10, end_line=25)
    assert "file://" in link
    assert "#L10-L25" in link

    # 5. 带元数据的工件产出
    meta = ArtifactMetadata(
        summary="竞品分析交付报告",
        user_facing=True,
        request_feedback=True,
    )
    art_path = ctx.write_artifact("report.md", "# 交付报告\n", metadata=meta)
    assert art_path.exists()
    meta_path = art_path.parent / "report.md.meta.json"
    assert meta_path.exists()
    meta_json = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta_json["RequestFeedback"] is True


def test_subagent_runner_full_architecture(tmp_path: Path):
    """测试 SubagentRunner 统合双轨日志、工件元数据与管理器的全生命周期调度。"""
    mock_llm = MagicMock()
    mock_llm.chat.return_value = "【验真结论】：毛利率达 71.9%，供应链可信度高。"

    runner = SubagentRunner(llm_client=mock_llm)
    res = runner.invoke_subagent(
        type_name="verifier_agent",
        role="验真分析师",
        prompt="验证【智能水杯】零售价 89 元与 1688 批发价 25 元的利润空间",
    )

    assert res.state == SubagentState.COMPLETED
    assert len(res.artifacts) >= 1
    assert "毛利率达 71.9%" in res.summary

    # 检查日志是否已写入
    log_dir = Path(res.artifacts[0]).parent.parent / ".system_generated" / "logs"
    logger = TranscriptLogger(log_dir)
    steps = logger.read_compact_steps()
    assert len(steps) >= 2  # USER_INPUT + PLANNER_RESPONSE
