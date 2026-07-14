"""文档分割服务模块 - 基于 LangChain 的智能文档分割"""

from pathlib import Path
from typing import Iterator, List, Union

from docx import Document as DocxDocument
from docx.document import Document as DocxDocumentType
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger
from pypdf import PdfReader

from app.config import config


class DocumentSplitterService:
    """文档分割服务 - 使用 LangChain 的分割器"""

    MAX_PDF_PAGES = 200

    def __init__(self):
        """初始化文档分割服务"""
        self.chunk_size = config.chunk_max_size
        self.chunk_overlap = config.chunk_overlap

        # Markdown 标题分割器 (只按一级和二级标题分割，减少分片数)
        self.markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
                # 不再按三级标题分割，避免过度碎片化
            ],
            strip_headers=False,  # 保留标题在内容中
        )

        # 递归字符分割器 (用于二次分割，使用更大的chunk_size)
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size * 2,  # 加倍chunk_size，减少分片数
            chunk_overlap=self.chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

        logger.info(
            f"文档分割服务初始化完成, chunk_size={self.chunk_size}, "
            f"secondary_chunk_size={self.chunk_size * 2}, "
            f"overlap={self.chunk_overlap}"
        )

    def split_markdown(
        self, content: str, file_path: str = "", extension: str = ".md"
    ) -> List[Document]:
        """
        分割 Markdown 文档 (两阶段分割 + 合并小片段)

        Args:
            content: Markdown 内容
            file_path: 文件路径 (用于元数据)

        Returns:
            List[Document]: 文档分片列表
        """
        if not content or not content.strip():
            logger.warning(f"Markdown 文档内容为空: {file_path}")
            return []

        try:
            # 第一阶段: 按标题分割
            md_docs = self.markdown_splitter.split_text(content)

            # 第二阶段: 按大小进一步分割
            docs_after_split = self.text_splitter.split_documents(md_docs)

            # 第三阶段: 合并太小的分片 (< 300字符)
            final_docs = self._merge_small_chunks(docs_after_split, min_size=300)

            # 添加文件路径元数据
            for doc in final_docs:
                doc.metadata["_source"] = file_path
                doc.metadata["_extension"] = extension
                doc.metadata["_file_name"] = Path(file_path).name

            logger.info(f"Markdown 分割完成: {file_path} -> {len(final_docs)} 个分片")
            return final_docs

        except Exception as e:
            logger.error(f"Markdown 分割失败: {file_path}, 错误: {e}")
            raise

    def split_text(self, content: str, file_path: str = "") -> List[Document]:
        """
        分割普通文本文档

        Args:
            content: 文本内容
            file_path: 文件路径 (用于元数据)

        Returns:
            List[Document]: 文档分片列表
        """
        if not content or not content.strip():
            logger.warning(f"文本文档内容为空: {file_path}")
            return []

        try:
            # 直接使用递归字符分割器
            docs = self.text_splitter.create_documents(
                texts=[content],
                metadatas=[
                    {
                        "_source": file_path,
                        "_extension": Path(file_path).suffix,
                        "_file_name": Path(file_path).name,
                    }
                ],
            )

            logger.info(f"文本分割完成: {file_path} -> {len(docs)} 个分片")
            return docs

        except Exception as e:
            logger.error(f"文本分割失败: {file_path}, 错误: {e}")
            raise

    def split_docx(self, file_path: str) -> List[Document]:
        """提取 DOCX 的标题、正文和表格，并按标题结构切分。"""
        try:
            document = DocxDocument(file_path)
            parts: list[str] = []

            for block in self._iter_docx_blocks(document):
                if isinstance(block, Paragraph):
                    text = block.text.strip()
                    if not text:
                        continue

                    heading_level = self._get_docx_heading_level(block)
                    if heading_level:
                        parts.append(f"{'#' * heading_level} {text}")
                    else:
                        parts.append(text)
                else:
                    table_text = self._format_docx_table(block)
                    if table_text:
                        parts.append(table_text)

            content = "\n\n".join(parts)
            if not content:
                logger.warning(f"DOCX 文档内容为空: {file_path}")
                return []

            return self.split_markdown(content, file_path, extension=".docx")
        except Exception as e:
            logger.error(f"DOCX 解析失败: {file_path}, 错误: {e}")
            raise

    def split_pdf(self, file_path: str) -> List[Document]:
        """提取文字版 PDF 的每页文本，并保留页码元数据。"""
        try:
            reader = PdfReader(file_path)
            if reader.is_encrypted:
                raise ValueError("不支持加密 PDF，请先移除密码后再上传")

            page_count = len(reader.pages)
            if page_count > self.MAX_PDF_PAGES:
                raise ValueError(
                    f"PDF 页数超过限制（最多 {self.MAX_PDF_PAGES} 页），请拆分后再上传"
                )

            documents: list[Document] = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if not text:
                    logger.warning(f"PDF 第 {page_number} 页未提取到文字: {file_path}")
                    continue

                page_documents = self.text_splitter.create_documents(
                    texts=[text],
                    metadatas=[
                        {
                            "_source": file_path,
                            "_extension": ".pdf",
                            "_file_name": Path(file_path).name,
                            "_page": page_number,
                        }
                    ],
                )
                documents.extend(page_documents)

            if not documents:
                raise ValueError(
                    "未从 PDF 提取到可检索文字；该文件可能是扫描件图片 PDF，"
                    "当前版本不支持 OCR"
                )

            logger.info(
                f"PDF 分割完成: {file_path} -> {len(documents)} 个分片，"
                f"共 {page_count} 页"
            )
            return documents
        except Exception as e:
            logger.error(f"PDF 解析失败: {file_path}, 错误: {e}")
            raise

    @staticmethod
    def _iter_docx_blocks(document: DocxDocumentType) -> Iterator[Union[Paragraph, Table]]:
        """按文档中的原始顺序遍历段落和表格，避免表格被统一挪到文末。"""
        for child in document.element.body.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, document)
            elif isinstance(child, CT_Tbl):
                yield Table(child, document)

    @staticmethod
    def _get_docx_heading_level(paragraph: Paragraph) -> int | None:
        """从 Word 内置标题样式推断 Markdown 标题级别。"""
        style_name = paragraph.style.name.lower()
        prefixes = ("heading", "标题")
        prefix = next((item for item in prefixes if style_name.startswith(item)), None)
        if prefix is None:
            return None

        level = style_name.removeprefix(prefix).strip()
        return int(level) if level.isdigit() else 1

    @staticmethod
    def _format_docx_table(table: Table) -> str:
        """将表格转成带表头的文本记录，便于向量检索定位字段和值。"""
        rows = [
            [cell.text.strip().replace("\n", " ") for cell in row.cells]
            for row in table.rows
        ]
        rows = [row for row in rows if any(row)]
        if not rows:
            return ""

        headers = rows[0]
        lines = ["表格："]
        for row in rows[1:]:
            cells = [
                f"{headers[index] or f'列{index + 1}'}：{value}"
                for index, value in enumerate(row)
                if value
            ]
            if cells:
                lines.append("；".join(cells))

        # 单行表格通常没有数据行，保留该行内容以免丢失信息。
        if len(lines) == 1:
            lines.append("；".join(value for value in headers if value))
        return "\n".join(lines)

    def split_document(self, content: str, file_path: str = "") -> List[Document]:
        """
        智能分割文档 (根据文件类型选择分割器)

        Args:
            content: 文档内容
            file_path: 文件路径

        Returns:
            List[Document]: 文档分片列表
        """
        if file_path.endswith(".md"):
            return self.split_markdown(content, file_path)
        else:
            return self.split_text(content, file_path)

    def _merge_small_chunks(
        self, documents: List[Document], min_size: int = 300
    ) -> List[Document]:
        """
        合并太小的分片

        Args:
            documents: 文档列表
            min_size: 最小分片大小 (字符数)

        Returns:
            List[Document]: 合并后的文档列表
        """
        if not documents:
            return []

        merged_docs = []
        current_doc = None

        for doc in documents:
            doc_size = len(doc.page_content)

            if current_doc is None:
                # 第一个文档
                current_doc = doc
            elif doc_size < min_size and len(current_doc.page_content) < self.chunk_size * 2:
                # 当前文档太小且合并后不会太大，则合并
                current_doc.page_content += "\n\n" + doc.page_content
                # 保留主文档的元数据
            else:
                # 保存当前文档，开始新文档
                merged_docs.append(current_doc)
                current_doc = doc

        # 添加最后一个文档
        if current_doc is not None:
            merged_docs.append(current_doc)

        return merged_docs


# 全局单例
document_splitter_service = DocumentSplitterService()
