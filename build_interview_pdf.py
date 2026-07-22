from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, KeepTogether
from reportlab.pdfbase.pdfmetrics import stringWidth
from pathlib import Path

OUT = Path('output/pdf')
OUT.mkdir(parents=True, exist_ok=True)
PDF = OUT / 'DevPilot智能研发系统_项目面试准备手册.pdf'

pdfmetrics.registerFont(TTFont('CN', '/Users/jjxian/Library/Fonts/simfang.ttf'))
pdfmetrics.registerFont(TTFont('CNBold', '/System/Library/Fonts/STHeiti Medium.ttc', subfontIndex=0))

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='CoverTitle', fontName='CNBold', fontSize=25, leading=34, alignment=TA_CENTER, textColor=colors.HexColor('#12355B'), spaceAfter=10*mm))
styles.add(ParagraphStyle(name='CoverSub', fontName='CN', fontSize=12, leading=20, alignment=TA_CENTER, textColor=colors.HexColor('#4B6178')))
styles.add(ParagraphStyle(name='H1C', fontName='CNBold', fontSize=17, leading=25, textColor=colors.HexColor('#12355B'), spaceBefore=8*mm, spaceAfter=4*mm, keepWithNext=True))
styles.add(ParagraphStyle(name='H2C', fontName='CNBold', fontSize=13, leading=20, textColor=colors.HexColor('#0B6E8A'), spaceBefore=5*mm, spaceAfter=2*mm, keepWithNext=True))
styles.add(ParagraphStyle(name='Q', fontName='CNBold', fontSize=12, leading=19, textColor=colors.HexColor('#152D4F'), spaceBefore=4*mm, spaceAfter=2*mm, keepWithNext=True))
styles.add(ParagraphStyle(name='BodyC', fontName='CN', fontSize=9.6, leading=16, textColor=colors.HexColor('#202A35'), spaceAfter=2.2*mm))
styles.add(ParagraphStyle(name='Small', fontName='CN', fontSize=8.5, leading=13, textColor=colors.HexColor('#4B5563'), spaceAfter=1.6*mm))
styles.add(ParagraphStyle(name='Label', fontName='CNBold', fontSize=9.6, leading=15, textColor=colors.HexColor('#7C3E00'), spaceBefore=1.3*mm, spaceAfter=0.8*mm))
styles.add(ParagraphStyle(name='TOC', fontName='CN', fontSize=10.5, leading=20, textColor=colors.HexColor('#263B50')))

def P(s, style='BodyC'): return Paragraph(s, styles[style])
def section(title, intro=None):
    out=[P(title,'H1C')]
    if intro: out += [P(intro,'BodyC')]
    return out
def question(n, title, answer, extra='', principle=''):
    out=[P(f'{n}. {title}', 'Q'), P('<b>【参考回答】</b>'+answer,'BodyC')]
    if extra: out += [P('<b>【建议补充】</b>'+extra,'Small')]
    if principle: out += [P('<b>【通用原理】</b>'+principle,'Small')]
    return out

story=[]
story += [Spacer(1,32*mm), P('DevPilot 智能研发系统', 'CoverTitle'), P('项目面试准备手册｜Java 后端 / 全栈 / AI 应用 / Agent 开发', 'CoverSub'), Spacer(1,8*mm), P('候选人背景：硕士 · 约 1 年工作经验 · 目标：杭州中大厂', 'CoverSub'), Spacer(1,20*mm), P('使用方式：先背“90 秒项目介绍”，再按岗位重点复习题库。所有带“建议补充”的地方，请用你的真实实现替换；不要把未知数据当作既成事实。', 'BodyC'), PageBreak()]

story += section('目录')
for x in ['1. 项目定位与 90 秒开场','2. 项目全链路拆解','3. 核心题库：Agent 与 RAG','4. 核心题库：检索、评测与数据','5. 核心题库：工程、架构与可靠性','6. 核心题库：Bugfix Agent','7. 数据库、高并发与 Java 迁移','8. 弱点、质疑点与复习清单']:
    story.append(P(x,'TOC'))
story.append(PageBreak())

