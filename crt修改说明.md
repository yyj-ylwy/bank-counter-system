# 缺陷修复变更说明（crt修改说明）

> 本次对银行柜面业务管理系统进行了一轮系统性缺陷排查与修复，覆盖**逻辑错误、外键/引用链接、数据类型规范**三类代码问题，并同步修正了《需求规格说明书》中与代码实现不一致之处（以代码为准）。
>
> 排查方式：按维度并行审查全部源码 + 逐条对抗式复核，确认 26 项真实缺陷后逐项修复；修复后经"全模块编译 + 导入 + 逻辑自测 + 逐处回读"验证。
>
> 变更文件：`admin.py`、`constants.py`、`creditcard.py`、`db.py`、`forex.py`、`loan.py`、`savings.py`、`static/operations.js`（共 8 个），以及《需求规格说明书》。

---

## 一、逻辑错误修复（A 组）

| 编号 | 文件 | 问题 | 修复 |
|------|------|------|------|
| L1 | `loan.py` `approve()` | 贷款审批为"待补件(SUPPLEMENT)"后无法再次审批，合同号永久卡死 | 审批入口前置状态放宽为 `status in (PENDING, SUPPLEMENT)`，补件后可继续审批 |
| L2 | `loan.py` `loan_view()` / `static/operations.js` | 催收记录写入 `collection_log` 后任何接口/前端都查不到 | `loan_view` 输出 `collection_log`；前端"催收登记(UC-205b)"结果页新增催收记录表格 |
| L3 | `loan.py` `repay()` | 逾期罚息只在列表现算现抛、从不入账；本金一还清即结清，罚息被免除 | 还款时按"剩余本金 × 日罚息率 × 逾期天数"**实时计算罚息**，罚息优先冲抵、其余冲抵本金；**本金与当期罚息全部还清才置 PAID_OFF**；还款流水明细区分本金/罚息部分 |
| L4 | `admin.py` `upsert_param()` | 非负校验只信任请求携带的 `param_type`，省略即默认 OTHER，可给利率/限额写入负数 | 以库中已存参数类型为权威，并对所有已知数字型参数键（`LOAN_RATE`/`WITHDRAW_DAILY_LIMIT` 等 8 个）建立白名单强制非负校验，无法绕过 |
| L5 | `admin.py` `update_user()` | 管理员可停用/降权自己（含唯一管理员），导致管理功能永久锁死 | 新增**自我锁定保护**（禁止停用/降低自己权限）与**末位管理员保护**（在用管理员仅剩 1 名时禁止降级/停用） |
| L6 | `forex.py` `change()` | 外汇子户变更全程无事务，CLOSE 的"余额=0"判断可被并发 `trade()` 充值后仍关闭，外币余额永久锁死 | 整个变更逻辑包入 `run_in_transaction`，事务内重读子户状态与**余额**再决策，杜绝并发窗口 |
| L7 | `creditcard.py` `cash_advance()` | 预借现金转入储蓄账户不校验账户归属，可转入任意客户账户 | 出款账户增加归属校验（复用 `common.verify_owner`），非持卡人账户返回 `E-OWNER` 拒绝 |
| L8 | `savings.py` `close_account()` | 销户只查贷款/外汇，漏查信用卡，客户有未结清账单也能销户 | 新增 `E-6` 校验：客户存在有效信用卡且有未结清账单（UNPAID/PARTIAL）时拒绝销户 |
| L9 | `forex.py` `open_subaccount()` + `db.py` | 判重与插入非原子(TOCTOU)、索引非唯一，可并发开出多个同币种子户 | 判重+插入包入事务；`db.py` 将 `(customer_id, currency)` 普通索引改为**仅对正常/冻结状态生效的部分唯一索引**，数据库层兜底 |
| L10 | `loan.py` `approve()` | `d.get("approved_amount") or ln["amount"]`：显式传入的 0 被静默替换成原申请全额，绕过校验 | 改用 `not in (None, "")` 判定"是否填写"，`approved_amount`、`term_months` 均同步修正 |
| L11 | `admin.py` `restore()` | 恢复确认 `confirm` 仅做真值判断，字符串 `"false"/"0"` 也被当作已确认放行 | 新增 `_truthy()` 布尔归一化，仅 `1/true/yes/on` 视为已确认 |

## 二、外键 / 引用链接修复（B 组）

