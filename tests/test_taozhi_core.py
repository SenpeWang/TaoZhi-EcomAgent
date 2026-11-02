"""淘智核心引擎与工具管线单元测试。"""

from taozhi.core.tools.truncate import truncate_text
from taozhi.core.tools.accumulator import OutputAccumulator
from taozhi.core.session.projection import build_session_projection
from taozhi.core.compaction.cut_point import is_cut_point_message, find_safe_cut_index


def test_truncate_text_line_boundary():
    # 模拟多行文本
    lines = [f"line_{i}\n" for i in range(100)]
    content = "".join(lines)

    # 截断到 10 行
    res = truncate_text(content, max_lines=10, max_bytes=10000)
    assert res.truncated is True
    assert res.truncated_by == "lines"
    assert res.output_lines == 10
    assert res.content.endswith("line_9\n")  # 严格保持行边界，不截断中间行


def test_output_accumulator_spill():
    accum = OutputAccumulator(max_lines=5, max_bytes=100)
    for i in range(20):
        accum.append(f"message number {i}\n")

    snapshot = accum.snapshot(persist_if_truncated=True)
    assert snapshot.truncation.truncated is True
    assert snapshot.full_output_path is not None  # 验证大输出自动落盘临时文件
    import os
    assert os.path.exists(snapshot.full_output_path)
    os.remove(snapshot.full_output_path)


def test_build_session_projection():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "type": "TOOL_RESULT", "content": "A" * 300},
        {"role": "tool", "type": "TOOL_RESULT", "content": "B" * 300},
        {"role": "tool", "type": "TOOL_RESULT", "content": "C" * 300},
        {"role": "tool", "type": "TOOL_RESULT", "content": "D" * 300},
        {"role": "assistant", "content": "done"},
    ]
    # 保留最近 2 个工具，历史更早的工具输出被静态瘦身
    projected = build_session_projection(messages, max_retained_tool_steps=2)
    assert len(projected) == len(messages)
    assert "pruned for token efficiency" in projected[1]["content"]
    assert "pruned for token efficiency" in projected[2]["content"]
    assert projected[3]["content"] == "C" * 300
    assert projected[4]["content"] == "D" * 300


def test_cut_point_safety():
    assert is_cut_point_message({"role": "user", "type": "USER_INPUT"}) is True
    assert is_cut_point_message({"role": "assistant", "type": "PLANNER_RESPONSE"}) is True
    assert is_cut_point_message({"role": "tool", "type": "TOOL_RESULT"}) is False