story += section('1. 项目定位与 90 秒开场')
story += [P('<b>项目一句话：</b>DevPilot 是面向金融研发团队的智能知识库与 Bugfix Agent：前者用可追溯 RAG 回答企业研发知识，后者根据异常堆栈把排障过程组织成可恢复、可审计的多步工作流。','BodyC'),
P('<b>你在项目中的主线：</b>不是“调用了大模型”，而是负责将不可靠的企业文档与不确定的模型输出，收敛为“检索可评测、回答有引用、流程可恢复”的工程系统。','BodyC')]
story += question('1.1','请用 90 秒介绍这个项目。','我做的是 DevPilot 智能研发系统，服务金融研发场景的知识查询和故障排查。它有两条主链路：第一条是企业知识库问答。我们把文档、排障手册、错误码说明和代码资料经过解析、OCR、分块、向量化后存进 Milvus，并通过 Dense Retrieval、BM25、RRF 和 Reranker 组成混合检索，让错误码、配置项和日志路径这类精确实体也能命中。回答阶段强制基于检索证据生成，并返回来源引用。第二条是 Bugfix Agent：输入异常日志和 Python 堆栈后，Agent 按“解析、定位、读上下文、搜索相关源码、分析根因、输出修复建议”的计划执行闭环。<br/>我主要负责 RAG 核心链路、LangGraph 的 Agentic RAG 工作流、180 条离线评测与检索优化，以及 PostgreSQL Checkpointer 的会话和状态持久化。在 Top-3 检索口径下，Hybrid RAG 的 Context Recall 为 0.918、Hit Rate 为 0.885、MRR 为 0.823。这个项目让我最关注的不是模型回答得像不像，而是证据是否找全、回答是否可追溯、失败时系统能否稳定降级和恢复。','建议准备：文档量、知识库数量、日均/峰值请求、模型与 embedding/reranker 选型、上线范围。面试若追问“指标怎么来的”，一定能说明 180 条样本的标注口径和 top-k。','好的项目介绍按“场景痛点-系统机制-本人职责-量化结果-工程价值”组织，避免按技术名词堆砌。')
story += question('1.2','你的个人贡献到底是什么，如何证明？','我的贡献可以拆成四块可验证交付。第一，完成从文档入库到带引用回答的 RAG 主链路，接口输入、索引结构、检索融合、上下文组装和引用映射都在闭环内。第二，用 LangGraph 把意图路由、检索、证据评估、改写重检和最终生成显式建模，能看到每一状态的输入输出，而不是一段难以调试的 prompt。第三，建立 180 条覆盖五类研发问题的评测集，用 Recall、Hit Rate、MRR 驱动检索策略选择。第四，完成 Bugfix Agent 的计划执行状态持久化，使重启后可以恢复会话和未完成任务。证明方式不是说“我参与”，而是我能说清每个模块的接口、失败分支、指标口径和我做出的具体取舍。','请补齐：代码仓库中你提交/主导的模块名、接口路径、评测脚本、改造前后数据。若无法证明“主导”，诚实改为“负责其中的……”。')

story += section('2. 项目全链路拆解','面试画图建议：先画在线问答主链，再补离线入库、状态存储和 Bugfix Agent。')
for s in ['离线：文档/图片 → 解析与 OCR → 清洗去重 → 分块与元数据 → Embedding + BM25 → Milvus/倒排索引','在线：请求 → 会话恢复 → 意图路由 → Dense + BM25 → RRF → Reranker → 证据评估/改写 → 基于证据回答 + 引用 → SSE','排障：日志/堆栈 → 计划 → 文件定位/读上下文/源码搜索 → 根因假设 → 建议 Diff → 状态检查点']:
    story.append(P('• '+s,'BodyC'))
story += question('2.1','为什么要做 Agentic RAG，而不是一次检索后直接回答？','一次检索的假设是用户 query 足够明确、首轮召回足够好。但研发场景里“启动报错怎么办”这类问题常缺少版本、模块或错误码；直接回答会把低质量上下文包装成确定结论。我的工作流先路由意图，再混合检索；LLM 只负责判断当前证据是否足以回答、缺哪些关键信息。证据不足时改写 query 并重检，达到次数上限或仍无证据就明确说明无法从知识库确认，而不是编造。这样把模型的开放式生成限制在证据约束内，也把重试策略变成可观测的状态机。','要补齐：证据充分性判定的 prompt/结构化字段、最大重检次数和何时反问用户。','Agentic RAG 的价值是“检索控制与质量闸门”，不是为了增加调用轮次；必须设最大步数、超时和回退。')
story += question('2.2','什么是 Agent？与大模型有什么本质不同？','我理解 Agent 本质上是一个围绕目标执行“感知、决策、行动、反馈”的系统。大模型本身更像概率语言模型：给定上下文输出下一个 token，并不天然拥有持久状态、工具权限、任务终止条件或执行闭环。Agent 则在模型外补上了规划、工具调用、记忆、环境反馈和控制流。以我的项目为例，LLM 负责理解日志、提出根因假设或评估证据；LangGraph 负责规定什么时候检索、什么时候改写、何时停止，工具负责读文件和搜索代码，Checkpointer 负责恢复中断状态。因此 Agent 的“自主性”不是放任模型自由发挥，而是受状态、权限、预算和观测约束的自主执行。','若面试官问“自主”是否过度宣传，回答：我们是受控工作流 Agent，不宣称开放环境的完全自主。','ReAct 是“思考-行动-观察”循环；Plan-Execute-Replan 更强调先有显式计划、执行后按反馈重规划。')

