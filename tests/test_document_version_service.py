from app.services.document_version_service import DocumentVersionService


def test_only_active_version_is_visible_to_retrieval():
    service = DocumentVersionService()
    service._activate_in_snapshot("version-new", "version-old")

    assert service.is_active_version({"_version_id": "version-new"}) is True
    assert service.is_active_version({"_version_id": "version-old"}) is False
    # 升级前的历史分片没有版本元数据，保持兼容可见。
    assert service.is_active_version({"_source": "legacy.md"}) is True
