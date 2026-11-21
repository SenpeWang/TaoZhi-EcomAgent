"""一次性统一已采集节点的 OpenInference 属性和执行链路索引。"""
from alembic import op

revision = "015_span_semantics"
down_revision = "014_task_spans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保留已知的调用数据，同名属性优先采用已写入的标准字段。"""
    op.execute("""
        UPDATE task_spans SET attributes =
            jsonb_strip_nulls(jsonb_build_object(
                'llm.model_name', attributes->'gen_ai.request.model',
                'llm.token_count.prompt', attributes->'gen_ai.usage.input_tokens',
                'llm.token_count.completion', attributes->'gen_ai.usage.output_tokens',
                'agent.name', attributes->'gen_ai.agent.name',
                'tool.name', attributes->'tool'
            )) || (attributes - ARRAY[
                'gen_ai.request.model','gen_ai.usage.input_tokens','gen_ai.usage.output_tokens',
                'gen_ai.agent.name','gen_ai.operation.name','tool'
            ])
        WHERE attributes ?| ARRAY[
            'gen_ai.request.model','gen_ai.usage.input_tokens','gen_ai.usage.output_tokens',
            'gen_ai.agent.name','gen_ai.operation.name','tool'
        ]
    """)
    op.execute("""UPDATE task_spans SET attributes=attributes-'subagent_id'
                  WHERE name='research.subagent_worker' AND attributes ? 'subagent_id'""")
    op.execute("""UPDATE task_spans SET kind='CHAIN' WHERE name='pipeline.retrieval' AND kind='RETRIEVER'""")
    op.execute("""UPDATE task_spans SET name='mcp.call_tool' WHERE kind='TOOL'
                  AND name='mcp.tool.' || (attributes->>'tool.name')""")
    op.execute("""UPDATE task_spans SET name='rag.specialist' WHERE kind='AGENT'
                  AND name='rag.specialist.' || (attributes->>'agent.name')""")
    op.execute("DROP INDEX idx_task_spans_run")
    op.execute("CREATE INDEX idx_task_spans_run ON task_spans(tenant_id,job_id,run_version,trace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_spans_trace_page ON task_spans(trace_id,id)")


def downgrade() -> None:
    """恢复上一版查询所用的字段、操作名和索引。"""
    op.execute("DROP INDEX idx_task_spans_trace_page")
    op.execute("DROP INDEX idx_task_spans_run")
    op.execute("CREATE INDEX idx_task_spans_run ON task_spans(tenant_id,job_id,run_version,id)")
    op.execute("""UPDATE task_spans SET name='rag.specialist.' || (attributes->>'agent.name')
                  WHERE name='rag.specialist' AND attributes ? 'agent.name'""")
    op.execute("""UPDATE task_spans SET name='mcp.tool.' || (attributes->>'tool.name')
                  WHERE name='mcp.call_tool' AND attributes ? 'tool.name'""")
    op.execute("""UPDATE task_spans SET kind='RETRIEVER' WHERE name='pipeline.retrieval' AND kind='CHAIN'""")
    op.execute("""
        UPDATE task_spans SET attributes =
            (attributes - ARRAY['llm.model_name','llm.token_count.prompt','llm.token_count.completion','agent.name','tool.name'])
            || jsonb_strip_nulls(jsonb_build_object(
                'gen_ai.request.model', attributes->'llm.model_name',
                'gen_ai.usage.input_tokens', attributes->'llm.token_count.prompt',
                'gen_ai.usage.output_tokens', attributes->'llm.token_count.completion',
                'gen_ai.agent.name', attributes->'agent.name',
                'tool', attributes->'tool.name'
            ))
        WHERE attributes ?| ARRAY['llm.model_name','llm.token_count.prompt','llm.token_count.completion','agent.name','tool.name']
    """)
