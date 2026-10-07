# 部署与运维

## 前提与隔离

当前支持 Linux x86_64，验证使用 Python 3.10、Node.js 24.21.0、PostgreSQL 18.6。系统需提供 Python、curl、tar、gcc、make、zlib/OpenSSL 开发库及 apt-get download；脚本不使用 sudo，不更换系统运行时。若主机缺少编译依赖，先由运维准备依赖，不绕过权限修改系统。

运行时、数据库、私有 Socket 和 Supervisor 配置位于部署账号的 ~/.local/share/ecom-v3/。账号自行创建的数据、原件和日志归项目 data/；整个目录忽略，不在 GitHub 中提供。

演示、正式、测试使用不同数据库、配置、附件目录与会话 Cookie；禁止测试借用正式库。

## 首次安装

~~~bash
git clone https://github.com/SenpeWang/test.git
cd test
bash scripts/install_runtime.sh
cp .env.example .env
chmod 600 .env
# 在编辑器填写 API_KEY；密钥不放进命令行或提交
.venv-v3/bin/python scripts/manage_database.py provision
ECOM_V3_CONFIG="$PWD/.env.v3-demo" .venv-v3/bin/python scripts/manage_database.py demo
.venv-v3/bin/python scripts/configure_services.py
make build-web
make start
make status
~~~

安装使用锁定的项目依赖，首次创建隔离虚拟环境。现有部署环境不因重新安装而被删除。provision 建立三个隔离数据库及 600 权限环境文件，运行 Alembic 迁移；现有环境文件和已存在角色密码不覆盖。

演示初始化只在 demo/test 模式允许，建立组织、四身份和明确标注的权限样例。公开仓库没有商品文档，需通过文档库授权上传；资料不足不能生成产品或业务事实。

## 正式初始化

新项目没有有效老板时使用受保护工具初始化。迁移已有旧系统数据时先确定写入冻结、权限映射、恢复条件，再执行 migrate；没有旧数据时该命令创建初始企业，但不会凭空创建旧账号。

~~~bash
ECOM_V3_CONFIG="$PWD/.env.v3-production" .venv-v3/bin/python scripts/manage_database.py migrate
ECOM_V3_CONFIG="$PWD/.env.v3-production" .venv-v3/bin/python scripts/manage_database.py bootstrap-owner
~~~

一次性 owner 凭据只保存于部署账号私有的 bootstrap-owner-企业代码.json，权限 600，首次登录修改密码；工具不输出密码。正式环境拒绝演示口令。后续恢复和关键身份变更由现有老板批准，管理员不能继承其权限。

演示端口 18501、正式端口 18080、数据库端口 15432 都监听回环地址。远端使用自己的 SSH 转发。若配置 HTTPS 对外服务，在对应环境设置 V3_COOKIE_SECURE=true，并落实反向代理、访问入口和正式授权；演示服务不向公网开放。

## 测试

provision 为本地生成 .env.v3-test 与 ecom_v3_test。执行：

~~~bash
make test
make build-web
make verify-browser
~~~

测试会清空独立测试库；模块首先要求 test 模式及数据库名 ecom_v3_test。合成事实在测试代码中构造，不从 data/ 读取企业文件；API_KEY 为空，slow 用例排除。

GitHub CI 使用自己的 PostgreSQL 服务库与临时配置，不读取部署凭据。make install-hooks 在每份克隆启用提交/推送检查；make check-repository 检查已提交文件。

## 启停、任务与构建

make start、make status、make stop 管理 Supervisor 进程，停止应用保留数据库。服务退出 SSH 后继续运行。自动开机启动项须由部署者按主机策略安排，不在安装脚本中改写其他人的 crontab。

API 与 Worker 按环境分进程；数据库守护检查数据库。任务采用 SKIP LOCKED、租约、心跳和执行版本，最多三次尝试；跨重试模型预算默认最多六次，总时限十分钟。恢复重新验证身份和来源，旧执行不能发布。网络调用取消后仍须在返回时校验。

make build-web 使用 npm ci 的锁定依赖和类型检查，在临时目录构建成功再切换文件，清理临时目录；既有页面的有效静态资源不能作为备份随意删除。

## 模型与只读业务接口

.env 设置 Sensenova 的 BASE_URL、API_KEY 和 MODEL，默认端点 https://token.sensenova.cn/v1，模型 sensenova-6.8-flash-lite。请求禁用系统代理与重定向。老板级資料外发需要老板批准 AI 许可与本次问题许可。

业务接口通过私有 BUSINESS_API_URL、BUSINESS_API_TOKEN 配置，固定 HTTPS GET /stock/{sku_id} 与 /orders/{order_id}；不接受用户指定 URL，不进行退款、改价或订单修改。

接口记录必须匹配当前企业和组织、请求编号、人员范围及带时区的数据时间；默认不早于 60 秒，时钟漂移最多 5 秒。库存和金额为非负整数，金额为人民币分、currency=CNY。电话、地址和姓名脱敏；采购成本仅授权老板可见，不发送外部模型。断连、过时、非法或未接入时明确说明，不生成虚构记录。

## 迁移、恢复与私有数据

保留旧账号密码哈希与状态。旧共享资料保持原共享边界，管理层资料按老板级受限，指定授权保留名单并审计；无可靠版本来源的旧答案待重新核验，旧运行中检查点不直接续用。迁移与 archive-tasks 只在明确的旧库迁移场景运行。

默认不创建 bak、源码快照或自动备份。用户明确要求时，backup_database.py create/verify 才会生成 dump、附件归档与摘要清单；输出属私有数据，独立数据库恢复校验，不覆盖在线库。删除旧备份后不能把它当作现存回退条件。

正式运维应另行确定备份保留、异机存放、恢复时间与数据损失目标并实际演练。本项目当前没有实现多机高可用、企业 SSO、异机备份或真实业务写操作。
