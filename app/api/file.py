"""文件上传接口模块"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from app.services.vector_index_service import vector_index_service
from app.services.bm25_retrieval_service import bm25_retrieval_service
from app.services.vector_store_manager import vector_store_manager
from loguru import logger

router = APIRouter()

# 文件上传后存储的路径
UPLOAD_DIR = Path("./uploads")
# 支持的文件类型
ALLOWED_EXTENSIONS = ["txt", "md", "docx", "pdf", "xlsx"]
# 单个文件支持最大大小
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件并自动创建向量索引

    Args:
        file: 上传的文件

    Returns:
        JSONResponse: 上传结果
    """
    try:
        # 1. 验证文件
        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")

        # 2. 规范化文件名（去除空格，处理 Windows 上传的文件）
        safe_filename = _sanitize_filename(file.filename)

        # 3. 验证文件扩展名
        file_extension = _get_file_extension(safe_filename)
        if file_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式，仅支持: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        # 4. 创建上传目录
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

        # 5. 保存文件
        file_path = UPLOAD_DIR / safe_filename

        # 如果文件已存在，先删除旧文件（实现覆盖更新）
        if file_path.exists():
            logger.info(f"文件已存在，将覆盖: {file_path}")
            file_path.unlink()

        # 读取并保存文件内容
        content = await file.read()

        # 验证文件大小
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件大小超过限制（最大 {MAX_FILE_SIZE} 字节）")

        file_path.write_bytes(content)

        logger.info(f"文件上传成功: {file_path}")

        # 5. 自动创建向量索引
        try:
            logger.info(f"开始为上传文件创建向量索引: {file_path}")
            vector_index_service.index_single_file(str(file_path))
            logger.info(f"向量索引创建成功: {file_path}")
        except Exception as e:
            logger.error(f"向量索引创建失败: {file_path}, 错误: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"文件已保存，但知识库索引失败: {e}",
            ) from e

        # 6. 返回响应
        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success",
                "data": {
                    "filename": safe_filename,
                    "file_path": str(file_path),
                    "size": len(content),
                },
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {e}")


@router.post("/index_directory")
async def index_directory(directory_path: str = None):
    """
    索引指定目录下的所有文件

    Args:
        directory_path: 目录路径（可选，默认使用 uploads 目录）

    Returns:
        JSONResponse: 索引结果
    """
    try:
        logger.info(f"开始索引目录: {directory_path or 'uploads'}")

        # 执行索引
        result = vector_index_service.index_directory(directory_path)

        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success" if result.success else "partial_success",
                "data": result.to_dict(),
            },
        )

    except Exception as e:
        logger.error(f"索引目录失败: {e}")
        raise HTTPException(status_code=500, detail=f"索引目录失败: {e}")


@router.get("/knowledge/stats")
async def get_knowledge_stats():
    """返回已成功建立向量索引的知识库文档统计。"""
    try:
        stats = vector_store_manager.get_knowledge_stats()
        return JSONResponse(
            status_code=200,
            content={"code": 200, "message": "success", "data": stats},
        )
    except Exception as e:
        logger.error(f"获取知识库统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取知识库统计失败: {e}") from e


@router.get("/knowledge/documents")
async def list_knowledge_documents(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=100, description="每页文档数量"),
):
    """返回上传目录与向量库合并后的文档列表及索引状态。"""
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        indexed_documents = {
            document["source"]: document
            for document in vector_store_manager.list_indexed_documents()
        }
        documents = []

        for file_path in sorted(UPLOAD_DIR.iterdir(), key=lambda path: path.name.lower()):
            if not file_path.is_file() or _get_file_extension(file_path.name) not in ALLOWED_EXTENSIONS:
                continue

            resolved_path = file_path.resolve().as_posix()
            indexed = indexed_documents.pop(resolved_path, None)
            stat = file_path.stat()
            documents.append(
                {
                    "filename": file_path.name,
                    "source": resolved_path,
                    "extension": _get_file_extension(file_path.name),
                    "size": stat.st_size,
                    "updated_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
                    "status": "indexed" if indexed else "unindexed",
                    "chunk_count": indexed["chunk_count"] if indexed else 0,
                    "page_count": indexed["page_count"] if indexed else 0,
                    "sheets": indexed["sheets"] if indexed else [],
                }
            )

        # 物理文件被手工移除时保留记录，便于在管理页识别并清理残留向量。
        for indexed in indexed_documents.values():
            documents.append(
                {
                    "filename": indexed["file_name"],
                    "source": indexed["source"],
                    "extension": indexed["extension"],
                    "size": None,
                    "updated_at": None,
                    "status": "missing",
                    "chunk_count": indexed["chunk_count"],
                    "page_count": indexed["page_count"],
                    "sheets": indexed["sheets"],
                }
            )

        documents.sort(key=lambda document: document["filename"].lower())
        total = len(documents)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        paged_documents = documents[start:start + page_size]

        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success",
                "data": {
                    "documents": paged_documents,
                    "pagination": {
                        "page": page,
                        "page_size": page_size,
                        "total": total,
                        "total_pages": total_pages,
                    },
                },
            },
        )
    except Exception as e:
        logger.error(f"获取知识库文档列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取知识库文档列表失败: {e}") from e