story += section('3. 核心题库：Agent 与 RAG')
story += question('3.1','LangGraph 相比 LangChain 的作用是什么？','LangChain 更适合快速封装模型、检索器和工具；LangGraph 在这个项目里解决的是有状态、多分支、可中断恢复的控制流。我把状态定义为 query、意图、检索结果、证据评估、改写次数、消息历史和错误信息；节点分别是路由、检索、评估、改写、生成。条件边根据证据是否充分、是否超过重试上限、是否发生错误选择下一步。这样一来每轮执行都有确定状态，调试时可以定位在“召回差、重排差还是评估误判”，而且 Checkpointer 可以保存节点间状态用于续执行。','建议补齐实际 State 字段与图中节点数量；最好能手绘状态转移图。','工作流编排不是提示词拼接：状态 schema、幂等节点、终止条件和异常分支是生产可用的关键。')
story += question('3.2','多轮会话如何避免上下文无限增长？','我把长期可恢复的会话状态存 PostgreSQL，通过 LangGraph Checkpointer 挂到 thread/session 维度；模型上下文不直接塞全量历史。近期若干轮保留原文保证连续性，较早内容压缩成摘要；每次请求再结合当前问题和检索证据组装有限窗口。摘要会保留用户目标、已确认的版本/模块/约束和未解决问题，不保留无关闲聊。这样既控制 token 成本和延迟，也避免旧对话把当前检索方向带偏。','请确认：窗口轮数、摘要触发阈值、session/thread 主键、TTL/清理策略；不清楚时不要编数字。','会话记忆分为存储记忆与模型上下文。持久化不等于每次都注入 prompt。')
story += question('3.3','如何做到回答带来源引用，并防止“假引用”？','检索结果从进入上下文起就携带稳定的 chunk_id、文档 ID、页码或段落位置、标题和得分。组装 prompt 时把证据编号为 [S1]、[S2]，要求模型只引用这些编号；输出后由服务端把编号映射回真实元数据，而不是让模型生成链接。对于没有足够证据的结论，提示词要求明确标注不确定或追问。更严格时我会做后处理校验：剔除不在候选集合中的引用，并检查每个关键结论是否至少绑定一条来源。','请确认：PDF 页码、网页 URL、文档版本等元数据是否真实存在；说明引用颗粒度是“文档”还是“chunk”。','引用解决可追溯性，不自动证明蕴含关系；高风险场景需要回答-证据一致性评估或人工审核。')
story += question('3.4','强制知识库模式是什么？什么时候使用？','强制知识库模式是把回答知识边界收紧：模型只能基于本次检索到的企业资料回答，资料不足就说“知识库没有足够依据”，不使用自身常识补全。它适合 SOP、内部配置、错误码和合规敏感信息，因为这些内容版本化且不能凭经验猜。普通技术咨询可允许“知识库证据 + 通用建议”分层输出，但必须清楚标记哪部分来自内部资料。','建议补齐：强制模式的 API 参数、无命中返回格式、是否允许追问。','这是 grounding / abstention 策略；关键是产品协议和服务端校验，不应只依赖 prompt。')
story += question('3.5','SSE 流式输出的难点有哪些？','SSE 适合单向把生成 token 和流程进度推给浏览器。实现上我会区分 event 类型，例如 token、status、source、error、done；只在最终证据稳定后发送来源，避免流中途换来源。服务端要处理客户端断开后的取消信号，给模型/工具设置超时，并保证异常也发送标准 error 事件后正确关闭。对 Agent 流程，不能只流模型 token，还应暴露“正在检索/重排/重试”的阶段信息，用户才能区分系统在工作还是卡住。','请补齐：FastAPI 的具体实现、心跳、断线取消、反向代理缓冲配置。','SSE 基于 HTTP 长连接，天然单向；需要双向交互或二进制时再考虑 WebSocket。')

