# Gmail ↔ CNKI 文献桥实操指南

> 配套脚本：`scripts/gmail_cnki_bridge.py`（纯标准库实现，无需额外依赖；真实下载路径依赖 nature-downloader 的 Node 环境）。
> 本指南把「只推知网文献到 Gmail → 下载知网 PDF 回传 Gmail → 攒够阈值自动生成综述」封装成三条命令。

---

## 一、闭环总览

```
① 检索端（电脑/手机）                ② 下载端（有 CNKI 登录态的机器）        ③ 接收端（Spark/daemon）
CNKI 检索 → 条目 JSON (papers)
        │
        ▼  push（SMTP，仅知网条目）
   Gmail 收到 [CNKI-DIGEST] 任务单邮件
        │
        ▼  ingest（nature-downloader CNKI 路由 / 本地 PDF 目录）
   Gmail 收到 [CNKI-INGEST] PDF 附件邮件
        │
        ▼  watch（IMAP 轮询）
   本地文献库归档 → 累积队列 → 满 batch_size 自动触发综述
        │
        ▼
   reviews/batch_N/
     ├─ thesis_chapter1_review.md   综述底本
     ├─ arta_synthesis_payload.json Word 活体引用包
     └─ arta_ppt_payload.json       答辩 PPT 数据
```

Gmail 在这里是**文献总线**：任务单（元数据）和 PDF（全文）都走邮件流转，Spark（你指定的编排 agent）只需要在正确时机执行对应命令。

## 二、前置条件

| 项 | 说明 |
|---|---|
| Gmail 应用专用密码 | 谷歌账号 → 安全 → 两步验证 → 开启 → **应用专用密码**（16 位）。`push/ingest/watch` 都需要；设为环境变量 `GMAIL_EMAIL`、`GMAIL_APP_PASSWORD`，切勿写进代码或聊天 |
| Node.js 22+ | 仅 `ingest` 走 nature-downloader 时需要 |
| 已登录 CNKI 的 Chrome | 真实下载知网依赖你机构的登录态（浏览器远程调试/CDP），CNKI 无公开 API、不能匿名下载 |
| nature-downloader 配置 | `python3 skills/nature-downloader/scripts/configure_school.py url "<你平时进馆的链接>"`（见该 skill 文档） |
| PyMuPDF（可选） | `pip install pymupdf`：CAJ 转 PDF、仿真 PDF 排版需要 |

**先做环境体检**（检查 Python / Gmail 凭据 / Node 22+ / nature-downloader / 可选依赖）：

```bash
python3 scripts/gmail_cnki_bridge.py doctor
# 顺带测一下 Gmail 端口连通性（不需要凭据也能测）
python3 scripts/gmail_cnki_bridge.py doctor --check-network
```

体检全绿才建议跑真实 `push / ingest / watch`；有 ❌ 时脚本以退出码 1 结束，方便脚本化编排。

**再跑离线自检**（不触网、不要凭据，验证过滤/邮件回环/入库/批次触发/去重）：

```bash
python3 scripts/gmail_cnki_bridge.py --selftest
```

## 三、第 1 步：只推知网文献到 Gmail（push）

先准备条目 JSON（列表，或 `{"papers": [...]}` manifest 字典，兼容 `cnki_pdf_downloader.py` 产出的 manifest）：

```json
[
  {
    "item_key": "CNKI_001",
    "title": "基于评分卡方法的水产发酵鲜味肽高通量识别与特征工程分析",
    "authors": ["张伟", "李明"],
    "year": "2024",
    "journal": "中国食品学报",
    "doi": "10.16429/j.1009-7848.2024.01.001",
    "cnki_url": "https://kns.cnki.net/kcms/detail/detail.aspx?filename=ZGSP202401001&dbcode=CJFD",
    "abstract": "……",
    "source": "cnki"
  }
]
```

发送任务单邮件（**自动过滤，只推知网条目**：来源标注 / 中文题名 / CNKI 域名，三者任一命中）：

```bash
python3 scripts/gmail_cnki_bridge.py push \
  --entries outputs/cnki_papers.json \
  --topic "食源性鲜味肽机器学习筛选"
```

