# RAG 离线评测

项目包含两套相互补充的评测：

- 检索评测：Context Precision、Context Recall、Hit Rate 和 MRR。
- 生成与端到端评测：Faithfulness、Answer Correctness、Answer Relevancy、
  Citation Correctness、Citation Completeness、Citation Source Validity 和 E2E Pass Rate。

## 数据集字段

已有样本只需保持 `id`、`question`、`reference_answer`、`expected_sources` 即可兼容。
为了评估负样本和分类结果，可以增加：

```json
{
  "id": "config-001",
  "category": "config",
  "question": "生产环境数据库连接超时时间是多少？",
  "reference_answer": "生产环境数据库连接超时时间为 30 秒。",
  "expected_sources": ["生产环境数据库配置说明.md"],
  "key_facts": ["连接超时时间为 30 秒"],
  "answerable": true,
  "should_clarify": false
}
```

不可回答样本可以将 `expected_sources` 设为空数组，并将 `answerable` 设为 `false`。

## 运行

先确保 Milvus 中已经导入评测集对应的知识文档，并配置 DashScope：

```bash
python -m app.evaluation.generation_runner --limit 5
```

确认链路正常后运行完整数据集：

```bash
python -m app.evaluation.generation_runner
```

也可以通过 `--offset` 和 `--limit` 分批执行。长任务建议增加检查点：

```bash
python -m app.evaluation.generation_runner \
  --limit 60 \
  --checkpoint evaluation/reports/generation_60_checkpoint.json
```

相同命令再次执行时会跳过检查点中已经完成的样本，并重试失败样本。最终报告会写入
`evaluation/reports`，同时生成 JSON 明细和 Markdown 汇总。

## 评分方案

前三项生成指标和引用语义指标由温度为 0 的结构化 LLM 裁判评分；引用来源有效性由本地
确定性规则校验，引用必须来自本轮实际检索结果。

单条样本只有同时满足以下条件才计为端到端通过：

- Faithfulness 不低于 0.90；
- Answer Correctness 不低于 0.85；
- Answer Relevancy 不低于 0.85；
- Citation Correctness 和 Citation Completeness 均不低于 0.90；
- Citation Source Validity 等于 1.00。

LLM 裁判可能存在偏差。正式对外使用结果前，应人工复核全部失败样本和至少 20% 的随机
通过样本，并在同一模型、Prompt、知识库版本和 Top-K 配置下进行版本对比。
