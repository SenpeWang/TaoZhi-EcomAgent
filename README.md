# 淘智 · TaoZhi-EcomAgent

[![main 分支 CI](https://github.com/SenpeWang/TaoZhi-EcomAgent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SenpeWang/TaoZhi-EcomAgent/actions/workflows/ci.yml)

淘智是面向手机配件电商团队的 AI 工作台。客服可以核对商品规格、适配机型和售后条款，运营可以查询经营指标，选品团队可以跟进竞品和供货线索。

商品资料随型号、渠道和版本变化，经营数据也有各自的权限和统计口径。淘智把找资料、看数据和做调研放在同一工作台，帮助团队确认结论的依据、适用范围和待核实事项。

## 工作台能力

| 场景 | 可以做什么 |
| --- | --- |
| 知识问答 | 从有权访问的企业资料中核对商品参数、机型适配和售后政策。答复附引文，资料不足或冲突时提示需要补充的依据。 |
| 经营问数 | 用自然语言查询获授权的经营指标，查看结果、数据来源与统计口径。 |
| 选品调研 | 由智能体分工收集市场信息和货源线索，查看调研进度与证据，并在执行中补充要求。 |
| 资料治理 | 管理资料上传、版本修订和发布审核，按组织、密级及个人授权控制使用范围。 |
| 运行分析 | 查看模型调用、工具执行和任务结果，比较评测批次与基线，定位回归和失败用例。 |

## 系统架构

[![淘智系统架构，展示工作台、API、任务执行、知识检索和运行观测之间的关系](web/src/assets/system-architecture.svg)](web/src/assets/system-architecture.svg)

- **工作台与服务**采用 Vue 和 FastAPI。后台任务处理文档解析、问答与调研，工作台实时显示进度和结果。
- **知识与数据**以 PostgreSQL 管理业务记录和权限。知识问答结合关键词与向量检索，经营问数使用经过治理的指标口径。
- **应用编排**使用 LangGraph 组织检索、核验和多智能体调研。需要复核的结果进入审核流程。
- **运行观测**使用 OpenTelemetry 记录模型与工具调用，关联任务、运行过程和错误。评测中心用基线和用例衡量工作流表现。

## 权限与轨迹

文档查阅、原件下载和外部模型处理分别按权限控制。答案引用支持回到文档版本和对应片段。店铺政策、定价、库存与订单结论以已授权的内部资料或业务记录为依据。

管理员可以在本租户内检查运行记录。普通成员可以回看自己的任务，追查执行步骤以及权限范围内的来源。

## 手机配件 PDF 语料

仓库随附可直接使用的原版 PDF，覆盖充电、线材、手机壳膜、支架和音频配件。语料按品类与来源登记，便于整理知识库资料或准备向量索引。[查看 PDF 原件](data/accessory_pdfs/)

## 文档索引

| 要了解什么 | 文档 |
| --- | --- |
| 体验工作台 | [本地演示](documentation/quickstart.md) |
| 准备资料、入库及核对答复依据 | [手机配件资料与问答](documentation/knowledge-base.md) |
| 理解源码分层和数据模型 | [目录契约](STRUCTURE.md) · [数据库模型](documentation/database.md) |
| 配置、部署与运维 | [部署与运维手册](documentation/deployment.md) |
| 权限、审计与隔离边界 | [权限与文档治理](documentation/access-control.md) |
| 参与开发与提交规范 | [贡献规范](CONTRIBUTING.md) |
| 运行评测和对照基线 | [评测指南](evals/README.md) · [基准与门禁](evals/BENCHMARK.md) |

## 许可证

仓库尚未提供项目级许可证。第三方资料的署名和适用许可随语料登记。[PDF 署名与许可](data/accessory_pdfs/README.md)