- 邮件主题：`[CNKI-DIGEST] {主题} | {日期} | {N} 篇`，正文是人类可读摘要 + 机器可读 JSON（正文标记块 + `cnki_papers.json` 附件双保险）。
- 先验证不发送：加 `--dry-run`，邮件存到 `outputs/gmail_bridge_dryrun/`。
- 条目从哪来：让 agent（Codex/Antigravity + nature-skills）用已登录 Chrome 在 CNKI 检索后导出，或手动整理。

## 四、第 2 步：下载知网 PDF 并回传 Gmail（ingest）

### 路径 A：真实下载（nature-downloader CNKI 路由）

```bash
python3 scripts/gmail_cnki_bridge.py ingest \
  --entries outputs/cnki_papers.json \
  --work-dir outputs/cnki_ingest
```

- 逐条调用 `skills/nature-downloader/scripts/batch_download.mjs --title "<题名>" --cnki-format pdf --no-si`，**中文题名强制走 CNKI 路由**，复用你 Chrome 里的登录态。
- 成功 → 自动打包成 `[CNKI-INGEST] {item_key} | {题名}` 邮件（PDF 附件 + 元数据 JSON）发回 Gmail。
- CAJ 附件/结果会自动尝试 `caj_to_pdf_converter.py` 转 PDF（需 PyMuPDF）。
- 逐条状态写入 `outputs/cnki_ingest/ingest_report/ingest_report.json`（下载失败的原因会带 nature-downloader 的 status，如 `carsi_waiting_user` 需要你在浏览器里手动过一下登录）。

### 路径 B：本地已有 PDF（跳过下载器）

手机/电脑手动保存了知网 PDF（或从 Zotero storage 拷出）后：

```bash
python3 scripts/gmail_cnki_bridge.py ingest \
  --entries outputs/cnki_papers.json \
  --local-pdf-dir "E:/下载"
```

按 `item_key` 前缀或题名前缀匹配目录里的 `.pdf/.caj`，逐条回传。
`--dry-run` 可先只生成 `.eml` 不发。

### 回传邮件长这样

```
主题: [CNKI-INGEST] CNKI_001 | 基于评分卡方法的水产发酵鲜味肽……
附件: CNKI_001_paper.pdf（ASCII 文件名；中文题名在元数据 JSON 里）
正文: <<<CNKI_JSON>>> {"papers":[{...元数据+sha256...}]} <<<END_CNKI_JSON>>>
```

## 五、第 3 步：接收 + 累积 + 自动综述（watch）

```bash
python3 scripts/gmail_cnki_bridge.py watch \
  --batch-size 10 \
  --topic "食源性鲜味肽机器学习筛选与呈味机制" \
  --daemon --interval 300
```

- IMAP 轮询未读邮件，识别 `[CNKI-INGEST]` 主题 → 提取 PDF 附件（CAJ 自动转）→ 归档到 `outputs/gmail_library/{item_key}/{item_key}.pdf`。
- **SHA-256 去重**：同一 PDF 只入库一次；已处理邮件按 Message-ID 记录，不重复触发。
- 每入 1 篇喂给 `LiteratureAccumulator`；**第 N 篇（= batch_size）入队瞬间自动唤醒综述引擎**，产物在 `outputs/gmail_library/reviews/batch_K/`。
- 一次性跑一轮：去掉 `--daemon`。
- 可选：`--mark-seen` 处理完在 Gmail 里标已读。

### 建议的 Gmail 过滤器（可选，让收件箱更干净）

Gmail 设置 → 过滤器：主题包含 `[CNKI-INGEST]` → 应用标签 `CNKI-BUS`（可勾选"跳过收件箱"），然后 `watch --folder "CNKI-BUS"`。

## 六、文件布局

```
outputs/
├── gmail_bridge_dryrun/          # push --dry-run 的 .eml
├── cnki_ingest/
│   └── ingest_report/ingest_report.json
├── gmail_library/
│   ├── index.json                # sha256 → 入库记录（去重）
│   ├── processed_messages.json   # Message-ID → 处理状态（防重复）
│   ├── accumulator_state.json    # 累积队列状态
│   ├── CNKI_001/CNKI_001.pdf     # 归档 PDF
│   └── reviews/batch_1/
│       ├── thesis_chapter1_review.md
│       ├── arta_synthesis_payload.json
│       └── arta_ppt_payload.json
└── gmail_bridge_selftest/        # --selftest 仿真产物
```