story += section('4. 核心题库：检索、评测与数据')
story += question('4.1','为什么 Dense Retrieval + BM25 混合检索？','Dense Retrieval 擅长召回语义相近但表述不同的知识，例如“服务起不来”和“启动失败”；但错误码、配置键、类名、日志路径这类字符串实体，语义向量容易把精确匹配稀释。BM25 依据词项频率和逆文档频率，对罕见 token 与精确术语更敏感。研发知识同时有两类需求，所以我并行召回，再用 RRF 融合候选集，最后用 Reranker 判断 query 与 chunk 的细粒度相关性。这个组合不是为了技术堆料，而是针对评测集中错误码、路径、配置项的失败模式设计的。','请补齐：向量 top-k、BM25 top-k、融合后 k、精排 k，以及是否做了字段/元数据过滤。','BM25 典型形式是 TF、IDF 与长度归一化；Dense 的相似度常是 cosine 或 inner product，二者分数尺度不可直接相加。')
story += question('4.2','RRF 是什么，为什么不用分数加权？','RRF，即 Reciprocal Rank Fusion，按每个检索器的名次给候选加分，常见形式是 score(d)=Σ1/(k+rank_i(d))。它只看排序，不要求 BM25 分数和向量相似度处在同一分布，因此比直接加权更稳。我们的做法是分别取两路候选，按 RRF 合并去重，再把靠前结果送给 Reranker。若以后有充足标注数据，可以做归一化和学习排序；在当前样本规模下，RRF 是实现简单、可解释且鲁棒的选择。','建议准备：你实际使用的 k 常数、是否两路等权；没有做过消融时不要说“证明最优”，说“作为稳定基线”。','RRF 解决候选融合，不等于相关性判定；Reranker 才做 query-document 的交叉编码匹配。')
story += question('4.3','Reranker 为什么有效？代价是什么？','向量检索通常把 query 和文档独立编码，适合在大量库中快速召回，但难以完整建模词序和否定等交互。Reranker 对每个 query-chunk 对联合打分，能更准确地区分“包含同一错误码但不是解决方案”的片段，所以我把它放在小候选集之后。代价是推理成本随候选数线性增长，因此不能给全库精排；工程上通过限制候选 k、批处理、缓存和超时降级控制延迟，超时就返回融合排序的结果并记录观测。','请确认 reranker 模型、部署方式、批量大小和 P95 延迟；这是高频追问。','典型 two-stage retrieval：高召回的 bi-encoder/稀疏检索召回，低吞吐高精度的 cross-encoder 精排。')
story += question('4.4','文本如何分块？为什么不能固定长度硬切？','分块目标是让每个 chunk 有足够完整的语义，同时不超过 embedding 与上下文预算。对普通文档我会优先按标题、段落、列表等结构切分，再在过长段落内按句子或字符窗口切；保留适度 overlap 以防答案跨边界。对 SOP、错误码和配置说明，要把“条件-步骤-结果/示例”保在同一块，必要时按条目切而不是按字符切。每个 chunk 还要保留文档标题、层级路径、页码、版本等元数据，后续用于过滤和引用。','请补齐实际 chunk_size、overlap、对表格/代码块/PDF 页眉页脚的处理；这些是审查真实性的点。','chunk 过小会丢上下文、召回噪声变多；过大则主题混杂、精排成本高且上下文拥挤。')
story += question('4.5','OCR 和文档解析如何保证质量？','OCR 是非结构化资料进入知识库的入口，不能把识别结果不加区分地入库。我的处理思路是先按文件类型走解析器；可提取文本的 PDF/DOCX 直接解析，扫描件或图片才走 RapidOCR。OCR 后进行空白、重复页眉页脚、乱码和明显低置信片段清洗，并保留页码和原始文件关联。对表格、代码、公式等高风险内容，不把它们假装成连续自然语言：能结构化解析就结构化，无法可靠识别就标记为低可信，避免污染索引。','请确认：OCR 置信度是否可得、低质量内容策略、是否抽样人工验收；如没有，要说这是待补强项。','知识库质量遵循 garbage in, garbage out；解析准确率、结构保留和版本管理通常比换 embedding 更先影响效果。')
story += question('4.6','180 条评测集怎么构建？指标怎样解释？','评测集覆盖错误码、配置查询、日志路径、故障排查、SOP 问答五类高频任务。每条样本至少有用户问法和人工标注的相关证据或目标文档；对于可能多证据的问题，标注可接受证据集合。Context Recall 关注应该出现的关键证据是否被召回，Hit Rate 关注 top-k 是否至少命中一个正确结果，MRR 关注第一个正确结果排得靠不靠前。在 Top-3 口径下，我们得到 Context Recall 0.918、Hit Rate 0.885、MRR 0.823，说明在这套离线样本上，大多数正确证据被找到且通常排在前面；它不是“模型正确率 88.5%”，更不能直接等价为线上效果。','必须补齐：标注人、标注准则、train/test 是否隔离、top-k、基线与消融结果。若没有人工标注，要明确 RAGAS/LLM judge 的局限。','评测要区分 retrieval、generation、end-to-end；先诊断召回，再诊断回答，避免一个总分掩盖问题。')
story += question('4.7','RAGAS 是否可靠？如何避免“自己评自己”？','RAGAS 能把评测自动化，但它常依赖 LLM judge，因此存在模型偏好、提示词敏感和评价漂移，不能作为唯一真相。对检索效果，我优先以人工标注的 chunk/document 计算 Hit Rate、MRR、Recall；RAGAS 的 context recall 等指标作为补充诊断。对于回答质量，我会抽样人工复核事实性、引用一致性和拒答是否合理，并固定评测版本、模型版本、prompt 和随机性设置。这样指标可复现，也不会把一次模型打分当成绝对结论。','请确认你的 Context Recall 是否由 RAGAS 计算；回答时精确描述，不要混称为“人工准确率”。')

