# ARTA (Academic-Review-Thesis-Agent) 与 Nature-Skills 综述集成规范

## 1. 架构定位
本集成规范定义了 **ARTA** 与 **Nature-Skills** 之间的协议映射与执行流。Nature-Skills 作为 S3 智能综述合成层的核心引擎，负责将 S1/S2 采集的知网/SCI PDF 解析为结构化事实证据，并输出符合 S4 (DualTrackWordCompiler)、S5 (LarkThesisFormatter) 与 S6 (PPTRouter) 契约的标准 Payload。

## 2. 核心 Skills 协同矩阵

| 阶段 | 调用的 Skill | 核心职责 | 输入产物 | 输出产物 |
|---|---|---|---|---|
| **S1 / S2** | `cnki-zotero-auto-ingest` / `zotero` MCP | 文献捕获、Zotero 23119 入库与 CSL 提取 | 知网/SCI PDF、DOI | `PaperItem` 列表、真实 `item_key` |
| **S3 事实提取** | `nature-paper-card` / `nature-reader` | 01-16 节事实提取、量化指标与页码锚定 | 原始论文 PDF | `PaperCard` (包含痛点、方法、指标、机理、边界) |
| **S3 横向对比** | `nature-literature-pipeline` | 三线对比表合成、学术争议与空白识别 | `PaperCard` 列表 | 表 1-1 科技三线对比表、争议分析 |
| **S3 章节起草** | `nature-writing` (`paper_type: review`) | 模式一多级编号综述撰写 (消除流水账) | 事实对比矩阵 | `thesis_chapter1_review.md` |
| **S4 活体编译** | `zotero-academic-writer` | CSL-JSON 复杂域注入 | `PaperItem` + 章节 Markdown | `arta_synthesis_payload.json` -> Word |
| **S5 论文排版** | `lark-thesis-formatter` | 高校标准排版 (鲁东大学模板等) | Word 底本 | 最终排版学位论文 `.docx` |
| **S6 幻灯片路由** | `ppt-master` / `dashi-ppt` | 答辩演示文稿编译 (字号 $\ge 18\text{pt}$) | 幻灯片大纲 | `arta_ppt_payload.json` -> 15页 `.pptx` |
| **QA 核验** | `nature-ref-verifier` / `vlm-checkpoint` | 引用真实性校验与 PPT 视觉重叠审计 | 最终交付物 | 零幻觉报告、无文字截断报告 |

## 3. SCI 真实内容量化审计标准 (Five-Pillar Checklist)

为确保生成的综述达到顶刊 SCI 与高质量学位论文标准，必须通过以下 5 维硬性审计：

1. **定量指标溯源度 ($\ge 90\%$)**：正文中出现的每一个数值（准确率、结合能、反应温度、阈值、误差等）必须在原 PDF 的具体图表或页码中有据可查。
2. **底层构效因果链**：段落必须解释“为什么该结构产生该性能”（例如：N 端 Asp/Glu 残基通过双重盐桥锁定受体活性口袋）。
3. **批判性边界与局限性**：明确指出各文献在样本量、温度、过拟合风险或计算耗时上的局限。
4. **非流水账式主题论证**：同一段落中融合多篇文献的横向对比与技术演进，严禁单篇独立罗列。
5. **活体引用与零虚假文献**：所有 `[UMAMI_001]` 引用必须 100% 对应 Zotero 数据库中的真实元数据。