## 七、与 Spark / Antigravity 的协作方式

> 📋 **完整的 Spark 指令集（提示词 + 钩子命令 + 任务单轮询）见
> [`docs/spark-prompts.md`](spark-prompts.md)，复制即用。** 下面是最短的一段。

把下面这段话直接交给你的编排 agent（Spark），它就能跑通整条链：

```text
按顺序执行，每步确认成功再继续：
1. 运行 python3 scripts/gmail_cnki_bridge.py --selftest，确认 SELF-TEST PASS；
2. 用已登录 CNKI 的 Chrome 检索主题「<你的主题>」，把前 N 条结果整理成
   outputs/cnki_papers.json（字段：item_key/title/authors/year/journal/doi/cnki_url/abstract/source=cnki）；
3. python3 scripts/gmail_cnki_bridge.py push --entries outputs/cnki_papers.json --topic "<你的主题>"；
4. python3 scripts/gmail_cnki_bridge.py ingest --entries outputs/cnki_papers.json
   （若 Chrome 未登录 CNKI，则先让我手动保存 PDF 到 E:/下载，再改用 --local-pdf-dir "E:/下载"）；
5. 启动 python3 scripts/gmail_cnki_bridge.py watch --batch-size 10 --topic "<你的主题>" --daemon；
6. 每出一个 reviews/batch_N/，按 arta-integration.md 五维审计核对综述底本。
```

## 八、边界与常见问题

| 问题 | 说明 |
|---|---|
| 只能走合法通路 | 真实下载依赖你机构的 CNKI 授权登录态；本桥只处理明确任务单，不做批量扫库、不绕过付费墙 |
| 仿真 vs 真实 | `cnki_pdf_downloader.py` 内置目录是**仿真数据**（用于流程验证）；真实文献必须经 nature-downloader 拉取 |
| Gmail 附件上限 | 单封 25 MB；知网 PDF 通常几 MB，无压力。超大附件请走 Google Drive 链接（需自行扩展） |
| 下载卡在登录页 | report 里出现 `carsi_waiting_user`/`publisher_verification_waiting_user`：在受控 Chrome 里手动完成机构登录后重跑该条 |
| IMAP 搜不到中文主题 | 本桥按「未读 + 本地主题前缀匹配」处理，不走 IMAP SUBJECT 搜索，规避编码问题 |
| 综述幻觉红线 | 综述中每个数值必须能溯源到入库 PDF 的具体页码；引用只允许来自已入库文献（见 `skills/nature-literature-pipeline/references/arta-integration.md`） |

## 九、测试

```bash
python3 -m unittest tests.test_gmail_cnki_bridge -v   # 17 项离线单测
python3 scripts/gmail_cnki_bridge.py --selftest        # 端到端仿真（可重复执行，默认自动清空上轮产物）
python3 scripts/gmail_cnki_bridge.py --selftest --keep-output  # 需要保留上轮产物时
python3 scripts/gmail_cnki_bridge.py doctor            # 环境体检
```

## 十、关于仿真数据的说明（重要）

`scripts/cnki_pdf_downloader.py` 内置的 4 条文献目录是**仿真样例数据**，产出的 PDF 是占位文档，
不是知网原文；其 manifest 与记录都带 `"simulated": true / "provenance": "simulated-catalog"` 标记，
便于下游区分。它的用途只有一个：在没有机构登录态的机器上打通全链路。

**真实知网原文**请走：

```bash
python3 scripts/gmail_cnki_bridge.py ingest --entries <你的条目JSON>
```

底层调用 `skills/nature-downloader`（CNKI 路由），需机构授权 + 已登录 Chrome + Node 22+。
若 `ingest_report.json` 中出现 `carsi_waiting_user`、`publisher_verification_waiting_user`
等状态，说明需要你在浏览器里手动完成一次登录/验证，然后重跑该条目。

## 十一、旧脚本的实时模式