| 编号 | 文件 | 问题 | 修复 |
|------|------|------|------|
| F1 | `creditcard.py` `cash_advance()` | 预借现金转入储蓄账户时未写 `account_id`，账户明细查询永远漏这笔入账（账实不符） | 新增业务类型 `CC_CASH_PAYOUT`（"预借现金入账"），在账户加钱后补写一条带 `account_id` 的入账流水，可在储蓄账户明细中追溯 |
| F2 | `savings.py` `close_account()` | 销户事务内只重读余额/状态，未重查贷款/外汇前置条件，并发下产生孤儿贷款/外汇子户 | 事务内用 `session=s` 重做贷款(E-2)、外汇子户(E-3)校验 |
| F3 | `forex.py` `trade()` | 事务内重读了子户状态却仍用事务外旧的 `base_account_id`，REBIND 改绑后可能记错账户 | 事务内按 `fxa["base_account_id"]` 重新取绑定账户 `base_acc` 后再交易 |
| F4 | `common.py` / `creditcard.py` | 归属校验函数 `verify_owner()` 定义了却从未被调用 | 在预借现金出款(L7)接入 `verify_owner`，函数真正落地 |

## 三、数据类型规范修复（C 组）

| 编号 | 文件 | 问题 | 修复 |
|------|------|------|------|
| T1 | `loan.py` `overdue_list()` / `repay()` | `get_param_dec(..., None)` 的 None 被 `dec()` 吞成 `Decimal(0)`，"罚息参数缺失应报错"分支永远走不到，静默算出 0 罚息 | 先用 `get_param` 判存在性、缺失即报 `E-3`，确认存在后再转 `Decimal` |
| T2 | `loan.py` / `creditcard.py` | 还款/账单日志内嵌金额用原生 `float` 存库，破坏"金额一律 Decimal128"不变式 | 还款日志 `repay_log` 内金额改存 `Decimal128`（`m()`），仅在对外序列化时转 float |

## 四、恢复功能补全（S3）与注释订正（S4）

| 编号 | 文件 | 内容 |
|------|------|------|
| S3 | `admin.py` `backup()`/`restore()` | 备份文件在 `_meta` 内嵌入**版本号、MD5 校验值、各集合记录数**；恢复前依次校验版本兼容性、集合格式、**记录数一致性**、**MD5 校验和**；恢复事务内执行**逻辑外键完整性扫描**（account/loan/fx_account/credit_card/credit_card_bill/system_param 六类引用），发现孤儿引用即抛错回滚。对应说明书 UC-504"校验文件完整性与外键完整性"的要求 |
| S4 | `admin.py` | `BACKUP_COLLECTIONS` 注释与代码自相矛盾（注释称"不含 counters"但列表含 counters）；订正注释为准确描述——**含 counters 是有意为之**，使恢复后业务编号与已恢复数据一致、避免编号重复 |

## 五、《需求规格说明书》修改（以代码为准）

| 编号 | 位置 | 修改 |
|------|------|------|
| S1 | 3.3 / 6.x 共 6 处 | "MySQL 5.7 关系型数据库 + 外键约束"更正为"MongoDB（Atlas 文档型数据库）+ ObjectId 逻辑引用，完整性由应用层事务与校验保证"；"数据库建表脚本"改为"数据库初始化脚本（集合与索引）" |
| S2 | 表 3-4 业务流水表 | `related_id` 说明更正：信用卡业务指向 `credit_card`（而非 `credit_card_bill`），账单关联通过 `bill_cycle` 字段；补充 `currency`、`cny_amount`、`bill_cycle` 三个代码实际写入的动态字段 |
| S5 | 表 3-5 贷款表 | 补 `purpose`、`guarantee`、`due_date`、`repay_method`、`reject_reason`、`supplement_note`、`disbursed_at`、`collection_log`、`created_at` 九个字段 |
| S6 | 表 3-7 信用卡表 | 补 `card_type`、`occupation`、`monthly_income`、`reject_reason`、`former_card_nos`、`exception_log`、`created_at` 七个字段 |
| S7 | 表 3-8 账单表 | 补 `min_repay`（最低还款额）、`created_at` |
| S8 | 表 3-2/3-3/3-6 | 客户表补 `address`、`occupation`、`created_at`；账户表、外汇子户表各补 `created_at` |
| S9 | 表 3-5/3-7 | 状态机文案与代码对齐（贷款补 REJECTED/SUPPLEMENT，信用卡补 FROZEN/LOST/INVALID 流转） |

> 说明书原件已备份为 `230710_3_需求规格说明书(3)(1)_backup_20260710_172630.docx`。

## 六、验证

- 全部 `.py` 通过 `py_compile`；11 个模块逐一 `import` 无语法/名称错误。
- 纯逻辑自测 `python test_logic.py` 全部通过。
- 逐处回读修改，确认与设计一致；说明书重新抽取核对内容与格式（克隆行字体与原行一致）。

## 七、重要说明

1. **罚息模型（L3）**：采用"还款时实时计算、罚息优先冲抵、本息全清才结清"的无状态模型，改变了原先"只还本金即结清"的行为，符合说明书 UC-204/205"应还含罚息"的口径。
2. **外汇唯一索引（L9）**：系统启动时会先删旧索引再建部分唯一索引（已容错，不阻断启动）。**若数据库中已存在同客户同币种的多个有效子户（历史脏数据），该唯一索引会创建失败并被忽略**，建议上线前先排查清理此类重复数据。