story += section('5. 核心题库：工程、架构与可靠性')
story += question('5.1','Milvus 中如何设计数据和索引？','我会把向量、chunk 文本与可过滤元数据建立稳定关联。至少包含主键 chunk_id、embedding、document_id、知识库/租户标识、标题、层级路径、页码、版本、更新时间和权限标签。向量字段用于 ANN 检索，标量字段用于先过滤再召回；原文可放 Milvus 或对象/关系库，但引用所需元数据必须可稳定取得。索引类型、距离度量和参数需要结合向量维度、数据规模与召回-延迟压测确定，不能只凭默认配置。','补齐：collection schema、索引类型（如 HNSW/IVF）、metric、分区及建索引/重建策略。','向量库设计的核心是过滤、版本与删除一致性；“能搜到”不等于生产可维护。')
story += question('5.2','文档更新、重复入库和删除如何处理？','我会把文档身份与内容版本分开。入库时计算内容 hash，并用 document_id + version 或 hash 做幂等判断；相同内容不重复建 chunk。更新时先生成新版本的 chunk 和向量，校验完成后切换检索可见版本，再异步删除或标记旧版本，避免更新窗口出现全空或新旧混杂。删除必须同时清理向量、BM25 索引、原文和缓存，并记录审计日志。对于异步链路，任务要可重试且操作幂等。','若当前没有版本机制，坦诚说“这是生产化缺口”，给出上面方案；不要暗示已实现。','双写场景要考虑最终一致性与补偿任务；不要只删除 Milvus 而遗漏倒排索引。')
story += question('5.3','如何做权限隔离，特别是金融研发资料？','权限不能只靠 prompt。检索入口先从认证上下文得到用户、部门/项目和权限范围，再把知识库、租户、文档 ACL 等作为服务端 filter 传给向量检索和 BM25；最终来源链接和原文读取也再次鉴权。日志不能打印完整敏感上下文，模型供应商、数据留存和脱敏策略要符合企业要求。对于 Bugfix Agent，代码读取工具要限定仓库根目录和允许路径，并且默认只读，绝不让模型任意执行 shell。','请确认是否已接入 SSO/RBAC/ACL；没有就明确说设计方案而非已上线能力。','LLM 的 prompt 注入防护不能取代鉴权；安全边界必须在工具层和数据层实施。')
story += question('5.4','服务重启后为什么能恢复 Bugfix 流程？','每个执行任务有稳定的 thread_id/run_id。LangGraph 在节点边界把 state 写入 PostgreSQL Checkpointer，其中包括当前计划、已完成步骤、工具观察结果、重试次数和下一节点。服务重启后，根据任务 ID 读取最近 checkpoint，跳过已成功且幂等的步骤，从下一个未完成节点继续；若上次在外部副作用步骤中断，则需要根据幂等键或执行记录判断是重试还是人工确认。恢复的关键不是“保存聊天记录”，而是保存可驱动状态机继续运行的状态。','补齐：checkpoint 粒度、失败状态、重试上限、run 的锁和并发恢复策略。','checkpoint 能恢复控制流，但外部操作要用幂等键、outbox 或人工审批处理“执行到一半”的不确定性。')
story += question('5.5','异常容错与降级如何设计？','我按依赖分层处理。解析/OCR 失败时标注文档失败原因并允许其他文档继续入库；向量或 reranker 超时则在限定时间内降级到 BM25 或融合结果；LLM 失败或限流时返回结构化错误、保留可恢复状态，不返回半截伪答案；Checkpointer 不可用时不声称任务可恢复。所有外部调用有超时、有限重试和指数退避，重试只用于瞬态错误。用户侧显示阶段状态和可操作提示，服务端记录 request_id、耗时、命中数、错误类型，便于排障。','建议补齐超时/重试配置和真正落地的监控指标。','降级策略要以“正确性边界”为前提：检索不确定时可以少答或拒答，不能降低标准后自信回答。')
story += question('5.6','如何观测并定位线上回答质量下降？','我会把链路拆成可观测阶段：路由意图、每路召回候选数和分数分布、RRF 后候选、rerank 前后变化、最终上下文 token、证据评估结果、改写次数、模型耗时和引用数。质量下降先按问题类别和文档版本切片：如果命中数下降，查索引/过滤/入库；如果首位相关性下降，查 query、融合或 reranker；如果证据足但回答差，查 prompt、模型或上下文截断。线上还应采集用户反馈并抽样复标，回灌评测集，而不是只看总成功率。','请补齐现在是否有 tracing/日志；若只是打印日志，别说已有全链路可观测平台。')

