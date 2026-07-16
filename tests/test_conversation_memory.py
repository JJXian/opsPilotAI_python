from app.services.conversation_service import ConversationService


def test_extract_sources_deduplicates_source_markers():
    sources = ConversationService._extract_sources(
        "请参考【来源：payment-service.md / 第 2 页】和"
        "【来源：payment-service.md / 第 2 页】，另见【来源：SOP.pdf / 第 1 页】"
    )

    assert sources == [
        {"label": "payment-service.md / 第 2 页"},
        {"label": "SOP.pdf / 第 1 页"},
    ]


def test_make_title_normalizes_whitespace_and_limits_length():
    assert ConversationService._make_title("  payment-service   超时  ") == "payment-service 超时"
    assert len(ConversationService._make_title("x" * 40)) == 32


def test_compact_memory_content_preserves_head_tail_and_marks_omission():
    content = "A" * 30 + "B" * 30

    compacted = ConversationService._compact_memory_content(content, max_chars=30)

    assert compacted.startswith("A" * 20)
    assert compacted.endswith("B" * 10)
    assert "中间省略 30 个字符" in compacted
