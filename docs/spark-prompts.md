# Spark 指令集（复制即用）

> ## 🔑 先确定你的 Spark 是哪种形态
>
> | 形态 | 怎么接 | 看哪节 |
> |---|---|---|
> | **云端，只能收 Gmail + 读云盘** ← 你是这种 | 本机把成果**寄给** Spark；Spark 在邮件里干活 | **[§0 云端 Spark](#0-云端-spark只能收-gmail读云盘)** |
> | 本地对话式 agent | 贴提示词，它自己执行命令 | §A |
> | 本地 CLI / HTTP 服务 | `--spark-cmd` / `--webhook` | §B |
>
> 本机（Windows + 已登录 CNKI 的 Chrome）负责**下载和编译**，
> 云端 Spark 负责**审稿和改写**——它碰不到你的本地文件，所以一切都得通过邮件送过去。

---

## 0. 云端 Spark（只能收 Gmail、读云盘）

### 0.1 数据怎么流

```
本机 harvest/watch          → 收邮箱已有 PDF、编译综述（全在本地）
      ↓ deliver（SMTP 发信）
Spark 的 Gmail 收件箱        → 收到 [CNKI-REVIEW] 邮件：
                                正文 = 任务说明 + 综述全文（内联，不依赖附件解析）
                                附件 = .md / payload.json / ppt.json
                                正文还可带云盘链接（大文件走这里）
      ↓ Spark 回信
你的 Gmail                   → Spark 把审计结果/修订稿回邮件给你
```

关键点：**综述全文直接内联在正文里**。很多云端 agent 解析附件不稳，正文最保险；
附件同时附上，方便你自己存档。

### 0.2 一条命令：收邮箱存量 + 编译 + 自动寄给 Spark

```powershell
$env:PYTHONUTF8=1
$env:GMAIL_EMAIL="jzthjyz@gmail.com"
$env:GMAIL_APP_PASSWORD="你的16位应用专用密码"
$env:SPARK_EMAIL="spark那边的收件地址@xxx"     # 设了它就不用每次传 --deliver-to

python scripts/gmail_cnki_bridge.py harvest --batch-size 10 --cnki-only --mark-seen `
    --topic "食源性鲜味肽高通量筛选与呈味机制解析" `
    --cloud-link "https://drive.google.com/drive/folders/你的文献文件夹ID"
```

攒够 10 篇 → 本地编译综述 → **自动寄一封 `[CNKI-REVIEW]` 给 Spark**，你什么都不用做。

### 0.3 挂实时监听（以后新邮件自动走完全程）

```powershell
python scripts/gmail_cnki_bridge.py watch --daemon --push-mode idle `
    --batch-size 10 --topic "食源性鲜味肽高通量筛选与呈味机制解析" --mark-seen `
    --deliver-to "spark那边的收件地址@xxx" `
    --cloud-link "https://drive.google.com/drive/folders/你的文献文件夹ID"
```

### 0.4 手动补寄（综述已经生成过了）

```powershell
# 先看看要寄什么（不发信，存成 .eml 可以打开检查）
python scripts/gmail_cnki_bridge.py deliver --topic "食源性鲜味肽" --dry-run

# 确认后正式寄最新一批
python scripts/gmail_cnki_bridge.py deliver --topic "食源性鲜味肽" `
    --to "spark@xxx" --cloud-link "https://drive.google.com/..."

# 指定某一批
python scripts/gmail_cnki_bridge.py deliver --batch outputs/gmail_library/reviews/batch_2 --to "spark@xxx"

# 寄单篇 PDF，让 Spark 做结构化抽取（PaperCard）
python scripts/gmail_cnki_bridge.py deliver --pdf outputs/gmail_library/CNKI_001/CNKI_001.pdf `
    --item-key CNKI_001 --title "鲜味肽的分离鉴定" --to "spark@xxx"
```

### 0.5 大文件走云盘

Gmail 单封上限 25MB，脚本内部按 16MB 卡（base64 会膨胀约 1.37 倍）。
超限的附件会**自动跳过并在控制台提示**，不会把整封信发失败。这时：

1. 把 PDF 传到 Google Drive / OneDrive，设为「知道链接即可查看」；
2. 用 `--cloud-link` 把链接带进正文（可重复传多个）；
3. Spark 从正文链接去云盘取文件。

### 0.6 Spark 侧要做的两件事

**① 建 Gmail 过滤器**（让 Spark 只被这两类邮件唤醒）：

- 主题包含 `[CNKI-REVIEW]` → 打标签 `待审综述`
- 主题包含 `[CNKI-CARD]` → 打标签 `待抽取文献`

**② 把这段设成 Spark 的常驻指令**：

```text
你是我的学位论文文献审稿助手。我会通过邮件给你派活，两类：

【主题含 [CNKI-REVIEW]】自动编译的综述底本（正文内含全文，附件有 .md 和 json）
请按 ARTA 五支柱逐条审计，输出「通过/不通过 + 证据」，再给修订后的综述全文：
  1. 定量溯源：每个数字和结论能否指到具体文献的具体页码？溯源率需≥90%。
  2. 因果链：机制解释是否连贯、有无跳步。
  3. 局限性：是否写明现有研究的边界与不足。
  4. 论证结构：是否是「A说…B说…C说…」的流水账？需改成问题导向的论证。
  5. 零虚假引用：引用是否全部来自本批次实际入库的文献。

【主题含 [CNKI-CARD]】单篇文献 PDF
请抽取 PaperCard：标题/作者/年份/期刊/DOI/研究问题/方法/
关键定量结果（每个数字标注页码）/结论/局限性。

通用铁律：
- 严禁编造文献、数据、页码。原文里找不到的，写「未提及」或「待核」，不要填空。
- 正文里如果有云盘链接，需要原始 PDF 时从那里取。
- 处理完直接回复本邮件，把结果写在正文里（我这边靠回信收结果）。
```

### 0.7 边界（说清楚，免得你踩坑）

- **本机必须开着**。下载知网、编译综述、发信都在你的 Windows 上跑，
  关机就断了。Spark 在云端不能替你连知网（它没有你的机构登录态）。
- **云盘不是自动上传的**。`--cloud-link` 只是把你给的链接写进邮件正文，
  脚本不会替你上传文件——需要你自己或用云盘客户端同步 `outputs/gmail_library/`。
- **Spark 的回信不会自动回流**。它审完是回邮件给你，本仓库目前不解析这类回信，
  你自己看邮件即可。

---

## A. 贴给 Spark 的提示词（本地对话式 agent）

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
