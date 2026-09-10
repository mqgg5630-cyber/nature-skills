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