`scripts/gmail_literature_trigger.py` 原本只有仿真模式，现已补上 `--live`：
它会委托本桥的 `watch` 执行真实 IMAP 监听（避免两份重复实现）。

```bash
python3 scripts/gmail_literature_trigger.py --live --batch-size 10 --daemon \
    --topic "食源性鲜味肽机器学习筛选与受体机制"
```

## 十二、让 Spark「一有邮件就触发」（实时推送）

默认 `watch --daemon` 是**定时轮询**（`--interval` 秒）。要做到邮件一到就干活，用 IMAP IDLE：

```powershell
python scripts/gmail_cnki_bridge.py watch --daemon --push-mode idle `
    --batch-size 10 --topic "食源性鲜味肽" `
    --on-event "python scripts/spark_event_hook.py"
```

- `--push-mode idle`：脚本阻塞在 IMAP IDLE 上，Gmail 一有新邮件立刻返回并处理（秒级），
  不再空转轮询；`--idle-timeout` 默认 600s 做保活重连（Gmail 要求 <29 分钟）。
  IDLE 不可用时会自动打印告警并退回轮询，不会卡死。
- 启动时会**先收一轮存量邮件**，避免开机前积压的邮件被漏掉。

### 两种把事件交给 Spark 的方式

| 方式 | 参数 | 适用 |
|---|---|---|
| 本地命令 | `--on-event "<命令>"` | Spark 在同一台机器上，直接拉起脚本/CLI |
| HTTP 回调 | `--webhook "http://127.0.0.1:8848/cnki"` | Spark 是常驻服务，走 HTTP POST |

两者可同时使用，都是 best-effort：钩子失败只告警，**绝不中断监听循环**。

### 事件格式

`paper_ingested`（单篇 PDF 入库时触发）：

```json
{"event":"paper_ingested","topic":"食源性鲜味肽","item_key":"CNKI_001",
 "title":"…","pdf_path":"outputs/gmail_library/CNKI_001/CNKI_001.pdf",
 "queue_count":3,"batch_size":10,"library_dir":"…","timestamp":"2026-09-10 13:20:00"}
```

`review_ready`（攒够阈值、综述编译完成时触发）：

```json
{"event":"review_ready","topic":"食源性鲜味肽","batch_id":1,"paper_count":10,
 "chapter1_path":"…/thesis_chapter1_review.md","payload_path":"…/arta_synthesis_payload.json",
 "ppt_path":"…/arta_ppt_payload.json","library_dir":"…","timestamp":"…"}
```

命令模式下事件同时通过 **stdin** 和环境变量 `CNKI_EVENT` / `CNKI_EVENT_TYPE` /
`CNKI_ITEM_KEY` / `CNKI_PDF_PATH` 传入，shell 和脚本都好接。

### 现成的 Spark 钩子：`scripts/spark_event_hook.py`

不用自己写胶水，直接挂上即可。它会：
1. 把事件追加进 `outputs/spark_events/events.jsonl`（Spark 可以 tail 这个文件）；
2. 为每个事件写一份 `outputs/spark_events/tasks/<时间戳>_<类型>_<key>.json` 任务单
   （Spark 扫目录取任务，带 `status: pending` 和 `suggested_action`）；
3. 可选直接拉起 Spark 命令，支持占位符 `{item_key} {pdf_path} {payload_path}
   {chapter1_path} {ppt_path} {topic} {task_path}`：

```powershell
--on-event "python scripts/spark_event_hook.py --spark-cmd 'spark run review --payload {payload_path}' --only review_ready"
```

`--only review_ready` 表示只在综述就绪时才叫 Spark，单篇入库只记账不打扰。

> ⚠️ 上面的 `spark run review ...` 只是**占位示例**，请换成你的 Spark 真实命令。
> 如果你的 Spark 没有 CLI（只有对话界面），就不要传 `--spark-cmd`：
> 钩子仍会把事件写进 `outputs/spark_events/tasks/`，让 Spark 扫目录取任务。
> 三种接法的完整指令见 [`docs/spark-prompts.md`](spark-prompts.md)。

### 完整的一条龙命令（推荐）

