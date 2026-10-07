# 企业工作台 v3 重构

当前正式实现与启动方式已升级至 Vue 3、FastAPI、LangGraph、PostgreSQL 和独立 Worker。历史 Streamlit、SQLite 在线服务已停止，旧代码只用于隔离离线回归，不作为生产入口。

请阅读：

- [使用与入口](../README.md)
- [权限和文档治理](ACCESS_CONTROL.md)
- [部署、备份与回退](ENTERPRISE_V3.md)
- [实际验收记录](VALIDATION.md)

管理员、员工、组长和老板有不同业务范围。身份、资料密级、组织范围及单文档授权由后端统一校验；审核不转借审核人权限，例外授权不向员工授予编辑能力。演示和正式数据隔离。