story += section('6. 核心题库：Bugfix Agent')
story += question('6.1','Bugfix Agent 的输入、输出和边界是什么？','输入是异常日志、Python 堆栈以及可访问的代码仓库上下文。它先结构化解析异常类型、message、调用栈帧和文件行号，再优先定位堆栈中的业务代码，读取附近上下文并做符号/关键字搜索，形成带证据的根因假设。输出是可疑位置、原因、影响范围、证据链和建议 Diff，定位不确定时会说明置信不足并列出需要补充的信息。边界是它提供辅助诊断和修复建议，不直接自动提交代码、更不在生产环境执行修改；最终合并仍由研发人员 review 和测试。','请准备一个真实堆栈案例，从输入到输出完整讲 2 分钟；注意脱敏。','自动修复风险高，正确的定位顺序是先保证可追溯证据与人工确认，再逐步提高自动化。')
story += question('6.2','为什么选择 Plan-Execute-Replan，而不是纯 ReAct？','Bug 排查是长任务，步骤之间有依赖：没有先解析堆栈就不知道读哪个文件，读完发现根因不在当前函数又要扩展搜索。Plan-Execute-Replan 先把目标拆为可检查的步骤，执行每步后根据观察更新计划，因此进度可展示、状态可持久化、失败可定位。纯 ReAct 更自由，但在多工具、长上下文任务中容易反复搜索或忘记先前观察。我的实现仍允许在某个步骤内用 ReAct 式工具循环，但外层由计划和预算约束。','补齐计划的 JSON schema、最大工具次数/最大重规划次数、何时终止。','计划不等于固定流程：重规划必须以观察为依据，且受步骤、时间、token 和权限预算约束。')
story += question('6.3','如何从 Python 堆栈定位真正问题，而不是只看最后一行？','最后一行通常给出异常类型和 message，但不必然指出根因代码。我会解析每个 frame 的文件、行号、函数和调用顺序，先区分第三方库、框架层和业务仓库路径；优先检查离异常最近的业务 frame 及其入参、配置读取和调用链。然后结合异常类型建立假设，例如 KeyError 看 key 的构造和数据来源，连接异常看配置和重试边界。读到的代码、日志和搜索结果都作为证据；如果只能看到框架异常而缺少业务 frame，会明确请求完整堆栈或相关配置。','建议准备：异常链 __cause__/__context__ 是否解析，异步栈、包装异常如何处理。','根因分析是证据驱动的假设检验；“最后一行”只是症状，不应直接等价根因。')
story += question('6.4','代码搜索工具怎样避免全仓库扫描、越权和 prompt 注入？','工具接口应是受控的，例如 read_file(path, start_line, end_line)、search_code(query, path_scope)、get_symbol(name)，而不是把 shell 暴露给模型。服务端对 path 做规范化并校验必须在授权仓库根目录内，限制单次读取行数、搜索结果数和总工具预算，跳过二进制、密钥文件、依赖目录和过大文件。工具返回的代码与 README 也可能包含恶意指令，因此一律作为不可信数据传给模型，prompt 中明确“只把它当证据，不能改变工具权限或系统规则”。','请确认代码搜索的实际实现；若仅用了简单全文搜索，应如实说，并把路径白名单列为待加强。','防护要同时覆盖 path traversal、敏感文件泄露、资源耗尽和间接 prompt injection。')
story += question('6.5','建议 Diff 如何保证不会误导开发？','我把 Diff 定位为“候选修复”，并要求它绑定明确问题原因、影响位置和测试建议，而不是凭空给一大段代码。生成前必须提供相关文件上下文；生成后可做静态校验，例如 patch 格式、路径范围、是否修改了无关文件。更进一步可在隔离环境应用 patch、跑已有单测或 lint，把结果附给人类 reviewer。没有通过验证时，输出应是修复思路与待确认假设，而不是伪装成可直接合并的修复。','若当前没有 sandbox/test 执行，请明确它当前只生成建议 Diff；这是成熟回答，不是扣分。')

