# Spark 指令集（复制即用）

面向 `scripts/gmail_cnki_bridge.py` 全流程的 Spark / Antigravity 编排指令。
分两类：**A. 贴给 Spark 的自然语言提示词**（Spark 是对话式 agent 时用）、
**B. 挂在钩子上的机器指令**（Spark 是可执行 CLI / HTTP 服务时用）。

> ⚠️ 本文中 `spark run ...` 一律是**占位示例**。请替换成你的 Spark 实际提供的命令；
> 如果你的 Spark 只有对话界面、没有 CLI，请用 A 类提示词 + 下面的「任务单轮询」模式。

---

## A. 贴给 Spark 的提示词

### A1. 处理邮箱里【已有】的 PDF（一次性存量回收）

```text
你是我的文献流水线编排助手。仓库根目录：E:\0mcp-agv\nature-skills。
所有命令在该目录下用 PowerShell 执行，执行前先设置 $env:PYTHONUTF8=1。

任务：把我 Gmail 邮箱里已有的知网文献 PDF 全部纳入流水线并生成综述。

按顺序执行，每步把完整输出贴给我确认后再继续：

1. 环境体检：
   python scripts/gmail_cnki_bridge.py doctor
   要求除 GMAIL_EMAIL / GMAIL_APP_PASSWORD 外全部为 ✅。若这两项为 ❌，
   提醒我先设置环境变量，不要替我编造凭据。

2. 先干跑，只看清单不落盘：
   python scripts/gmail_cnki_bridge.py harvest --dry-run --newest-first --limit 50 `
       --topic "食源性鲜味肽高通量筛选与呈味机制解析"
   把 would_ingest 的条目列成表格（item_key / 标题 / 大小 / 来源邮件主题）给我看，
   并指出其中哪些明显不是学术文献（发票、说明书、课件等）。

3. 我确认后，正式收取：
   python scripts/gmail_cnki_bridge.py harvest --batch-size 10 --cnki-only --mark-seen `
       --topic "食源性鲜味肽高通量筛选与呈味机制解析" `
       --on-event "python scripts/spark_event_hook.py"
   读取 outputs/gmail_library/harvest_report.json，汇报
   ingested / duplicate / skipped 各多少条。

4. 如果队列没攒够 batch-size、没有出综述，告诉我还差几篇，
   并建议是放宽筛选（去掉 --cnki-only、扩大 --since 范围、换 --folder "[Gmail]/All Mail"）
   还是补充新文献。不要为了凑数把无关 PDF 收进来。

5. 出综述后，读取 outputs/gmail_library/reviews/batch_N/thesis_chapter1_review.md，
   按 skills/nature-literature-pipeline/references/arta-integration.md 的五支柱做审计：
   定量数据溯源率≥90%（每个数字要能指到具体 PDF 页码）、因果链完整、
   写明局限性、论证非流水账、零虚假引用。
   逐条给出「通过 / 不通过 + 证据」，不通过的直接给出修改建议。

硬性要求：
- 不许编造文献、数据、页码。溯源不到的内容标注「待核」，不要填空。
- 命令失败时把原始报错贴出来，不要自行改命令绕过。
```

### A2. 挂实时监听（增量，邮件一到就干活）

```text
在 E:\0mcp-agv\nature-skills 下启动常驻监听，并说明如何停止：

$env:PYTHONUTF8=1
python scripts/gmail_cnki_bridge.py watch --daemon --push-mode idle `
    --batch-size 10 --topic "食源性鲜味肽高通量筛选与呈味机制解析" `
    --library outputs/gmail_library --mark-seen `
    --on-event "python scripts/spark_event_hook.py"

启动后：
- 确认日志里出现「触发方式: IMAP IDLE 实时推送」，否则说明退回了轮询，报告原因；
- 之后每当 outputs/spark_events/tasks/ 下出现新的 *_review_ready_*.json，
  立刻读取其中的 chapter1_path，执行 A1 第 5 步的五支柱审计并汇报；
- 出现 *_paper_ingested_*.json 时只记账，不打扰我。
```

### A3. 从零开始（检索 → 推送 → 下载 → 综述）

