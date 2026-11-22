# 真实业务台账字段字典（模板口径）

> 类别：制度性说明（随模板交付）。时间：2026-10-07。
> 用途：企业后续提供真实导出时的统一清洗口径。全部金额为人民币分（整数），时间为 ISO 8601 带时区。

## products.csv（在售商品台账）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| sku_id | string | 公司内部 SKU 唯一标识（主键） |
| product_name | string | 商品名称 |
| brand | string | 品牌 |
| model | string | 厂商准确型号 |
| category | enum | 手机壳/保护膜/充电器/数据线/无线充电器/支架/车载配件/手机 |
| purchase_cost_minor | int | 采购成本（分），老板级字段 |
| list_price_minor | int | 标价（分） |
| status | enum | on_sale/off_sale/draft |
| updated_at | datetime | 最后变更时间（带时区） |

## inventory.csv（库存台账）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| sku_id | string | 外键 → products.sku_id |
| warehouse_id | string | 仓库标识 |
| on_hand | int | 现存 |
| occupied | int | 占用（已售未出库） |
| available | int | 可售 = on_hand - occupied，必须 ≥0 |
| in_transit | int | 在途 |
| snapshot_at | datetime | 快照时间（带时区），非实时承诺 |

## orders.csv（订单台账）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| order_id | string | 订单号（主键，平台单号与内部单号需映射表） |
| sku_id | string | 外键 → products.sku_id |
| quantity | int | 数量，>0 |
| goods_amount_minor | int | 商品金额（分） |
| paid_amount_minor | int | 实付金额（分） |
| refunded_amount_minor | int | 累计退款（分），≤ paid_amount_minor |
| status | enum | pending/paid/processing/shipped/delivered/completed/cancelled/refunding/refunded/closed/returned |
| assigned_user_id | string | 分配员工，必须在在职名单内 |
| warehouse_id | string | 发货仓 |
| created_at/paid_at/shipped_at/completed_at | datetime | 状态时间，必须满足先后顺序 |
| exported_at | datetime | 导出时间 |

## aftersales.csv（售后台账）

after_sales_id（主键）、order_id（外键）、type（refund/return/repair/consult）、reason、refunded_amount_minor（≤ 实付）、status（pending/approved/rejected/completed）、applied_at、closed_at、handler_user_id。

## purchases.csv（采购台账，老板级）

purchase_id（主键）、sku_id（外键）、supplier_id、quantity、unit_cost_minor（分）、total_cost_minor（=quantity×unit_cost，容许运费分摊差额并附说明列）、status（draft/approved/received/cancelled）、ordered_at、received_at。

## finance.csv（财务流水，老板级）

txn_id（主键）、biz_type（order/refund/purchase/fee）、biz_id（外键）、amount_minor（分，退款/采购为正数支出，方向由 direction 字段区分，不用负数表达方向）、direction（in/out）、occurred_at、account、exported_at。

## 通用校验规则

1. 所有 *_minor 字段必须是非负整数；方向用 direction 表达。
2. refunded_amount_minor ≤ paid_amount_minor；售后退款 ≤ 对应订单实付。
3. 状态迁移时间必须满足：created < paid < processing < shipped < delivered < completed；cancelled/refunding 按业务规则单独校验。
4. 外键缺失、重复主键、时间冲突一律隔离到 `待核验/`，不得静默删除。
5. 客户姓名/电话/地址进入智能体前脱敏；采购成本与利润仅老板可见。