@router.delete("/knowledge/documents/{filename}")
async def delete_knowledge_document(filename: str):
    """删除本地源文件及其对应的所有向量分片。"""
    try:
        file_path = _get_upload_file_path(filename)
        normalized_path = file_path.resolve().as_posix()
        deleted_chunks = vector_store_manager.delete_by_source(
            normalized_path,
            ignore_errors=False,
        )
        if file_path.exists():
            file_path.unlink()
        elif deleted_chunks == 0:
            raise HTTPException(status_code=404, detail="文档不存在或已删除")

        bm25_retrieval_service.refresh_from_milvus()

        logger.info(f"知识库文档已删除: {file_path}, 分片数={deleted_chunks}")
        return {"code": 200, "message": "success", "data": {"deleted_chunks": deleted_chunks}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除知识库文档失败: {filename}, 错误: {e}")
        raise HTTPException(status_code=500, detail=f"删除知识库文档失败: {e}") from e


@router.post("/knowledge/documents/{filename}/reindex")
async def reindex_knowledge_document(filename: str):
    """对已有源文件重新分割、嵌入并替换向量索引。"""
    try:
        file_path = _get_upload_file_path(filename)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="文档不存在，无法重新索引")

        vector_index_service.index_single_file(str(file_path))
        return {"code": 200, "message": "success", "data": {"filename": file_path.name}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重新索引失败: {filename}, 错误: {e}")
        raise HTTPException(status_code=500, detail=f"重新索引失败: {e}") from e


@router.post("/knowledge/documents/{filename}/replace")
async def replace_knowledge_document(filename: str, file: UploadFile = File(...)):
    """使用同类型新文件覆盖已有文档，并重建其向量索引。"""
    try:
        file_path = _get_upload_file_path(filename)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="原文档不存在，无法覆盖更新")
        if not file.filename:
            raise HTTPException(status_code=400, detail="请选择用于覆盖的文件")

        incoming_extension = _get_file_extension(_sanitize_filename(file.filename))
        current_extension = _get_file_extension(file_path.name)
        if incoming_extension != current_extension:
            raise HTTPException(
                status_code=400,
                detail=f"覆盖更新需保持文件类型一致（当前为 .{current_extension}）",
            )

        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件大小超过限制（最大 {MAX_FILE_SIZE} 字节）")

        file_path.write_bytes(content)
        vector_index_service.index_single_file(str(file_path))
        logger.info(f"知识库文档覆盖更新成功: {file_path}")
        return {"code": 200, "message": "success", "data": {"filename": file_path.name, "size": len(content)}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"覆盖更新失败: {filename}, 错误: {e}")
        raise HTTPException(status_code=500, detail=f"覆盖更新失败: {e}") from e


def _get_file_extension(filename: str) -> str:
    """
    获取文件扩展名

    Args:
        filename: 文件名

    Returns:
        str: 扩展名（小写，不含点）
    """
    parts = filename.rsplit(".", 1)
    if len(parts) == 2:
        return parts[1].lower()
    return ""


def _sanitize_filename(filename: str) -> str:
    """
    规范化文件名，去除空格和特殊字符

    Args:
        filename: 原始文件名

    Returns:
        str: 规范化后的文件名
    """
    # 去除空格
    sanitized = filename.replace(" ", "_")
    # 去除其他可能导致问题的字符
    for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        sanitized = sanitized.replace(char, "_")
    return sanitized


def _get_upload_file_path(filename: str) -> Path:
    """将 URL 中的文件名安全地映射为 uploads 下的文件路径。"""
    safe_filename = _sanitize_filename(filename)
    if safe_filename != filename or not safe_filename:
        raise HTTPException(status_code=400, detail="无效的文档名称")

    upload_dir = UPLOAD_DIR.resolve()
    file_path = (upload_dir / safe_filename).resolve()
    if file_path.parent != upload_dir:
        raise HTTPException(status_code=400, detail="无效的文档路径")
    return file_path
