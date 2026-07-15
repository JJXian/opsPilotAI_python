import fitz

from app.services.document_splitter_service import document_splitter_service


def test_split_pdf_uses_ocr_for_scanned_page(tmp_path, monkeypatch):
    file_path = tmp_path / "scanned.pdf"
    pdf = fitz.open()
    pdf.new_page()
    pdf.save(file_path)
    pdf.close()

    monkeypatch.setattr(
        document_splitter_service,
        "_ocr_pdf_page",
        lambda path, page_index: "扫描件中的运维手册内容",
    )

    documents = document_splitter_service.split_pdf(str(file_path))

    assert len(documents) == 1
    assert documents[0].page_content == "扫描件中的运维手册内容"
    assert documents[0].metadata["_page"] == 1
    assert documents[0].metadata["_extraction_method"] == "ocr"
