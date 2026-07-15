from pathlib import Path

from app.services.document_splitter_service import document_splitter_service


def _write_text_pdf(path: Path, pages: list[str]) -> None:
    """生成极简文字 PDF，避免测试依赖额外的 PDF 生成库。"""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = []
    content_ids = []
    next_id = 3
    for _ in pages:
        page_ids.append(next_id)
        content_ids.append(next_id + 1)
        next_id += 2
    font_id = next_id
    objects.append(
        "<< /Type /Pages /Kids ["
        + " ".join(f"{page_id} 0 R" for page_id in page_ids)
        + f"] /Count {len(pages)} >>"
    )
    for page_id, content_id, text in zip(page_ids, content_ids, pages):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/MediaBox [0 0 612 792] /Contents {content_id} 0 R >>"
        )
        objects.append(f"<< /Length {len(content.encode('ascii'))} >>\nstream\n{content}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n{value}\nendobj\n".encode("ascii"))
    xref_offset = sum(len(chunk) for chunk in chunks)
    xref = [f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n" for offset in offsets[1:])
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    chunks.append("".join(xref).encode("ascii"))
    chunks.append(trailer.encode("ascii"))
    path.write_bytes(b"".join(chunks))


def test_split_pdf_preserves_page_number_and_text(tmp_path):
    file_path = tmp_path / "pricing.pdf"
    _write_text_pdf(file_path, ["Car cover price is 5399", "Car cover price is 6399"])

    documents = document_splitter_service.split_pdf(str(file_path))

    assert [item.metadata["_page"] for item in documents] == [1, 2]
    assert "5399" in documents[0].page_content
    assert "6399" in documents[1].page_content
    assert all(item.metadata["_extension"] == ".pdf" for item in documents)
    assert all(item.metadata["_extraction_method"] == "text" for item in documents)