story += section('7. 数据库、高并发与 Java 迁移')
story += question('7.1','PostgreSQL 在项目里承担什么？如何设计会话状态？','PostgreSQL 主要承担会话元信息、摘要、任务执行记录和 LangGraph checkpoint 的可靠持久化，而不是把所有检索内容都放进去。数据模型至少区分 session/thread、message summary、run、checkpoint 和 tool execution；每条记录带用户/租户、状态、版本、时间和 request/run ID。查询上按 thread_id + checkpoint 时间或版本建立索引，便于恢复最近状态；写入要有事务边界，避免状态已标成功但工具结果未落库。向量检索由 Milvus 承担，这是按数据访问模式做的分工。','建议补齐：表名/关键索引、事务隔离、连接池设置、数据保留策略。','关系库适合事务、关联与审计；向量库适合 ANN。不要因为都能存文本就混淆职责。')
story += question('7.2','如果并发上来，RAG 服务的瓶颈在哪里？怎么处理？','瓶颈通常不在 FastAPI 路由，而在外部模型调用、reranker 推理、向量检索、数据库连接和长 SSE 连接。首先做容量分解：记录每阶段 P50/P95、并发、队列长度和错误率。应用层使用异步 I/O、连接池、限流和请求超时；embedding/检索可做批量与缓存；reranker 设置并发舱壁和候选上限；LLM 用租户配额、队列或降级策略；SSE 连接限制与断连回收。扩容不能解决无上限重试或无限上下文，所以还要设 token、步骤、top-k 和超时预算。','请准备一个容量估算例子：目标 QPS、平均模型耗时、需要的并发槽。没有线上压测就说“设计上的处理”。','高并发要先找瓶颈资源；Little 定律 L=λW 可解释为什么慢调用会迅速占满并发。')
story += question('7.3','如果把该系统迁到 Java/Spring 生态，怎么设计？','我会保留系统边界而不是机械翻译 Python。网关/业务 API 用 Spring Boot，SSE 用 WebFlux 或 MVC 的 SseEmitter；会话、任务和 checkpoint 仍用 PostgreSQL，检索服务通过 Milvus Java SDK/HTTP 客户端访问，异步任务通过消息队列或工作线程池解耦。Agent 编排可以选成熟的 Java AI 框架，或把状态机显式实现为状态+事件驱动；关键是节点幂等、状态持久化和观测能力不退化。模型调用封装为统一 provider 接口，支持超时、重试、限流和 tracing。Python 的 OCR、代码分析等生态工具可先以独立服务保留，通过 RPC 调用。','回答时不要硬说“Java 一定更快”。请根据目标岗位补充 Spring、Redis、MQ 的真实熟练程度。','跨语言迁移优先稳定契约：API、状态 schema、事件、权限与可观测性；语言只是实现选项。')
story += question('7.4','哪些地方会使用 Redis 或消息队列？','如果规模增加，Redis 适合短期缓存，如相同 query 的检索结果、会话热数据、限流计数和分布式锁；缓存 key 必须带知识库版本、权限范围和模型/检索配置，避免越权和陈旧答案。消息队列适合耗时且可异步的文档解析、批量 embedding、重建索引和离线评测任务，用任务状态表配合幂等消费和死信/重试。在线问答是否异步要看体验：用户要实时首 token 时仍走同步编排，重任务不要堵塞请求线程。','若项目未用 Redis/MQ，请先说“当前版本没有引入”，再讲扩展设计，不要把设计当事实。')

