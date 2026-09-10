# 基于 Zotero 真实 PDF 文献的 SCI 顶刊综述生成指南与全流程工作流

> 本文档由 **Nature Skills** 团队针对 Zotero + 知网/SCI PDF 全文提取、结构化证据链梳理与顶刊级综述论文写作全流程编写。

---

## 目录

- [1. 综述写作的开源仓库、MCP 与 Skills 生态](#1-综述写作的开源仓库mcp-与-skills-生态)
- [2. 学术 PDF 全面读取的选型与最佳实践](#2-学术-pdf-全面读取的选型与最佳实践)
- [3. 基于真实 PDF 内容生成科研 SCI 综述的标准流程（防幻觉）](#3-基于真实-pdf-内容生成科研-sci-综述的标准流程防幻觉)
- [4. 写综述推荐的 Skills 组合与分工](#4-写综述推荐的-skills-组合与分工)
- [5. 独立测试与集成步骤（从单模块测试到全面组装）](#5-独立测试与集成步骤从单模块测试到全面组装)
- [6. 分支同步与本地 Git 克隆指令](#6-分支同步与本地-git-克隆指令)

---

## 1. 综述写作的开源仓库、MCP 与 Skills 生态

要基于 Zotero 中带有真实 PDF 的文献库（如通过知网 CNKI 插件或 Jasmine / Translators 导入的论文）生成高水平 SCI 综述，核心不能依赖“大模型自由发挥”，而必须建立在**事实提取（Fact Extraction）与证据链锚定（Evidence Grounding）**之上。

### 1.1 本仓库 `nature-skills`（专为顶刊 SCI 设计）
本仓库收录了面向 Nature / Science 及各顶刊标准的全套科研 Skills：
- **`nature-literature-pipeline`**：全自动文献流水线与**集中综述编译工作流**（`references/review-compilation-workflow.md`），涵盖存量盘点、空白填补、受众过滤到 7 节架构设计。
- **`nature-paper-card`**：单篇文献深度精读生成 01–16 节的 Paper Card（提取研究问题、实验系统、量化数据、机理解释、结论边界与局限性），是杜绝幻觉的基石。
- **`nature-reader`**：带原文来源锚点、双语对照、公式与图表精确保留的全文深度解析器。
- **`nature-writing`（`paper_type: review`）**：严格遵循顶刊综述论证链（`Scope -> Organizing Principle -> Thematic Synthesis -> Disagreements & Gaps -> Author Stance -> Outlook`），彻底摒弃流水账罗列。
- **`nature-citation` & `nature-ref-verifier`**：多源交叉核验引用真实性，防止捏造 DOI、作者或卷期。
- **`nature-figure`**：用于绘制综述核心的自绘机理概念图（Class A）与跨研究对比热力/柱状图。
- **`nature-polishing`**：Nature 级学术语言润色与逻辑连接词校正。

### 1.2 外部协作 MCP 与开源工具
- **`zotero-mcp` / `mcp-server-zotero`**：连接本地 Zotero 7 或 Web API，实现条目检索、标签筛选、PDF 附件路径获取和笔记提取。
- **`MinerU (Magic-PDF)` / `Marker`**：业界领先的开源学术 PDF 版面分析工具，支持双栏、公式 LaTeX 化与复杂表格 Markdown 化。

---

## 2. 学术 PDF 全面读取的选型与最佳实践

学术论文 PDF（尤其是知网双栏 PDF 和国外 Elsevier / Springer / Nature 排版）具有双栏混排、跨页表格、嵌入式公式、Figure Caption 混排等复杂特性。

### 2.1 常见的四级读取解析方案

| 方案级别 | 核心技术 / 工具 | 优势 | 适用场景 |
|---|---|---|---|
| **Level 1: 学术版面分析引擎（推荐首选）** | **MinerU (Magic-PDF)** / **Marker** / **Grobid** | 完美还原双栏阅读顺序、精准提取三线表为 Markdown、公式转标准 LaTeX、自动切割图表与 Caption | 需要高精度提取正文、数学公式、表格数据与实验参数时 |
| **Level 2: 几何流式 Python 本地引擎** | **PyMuPDF (`fitz`)** + 正则状态机（本仓库 `prepare_paper.py` & `zotero_review_pipeline.py`） | 极轻量、无需重型深度学习依赖、提取速度毫秒级、精确到页面与几何块 | 批量扫描大量 PDF、快速抽取 IMRAD 章节与图表 Captions |
| **Level 3: 多模态视觉模型（VLM）** | **Claude 3.7 / GPT-4o / Qwen-VL** + 逐页高清渲染 | 语义理解最强，能直接看懂复杂的工艺流程图、机理示意图和扫描版 PDF | 针对核心突破论文的重点图表进行多模态机理解析 |
| **Level 4: Zotero 原生划线与笔记提取** | Zotero 7 内置 PDF 全文索引 + Annotations Export | 包含研究者本人的精读高亮、标签与批注，信息密度极高 | 结合人工筛选的重点段落进行快速合成 |

### 2.2 推荐的读取实施标准
1. **优先提取结构化章节**：将 PDF 分割为 `Abstract`、`Introduction`、`Methods`、`Results`、`Discussion`、`Conclusion`，而不是一股脑将整篇纯文本喂给大模型。
2. **提取图表 Caption 索引**：学术综述中 70% 的核心量化结论都浓缩在 Figure 和 Table 的图注中。
3. **保留页码锚点（Page Anchors）**：提取时强制记录 `pdf_page`，确保后续写综述时每一句话都能溯源到 PDF 的具体页码和图表编号。

---

## 3. 基于真实 PDF 内容生成科研 SCI 综述的标准流程（防幻觉）

高质量 SCI 综述的核心原则是：**“先做事实提取与对比矩阵（Evidence Matrix），再做主题式综合分析（Thematic Synthesis），绝不让模型直接无约束写作。”**

```
 ┌─────────────────────────────────────────────────────────────┐
 │                Zotero 本地库 / 知网 PDF 文献               │
 └──────────────────────────────┬──────────────────────────────┘
                                │ ① 检索与附件定位 (Zotero Connector / MCP)
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │            PDF 结构化解析 (PyMuPDF / MinerU 提取)           │
 └──────────────────────────────┬──────────────────────────────┘
                                │ ② 01-16 节事实卡片化 (nature-paper-card)
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │         结构化文献事实库 (Paper Cards + 证据锚点)          │
 └──────────────────────────────┬──────────────────────────────┘
                                │ ③ 横向对比与争议/空白识别 (nature-literature-pipeline)
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │         跨研究证据对比矩阵 (Cross-Study Evidence Matrix)    │
 └──────────────────────────────┬──────────────────────────────┘
                                │ ④ 7 节因果驱动顶刊大纲 (nature-writing: review)
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │          SCI 综述草稿起草 (主题对比论证，严禁流水账)         │
 └──────────────────────────────┬──────────────────────────────┘
                                │ ⑤ 引用多源交叉验证 (nature-ref-verifier)
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │     高质量 SCI 综述产物 (.md) + 标准 BibTeX + 零幻觉报告    │
 └─────────────────────────────────────────────────────────────┘
```

### 步骤详解：

#### 步骤一：单篇文献事实卡片化（Paper Card Extraction）
从每篇 PDF 抽取 6 个核心要素：
1. **研究痛点与核心科学问题**（Research Problem）
2. **实验材料与方法体系**（Materials & Methods）
3. **关键量化指标**（Quantitative Metrics，包含精确数值、测试条件）
4. **提出的物理/化学机制**（Proposed Mechanism）
5. **适用边界与局限性**（Limitations & Boundary Conditions）
6. **来源证据锚点**（Figure/Table/Page 编号）

#### 步骤二：构建横向对比矩阵（Evidence Matrix）
将多篇文献汇总成 Markdown 表格，横向比对不同流派、不同机理、不同性能的优劣，找出：
- **领域共识（Consensus）**
- **学术争议与矛盾（Contradictions/Disagreements）**：例如“为什么研究 A 认为掺杂能提高稳定性，而研究 B 却报告发生了相分离？”
- **研究空白（Research Gaps）**：例如目前所有工作均集中在室温，高温恶劣工况下数据缺失。

#### 步骤三：7 节顶刊论证架构（Narrative Architecture）
顶刊综述的标准篇幅分布与逻辑链：
1. **§1 Introduction & Thematic Scope (~8%)**：从大背景漏斗式引出核心矛盾与本综述的主题边界。
2. **§2 Fundamental Mechanisms (~13%)**：底层物理/化学反应原理。
3. **§3 Material & Methodology Taxonomy (~17%)**：材料分类、合成路线或算法架构对比。
4. **§4 Core Degradation / Bottlenecks / Controversies (~30% ★核心)**：深入剖析核心瓶颈与争议焦点（篇幅最大、信息密度最高）。
5. **§5 Modulation & Engineering Strategies (~13%)**：界面调控、改性手段或优化方案。
6. **§6 Advanced Characterization & In-Situ Methods (~10%)**：先进表征与计算方法学。
7. **§7 Strategic Roadmap & Outlook (~10%)**：提出领域未来的关键挑战、可检验假设与发展路线图。

#### 步骤四：主题驱动的对比式起草（Thematic Drafting）
- **严禁流水账**：绝对避免 `Author A reported X. Author B reported Y.`
- **采用机理/现象驱动的综合句式**：
  > *"While in-situ Raman spectroscopy by Wang et al. [Wang2023] demonstrated that crosslinked networks deflect dendrites at 60 °C, subsequent cryogenic TEM investigations by Zhang et al. [Zhang2024] revealed that localized interfacial shear stress above 70% SOC triggers void accumulation, suggesting that mechanical stiffness alone cannot accommodate dynamic lattice contraction."*

#### 步骤五：多源引用核验（Ref Verification）
通过 `nature-ref-verifier` 对综述中出现的全部 `[CiteKey]` 进行核对，确保 100% 对应 Zotero 中的真实文献，导出可直接导入 Zotero 的 `.bib` 文件。

---

## 4. 写综述推荐的 Skills 组合与分工

| 阶段 | 推荐使用的 Skill | 核心职责 |
|---|---|---|
| **1. 文献整理与空白识别** | `nature-literature-pipeline` | 运行综述编译工作流，盘点存量、识别子方向空白 |
| **2. 单篇 PDF 精读与卡片化** | `nature-paper-card` / `nature-reader` | 解析双栏 PDF，提取 01-16 节 Paper Card 与公式图表 |
| **3. 综述大纲与正文起草** | `nature-writing` (`paper_type: review`) | 建立 7 节漏斗式论证链，执行主题对比写作 |
| **4. 综述机理图表规划** | `nature-figure` | 规划 Class A 自绘机理流程图与 Class B 引用图表 |
| **5. 引用交叉验证与查重** | `nature-ref-verifier` / `nature-citation` | 逐条核验作者、年份、DOI、页码，杜绝错引漏引 |
| **6. 顶刊语言与语气润色** | `nature-polishing` | 提升学术英语言语严谨度，消除中式英语与冗余 |

---

## 5. 独立测试与集成步骤（从单模块测试到全面组装）

我们在本仓库中提供了开箱即用的测试脚本 `scripts/zotero_review_pipeline.py`。

### 5.1 第一步：执行独立测试（无需配置即可运行）
在终端运行：
```bash
python3 scripts/zotero_review_pipeline.py --test-mode
```

**测试效果**：
1. 自动加载模拟的真实 SCI/知网文献数据集。
2. 解析文献核心事实，生成 `outputs/review_test/paper_cards.json`。
3. 构建跨研究横向证据对比矩阵。
4. 综合生成一份标准的 SCI 综述草稿 `outputs/review_test/review_draft.md`。
5. 导出标准 BibTeX 文件 `outputs/review_test/references.bib`。
6. 执行引用核验并输出 `outputs/review_test/verification_report.md`。

运行单元测试套件：
```bash
python3 -m unittest tests/test_zotero_review_pipeline.py
```

### 5.2 第二步：连接本地 Zotero 客户端或本地 PDF 文件夹
当独立测试验证成功后，你可以将你的真实 Zotero 附件或 PDF 文件夹连接进来：

- **方式 A：直接传入存放 PDF 的本地文件夹**
  ```bash
  python3 scripts/zotero_review_pipeline.py --pdf-dir /path/to/your/cnki_pdfs --query "Your Review Topic"
  ```

- **方式 B：直接指定 Zotero 数据目录（自动遍历 storage 附件）**
  ```bash
  python3 scripts/zotero_review_pipeline.py --zotero-dir ~/Zotero --query "Your Review Topic"
  ```

---

## 6. 分支同步与本地 Git 克隆指令

本次所有代码、工具脚本、测试用例与综述编写指南已同步推送至当前会话专属分支 `arena/01a088f7-nature-skills`。

### 本地克隆本分支指令：
```bash
git clone -b arena/01a088f7-nature-skills https://github.com/mqgg5630-cyber/nature-skills.git
cd nature-skills
```

### 如果本地已有仓库，切换并拉取本分支：
```bash
git checkout -b arena/01a088f7-nature-skills origin/arena/01a088f7-nature-skills
git pull origin arena/01a088f7-nature-skills
```
