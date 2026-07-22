# DevPilot 智能研发知识库与 Bugfix 系统

> 面向研发知识问答与代码缺陷排查场景的 AI 助手，支持 RAG 知识库问答、来源引用、流式对话和日志驱动的 Bugfix Agent。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-latest-orange.svg)](https://www.langchain.com/)

## 核心特性

- **RAG 智能问答**：支持文档上传、自动切分、Embedding 向量化、Milvus 索引、上下文组装和回答来源引用。
- **混合检索增强**：实现 Dense Retrieval + BM25、RRF 排名融合与可配置 Reranker 精排，提升错误码、日志路径、配置项等精确标识符检索效果。
- **Bugfix Agent**：基于 Plan-Execute-Replan 工作流，从 Python 或 Spring Boot/Java 异常堆栈出发，自动解析日志、定位代码、读取上下文、搜索相关源码并生成修复建议。
- **智能对话体验**：支持 ReAct 风格工具调用、知识库优先模式、多轮上下文管理、异常容错和 SSE 流式输出。
- **持久化记忆**：使用 PostgreSQL 保存会话、消息和摘要记忆，使用 LangGraph Checkpointer 持久化 Agent 执行状态。
- **兼容诊断工具**：保留日志、监控和历史 AIOps 诊断相关接口，用于兼容既有排障流程；当前项目主定位为研发知识库问答与 Bugfix Agent。

## 技术栈

- **后端框架**：FastAPI
- **Agent 框架**：LangChain、LangGraph
- **LLM**：阿里云 DashScope / 通义千问
- **向量库**：Milvus
- **关系数据库**：PostgreSQL
- **检索链路**：Embedding、BM25、RRF、Reranker
- **文档解析**：python-docx、pypdf、PyMuPDF、RapidOCR、python-calamine
- **工具协议**：MCP
- **评测体系**：RAGAS、Context Precision / Recall、Hit Rate、MRR

## 快速开始

### 环境要求

- Python 3.11+
- Docker / Docker Compose
- 阿里云 DashScope API Key

### Linux / macOS

```bash
# 1. 克隆项目
git clone <repository_url>
cd super_biz_agent_py

# 2. 安装依赖
pip install uv
uv venv
source .venv/bin/activate
uv pip install -e .

# 3. 编辑 .env，填入 DASHSCOPE_API_KEY
vim .env

# 4. 初始化依赖服务并上传示例知识库文档
make init

# 5. 启动服务
make start
```

### Windows

```powershell
# 创建虚拟环境并安装依赖
pip install uv
uv venv
.venv\Scripts\activate
uv pip install -e .

# 编辑 .env
notepad .env

# 启动所有服务
.\start-windows.bat
```

### 访问地址

- Web 界面：http://localhost:9900
- API 文档：http://localhost:9900/docs

## 配置说明

```env
# DashScope
DASHSCOPE_API_KEY=your-api-key
DASHSCOPE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-max

# Milvus
MILVUS_HOST=localhost
MILVUS_PORT=19530

# PostgreSQL
DATABASE_URL=postgresql://opspilot:opspilot@localhost:5432/opspilot

# RAG
RAG_TOP_K=3
CHUNK_MAX_SIZE=800
CHUNK_OVERLAP=100

# Bugfix Agent
BUGFIX_REPOSITORY_ROOT=.
BUGFIX_MAX_SEARCH_RESULTS=12
# 可选：服务器日志源白名单。服务进程通过既有 SSH Agent/密钥认证连接，客户端不会传入 SSH 地址或命令。
BUGFIX_LOG_SOURCES=[{"id":"prod-api","name":"生产 API","host":"10.0.0.8","user":"ops","log_path":"/var/log/api/error.log","tail_lines":800}]
```

## API 接口

| 功能 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 普通对话 | POST | `/api/chat` | 一次性返回 RAG 问答结果 |
| 流式对话 | POST | `/api/chat_stream` | SSE 流式输出 |
| Bugfix Agent | POST | `/api/bugfix` | 根据异常堆栈或预配置服务器日志生成定位与修复建议 |
| Bugfix 日志源 | GET | `/api/bugfix/log-sources` | 获取可选择的服务器日志源（不泄露地址与路径） |
| 文件上传 | POST | `/api/upload` | 上传文档并建立知识库索引 |
| 知识库文档 | GET | `/api/documents` | 获取已索引文档列表 |
| 会话列表 | GET | `/api/chat/sessions` | 分页获取持久化会话 |
| 会话消息 | GET | `/api/chat/session/{session_id}` | 获取会话历史 |
| 删除会话 | DELETE | `/api/chat/session/{session_id}` | 删除消息、摘要和 Agent Checkpoint |
| 健康检查 | GET | `/api/health` | 服务状态检查 |
| 兼容诊断接口 | POST | `/api/aiops` | 历史 AIOps 诊断接口，保留兼容 |

### RAG 问答

```bash
curl -X POST "http://localhost:9900/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"ERR-PAY-504 应该如何排查？"}'
```

### 流式对话

```bash
curl -X POST "http://localhost:9900/api/chat_stream" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"查询 payment-service 的日志路径和排查 SOP"}' \
  --no-buffer
```

### Bugfix Agent

```bash
curl -N -X POST "http://localhost:9900/api/bugfix" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "bugfix-demo",
    "include_diff": true,
    "log": "Traceback (most recent call last):\n  File \"app/services/example.py\", line 42, in run\n    result = items[0]\nIndexError: list index out of range"
  }'
```

Bugfix Agent 输出包含：

- 异常类型、堆栈文件、行号和函数名解析结果
- 可疑代码片段和相关源码搜索结果
- Git 与测试文件线索
- 根因分析、影响范围和修复建议
- 可选建议 Diff，默认只展示，不会修改文件

### 从服务器自动读取日志

在 `.env` 中配置 `BUGFIX_LOG_SOURCES` 后，Bugfix 面板可选择日志源；Agent 会以服务进程已有的 SSH 凭据只读执行 `tail`，再继续原有的解析、定位、读上下文、搜索源码、根因分析和修复建议流程。也可以直接调用：

```bash
curl -N -X POST "http://localhost:9900/api/bugfix" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"bugfix-prod","log_source_id":"prod-api","include_diff":true}'
```

日志源只能在服务端白名单中配置；接口不接受任意主机、路径、Shell 命令或写入操作。建议为该 SSH 账号授予目标日志文件的最小只读权限。

## RAG 离线评测

项目内置离线检索评测流程，用于对比纯向量检索与 Hybrid RAG（BM25 + Embedding + RRF）。评测使用来源文件名作为稳定相关文档 ID，重新上传或重建索引后仍可复现。

```bash
# 首次安装评测依赖
uv sync --extra eval

# 运行评测，需要 Milvus 已启动且示例知识库文档已入库
make eval-rag

# 使用其他 TopK
.venv/bin/python -m app.evaluation --top-k 5
```

报告会写入 `evaluation/reports/`，同时生成 JSON 与 Markdown。核心指标：

- `Context Precision`：召回来源中有多少是人工标注的相关文档。
- `Context Recall`：人工标注的相关来源是否被成功召回。
- `Hit Rate`：TopK 中是否至少命中一条相关来源。
- `MRR`：第一条相关来源的排名质量。

## 项目结构

```text
super_biz_agent_py/
├── app/
│   ├── main.py                          # FastAPI 应用入口
│   ├── api/
│   │   ├── chat.py                      # RAG 对话接口
│   │   ├── bugfix.py                    # Bugfix Agent SSE 接口
│   │   ├── aiops.py                     # 历史兼容诊断接口
│   │   ├── file.py                      # 文档上传与知识库管理
│   │   └── health.py                    # 健康检查
│   ├── services/
│   │   ├── rag_agent_service.py         # ReAct 风格 RAG Agent
│   │   ├── agentic_rag_service.py       # Agentic RAG 服务
│   │   ├── bugfix_service.py            # Bugfix Agent 状态图与报告生成
│   │   ├── bugfix_tools.py              # 只读代码分析工具
│   │   ├── hybrid_retrieval_service.py  # 混合检索与 RRF 融合
│   │   ├── reranker_service.py          # 精排服务
│   │   └── conversation_service.py      # 会话记忆与摘要压缩
│   ├── tools/
│   │   ├── knowledge_tool.py            # 知识库检索工具
│   │   ├── query_metrics_alerts.py      # 兼容监控告警查询工具
│   │   └── time_tool.py                 # 时间工具
│   ├── core/
│   │   ├── database.py                  # PostgreSQL 连接管理
│   │   ├── checkpointer.py              # LangGraph Checkpointer
│   │   ├── llm_factory.py               # LLM 工厂
│   │   └── milvus_client.py             # Milvus 客户端
│   └── evaluation/                       # RAG 检索评测
├── static/                               # Web 前端
├── docs/                                 # 学习指南与项目文档
├── aiops-docs/                           # 示例知识库文档，包含历史排障资料
├── mcp_servers/                          # 兼容日志/监控 MCP 工具服务
├── tests/                                # 单元测试
├── vector-database.yml                   # Milvus Docker Compose 配置
├── pyproject.toml
└── README.md
```

## Bugfix Agent 工作流

```text
1. Parse    解析 Python Traceback，提取异常类型、文件、行号和函数名
2. Plan     生成代码定位与证据收集计划
3. Execute  执行只读代码工具：读取片段、搜索源码、发现测试、查看 Git 线索
4. Replan   根据证据决定继续补充上下文或进入报告生成
5. Report   输出根因分析、影响范围、修复建议和可选建议 Diff
```

安全边界：

- 只读取 `BUGFIX_REPOSITORY_ROOT` 内的文件。
- 拒绝 `../` 等路径越界访问。
- 建议 Diff 仅作为报告内容展示，不会自动写入项目文件。

## 会话记忆持久化

`make up` 会同时启动 Milvus 和 PostgreSQL。会话历史保存在 PostgreSQL，而非浏览器 `localStorage`；刷新页面或重启 FastAPI 后仍可继续对话。

普通对话会按“会话摘要 + 最近消息 + 当前问题”组装上下文。消息数量达到阈值后，系统会将较早记录压缩为摘要，以控制上下文长度和模型调用成本。

LangGraph Checkpointer 会保存 RAG Agent、Bugfix Agent 以及兼容诊断流程的执行状态，支持服务重启后的流程恢复。

## 常用命令

```bash
make init              # 一键初始化 Docker、服务和示例知识库
make start             # 启动所有服务
make stop              # 停止所有服务
make restart           # 重启所有服务

make install-dev       # 安装开发依赖
make sync              # 同步依赖
make up                # 启动 Docker 容器
make down              # 停止 Docker 容器
make format            # 格式化代码
make lint              # 代码检查
make test              # 运行测试
make eval-rag          # 运行 RAG 检索评测
```

## 相关文档

- [Bug 修复 Agent 学习指南](docs/bugfix_agent_学习指南.md)
- [MCP 工具服务说明](mcp_servers/README.md)
