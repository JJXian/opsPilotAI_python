from app.services.bm25_retrieval_service import Bm25RetrievalService


def test_bm25_retrieves_chinese_keywords_and_exact_error_codes():
    service = Bm25RetrievalService()
    service.refresh(
        [
            (
                "database",
                "数据库连接池耗尽时，先检查活跃连接数和慢 SQL。",
                {"_file_name": "database-runbook.md"},
            ),
            (
                "oracle",
                "ORA-12514 表示监听器当前不知道请求的服务名。",
                {"_file_name": "oracle-runbook.md"},
            ),
        ]
    )

    results = service.search("ORA-12514 怎么处理", top_k=2)

    assert results[0].document.id == "oracle"
    # 小语料中 rank-bm25 的 IDF 允许为 0，但精确错误码仍必须被保留为候选。
    assert results[0].score >= 0