```text
按顺序执行，每步确认成功再继续：
1. python scripts/gmail_cnki_bridge.py --selftest，确认 SELF-TEST PASS；
2. 用我已登录 CNKI 的 Chrome 检索主题「<你的主题>」，把前 N 条结果整理成
   outputs/cnki_papers.json，字段：
   item_key/title/authors/year/journal/doi/cnki_url/abstract/source=cnki；
3. python scripts/gmail_cnki_bridge.py push --entries outputs/cnki_papers.json --topic "<你的主题>"；
4. python scripts/gmail_cnki_bridge.py ingest --entries outputs/cnki_papers.json
   若 ingest_report.json 里出现 carsi_waiting_user / publisher_verification_waiting_user，
   说明需要我在浏览器里手动登录一次，暂停并告诉我该点哪里，不要重试刷屏；
   实在下不下来就让我手动存到 E:\下载，改用 --local-pdf-dir "E:\下载"；
5. python scripts/gmail_cnki_bridge.py watch --daemon --push-mode idle --batch-size 10 --topic "<你的主题>"；
6. 每出一个 reviews/batch_N/，做五支柱审计。
```

---

## B. 机器指令（挂在事件钩子上）

事件字段：`paper_ingested` → `item_key/title/pdf_path/queue_count/batch_size`；
`review_ready` → `batch_id/paper_count/chapter1_path/payload_path/ppt_path`。
钩子命令里可用占位符：`{item_key} {pdf_path} {payload_path} {chapter1_path} {ppt_path} {topic} {task_path}`。

### B1. Spark 是 CLI

```powershell
--on-event "python scripts/spark_event_hook.py --spark-cmd '<你的真实命令> {payload_path}' --only review_ready"
```

把 `<你的真实命令>` 换成实际可执行的东西，例如：

```powershell
# 例：调本仓库自带的综述管线
--on-event "python scripts/spark_event_hook.py --spark-cmd 'python scripts/zotero_review_pipeline.py --payload {payload_path}' --only review_ready"

# 例：单篇入库就做结构化抽取
--on-event "python scripts/spark_event_hook.py --spark-cmd 'python my_spark_cli.py extract --pdf \"{pdf_path}\" --key {item_key}' --only paper_ingested"
```

### B2. Spark 是常驻 HTTP 服务

```powershell
python scripts/gmail_cnki_bridge.py watch --daemon --push-mode idle --batch-size 10 `
    --topic "食源性鲜味肽" --webhook "http://127.0.0.1:8848/cnki-event"
```

Spark 侧收到的就是上面那份事件 JSON（`Content-Type: application/json`）。

### B3. Spark 没有 CLI 也没有服务（任务单轮询）

`spark_event_hook.py` 默认就会落两样东西，让对话式 Spark 也能消费：

- `outputs/spark_events/events.jsonl` —— 追加式事件流，可 tail
- `outputs/spark_events/tasks/<时间戳>_<类型>_<key>.json` —— 任务单，带 `status: pending` 和 `suggested_action`

给 Spark 的轮询指令：

```text
每次我说「查任务」时，你执行：
  Get-ChildItem outputs/spark_events/tasks/*.json | Where-Object { (Get-Content $_ -Raw | ConvertFrom-Json).status -eq 'pending' }
读取所有 pending 任务单，按 event.event 分派：
  - review_ready   → 读 event.chapter1_path，做五支柱审计
  - paper_ingested → 读 event.pdf_path，抽取 PaperCard（标题/作者/年份/期刊/核心结论/关键数据+页码）
处理完把该 JSON 的 status 改成 done，并追加 result 字段记录你的产出路径。
```

---

## C. 一句话速查

| 场景 | 命令 |
|---|---|
| 先体检 | `python scripts/gmail_cnki_bridge.py doctor` |
| 看邮箱有啥（不落盘） | `... harvest --dry-run --newest-first --limit 50 --topic "…"` |
| 收存量 | `... harvest --batch-size 10 --cnki-only --mark-seen --topic "…" --on-event "python scripts/spark_event_hook.py"` |
| 管增量（实时） | `... watch --daemon --push-mode idle --batch-size 10 --topic "…" --on-event "python scripts/spark_event_hook.py"` |
| 只在出综述时叫 Spark | 钩子加 `--only review_ready` |
