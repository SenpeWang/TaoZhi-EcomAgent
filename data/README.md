# 数据目录说明

本目录记录公开语料、演示和评测夹具，以及本机运行数据的存放位置。[项目结构](../STRUCTURE.md)

| 目录 | 内容 |
| --- | --- |
| `accessory_pdfs/` | 原始手机配件 PDF、可追溯来源清单及各文档许可 |
| `private/accessory_pdfs/` | 本机留存的厂商原件和清单，仅用于授权范围内的内部查阅 |
| `corpus/<category>/` | 按分类保存的演示商品语料 |
| `incoming/<tenant_id>/` | 待解析的企业附件 |
| `private/<mode>/` | 文件存储适配器保存的授权入库原件 |
| `runtime/`、`reports/<tenant_id>/` | 本机运行记录与业务报告 |
| `index/`、`embeddings/`、`graph/` | 演示初始化资料和各环境的派生索引 |

## 配件资料

公开目录内可直接读取 PDF 原件。核对版本与 SHA-256 后，可自行按页切片和生成词嵌入。本机私有目录中的原件及其使用条件由资料维护者保管。[原件、来源与署名](accessory_pdfs/)

企业附件、运行记录和派生索引由对应环境保管。上传权限、生效流程与问答验收见资料手册。[资料与问答手册](../documentation/knowledge-base.md)