story += section('8. 弱点、质疑点与复习清单')
story += [P('<b>面试最容易质疑的点：</b>“指标接近 0.99 是否样本太简单或泄漏？”、“Agent 是工作流包装吗？”、“Bugfix 是否真的能修？”、“RAGAS 指标是否可相信？”、“金融数据如何安全？”、“一年经验是否独立完成？”这些不是坏问题，关键是边界、口径、证据和诚实。','BodyC')]
story += question('8.1','你的指标为什么这么高，是否数据泄漏？','这个问题合理。当前 Top-3 口径下的 Context Recall 0.918、Hit Rate 0.885、MRR 0.823 是 180 条离线评测集上的检索指标，不是线上端到端正确率。我会先核查评测样本是否和入库文档版本一致、是否有近重复 query、同一文档片段是否同时出现在开发调参和测试评测中。若没有严格冻结的独立测试集，我不会宣称泛化能力已经被完全证明；下一步是按文档版本和时间划分盲测集，并报告不同问题类别、不同 top-k 的结果和基线对比。现阶段它能证明这套混合检索对现有典型问题覆盖较好，但不能替代线上 A/B 和人工评审。','务必确认是否做了调参集/测试集隔离。若没有，主动说明改进计划反而会显得可靠。')
story += question('8.2','“提升效率”怎么量化？','当前我能严谨陈述的是检索离线指标和系统能力，不会把“辅助研发”直接说成“效率提升百分之多少”，除非有真实工单耗时或用户实验数据。若要量化 Bugfix Agent，我会定义从收到异常到形成首个可行动结论的时间、人工检索文件数、定位命中率、建议被采纳率和人工复核通过率，并与人工排障或旧流程对照。没有这些数据时，应该说它降低了信息收集成本、提供了标准化排障路径，效率收益待通过试点验证。','这是你项目描述当前最大的表述风险之一：避免“显著提升”这类无基线结论。')
story += question('8.3','你做过哪些失败尝试？','一个典型失败模式是只做向量检索。它对自然语言相似问法有帮助，但在错误码、配置键、路径等精确实体上会出现语义相似却实体不对的结果。因此我没有简单调大 top-k，而是引入 BM25 并行召回、RRF 融合和 rerank，用评测集验证排序。另一个风险是让 LLM 在证据不足时仍然输出完整结论，所以工作流加入充分性判断、重检和拒答分支。失败复盘让我学到：先分类问题和建立评测，再选技术，而不是看到某个框架就堆上去。','如果你实际的失败不同，请替换为真实案例；一定包含“现象-定位-取舍-结果”。')
story += question('8.4','你会如何用 30 天把项目工程化？','第一周补齐事实基线：冻结评测集、做按类别报表、打通 tracing 和失败日志。第二周治理知识：版本化入库、增量更新、权限 filter、文档质量抽检。第三周强化可靠性：限流、超时、缓存、异步任务、压测和故障演练。第四周做业务闭环：选真实工单试点，采集排障耗时、引用点击、采纳率和人工纠错，按失败样本迭代。对于 Bugfix Agent，坚持只读分析和建议 Diff，先把审计、测试和人工审批做好，再讨论自动化修改。','这一题适合系统设计面，强调优先级与可衡量结果。')

story += section('最后：背诵卡与面试纪律')
for s in ['任何指标先说口径、样本、top-k/版本，再说结果；检索指标不等于回答正确率。','任何“负责/优化/提升”都落到模块、输入输出、失败分支、前后对比或可复现产物。','已实现、设计方案、待验证三种状态必须分开说。被追问时宁可承认边界，也不要补造事实。','RAG 主线：数据质量 → 混合召回 → 融合精排 → 证据约束生成 → 引用 → 评测闭环。','Agent 主线：目标 → 状态机 → 受控工具 → 观察与重规划 → checkpoint → 限制与审计。','Bugfix 主线：堆栈不是根因 → 读业务上下文 → 搜索证据 → 假设 → 建议 Diff → 人工验证。']:
    story.append(P('• '+s,'BodyC'))
story += [Spacer(1,8*mm), P('面试结束前可反问：团队在 AI 应用中如何定义离线评测与线上质量闭环？知识安全和工具权限是怎样落地的？这能展示你真正关心生产化，而非只会调用模型。','Small')]

def footer(canvas, doc):
    canvas.saveState(); canvas.setStrokeColor(colors.HexColor('#D8E2EA')); canvas.line(18*mm,15*mm,192*mm,15*mm)
    canvas.setFont('CN',8); canvas.setFillColor(colors.HexColor('#657786')); canvas.drawString(18*mm,9*mm,'DevPilot 智能研发系统｜项目面试准备手册')
    canvas.drawRightString(192*mm,9*mm,f'第 {doc.page} 页')
    canvas.restoreState()

doc=SimpleDocTemplate(str(PDF), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=21*mm, title='DevPilot 智能研发系统项目面试准备手册', author='Codex')
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(PDF)