```powershell
$env:GMAIL_EMAIL="your_account@gmail.com"
$env:GMAIL_APP_PASSWORD="xxxxxxxxxxxxxxxx"

python scripts/gmail_cnki_bridge.py watch --daemon --push-mode idle `
    --batch-size 10 --topic "食源性鲜味肽高通量筛选与呈味机制解析" `
    --library outputs/gmail_library --mark-seen `
    --on-event "python scripts/spark_event_hook.py --spark-cmd 'spark run review --payload {payload_path}' --only review_ready"
```

> 说明：Gmail 没有真正的「服务端 push 到本机」能力（除非上 Google Cloud Pub/Sub + 公网回调），
> IMAP IDLE 是免公网、免额外服务的最实时方案，延迟通常在数秒内。

## 十三、让 Spark 处理邮箱里【已有】的 PDF（harvest）

`watch` 只处理本系统自己发的、未读的 `[CNKI-INGEST]` 邮件。
如果你邮箱里**早就躺着**一堆文献 PDF（导师转发的、自己存档的、其它工具发的），
用新增的 `harvest` 子命令一次性回收——它扫描**任意邮件里的任意 PDF 附件**。

### 第一步：先 dry-run 看清单（强烈建议）

```powershell
python scripts/gmail_cnki_bridge.py harvest --dry-run --newest-first --limit 50 `
    --topic "食源性鲜味肽高通量筛选与呈味机制解析"
```

只列出「会收哪些附件、多大、来自哪封邮件」，**不落盘、不入库、不触发综述**。

### 第二步：确认无误后正式收

```powershell
python scripts/gmail_cnki_bridge.py harvest --batch-size 10 --cnki-only `
    --topic "食源性鲜味肽高通量筛选与呈味机制解析" `
    --on-event "python scripts/spark_event_hook.py"
```

收够 `--batch-size` 篇会**立刻自动编译综述**并触发 `review_ready` 事件给 Spark。

### 筛选参数

| 参数 | 作用 |
|---|---|
| `--since 2025-01-01` / `--before 2026-01-01` | 按日期范围（自动转 IMAP 的 `01-Jan-2025` 格式） |
| `--from prof@univ.edu` | 只收某个发件人的邮件（导师转发场景最常用） |
| `--subject "CNKI"` | 主题关键词。**中文主题建议改用标签或发件人过滤**，IMAP 中文搜索有编码坑 |
| `--folder "[Gmail]/All Mail"` | 扫全部邮件；也可填你自建的 Gmail 标签名 |
| `--unseen-only` | 只看未读 |
| `--cnki-only` | 只收中文/知网文献，过滤掉发票、说明书等无关 PDF |
| `--limit 50` | 最多处理多少个附件，先小批量试水 |
| `--newest-first` | 从最新邮件往回扫 |
| `--mark-seen` | 处理完标记已读，方便下次增量 |

### 三条安全保证

1. **SHA-256 去重**：同一份 PDF 无论出现在几封邮件里都只入库一次，
   已被 `watch` 收过的也不会重复计入批次（共用同一个 `index.json`）。
2. **元数据兜底**：本系统发的回传件走精确解析；其它邮件则从**附件名 → 主题 → 发件日期**
   依次推断标题和年份，中文附件名可直接用。
3. **可重复执行**：`harvest` 幂等，多跑几次不会污染文献库；每次产出
   `outputs/gmail_library/harvest_report.json` 供核对。

### 典型用法：把导师半年内转发的文献全收了

```powershell
python scripts/gmail_cnki_bridge.py harvest `
    --from "tutor@ldu.edu.cn" --since 2026-03-01 --cnki-only --mark-seen `
    --batch-size 10 --topic "食源性鲜味肽高通量筛选与呈味机制解析" `
    --on-event "python scripts/spark_event_hook.py --spark-cmd 'spark run review --payload {payload_path}' --only review_ready"
```

### 存量 + 增量的推荐组合

```powershell
# 1) 先把历史存量一次性收干净
python scripts/gmail_cnki_bridge.py harvest --cnki-only --mark-seen --batch-size 10 --topic "…"

# 2) 再挂上实时监听，以后新邮件秒级自动处理
python scripts/gmail_cnki_bridge.py watch --daemon --push-mode idle --batch-size 10 --topic "…" `
    --on-event "python scripts/spark_event_hook.py"
```
