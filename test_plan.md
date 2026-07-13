# 银行柜面系统 - 全量测试计划（白盒 + 黑盒）

> 测试环境: https://bank-counter-system.onrender.com  
> 测试日期: 2026-07-13  
> 设计原则: 覆盖所有角色 × 所有操作 × 正向/异常/边界路径

## 测试数据

| 标识类型 | 值 | 归属客户 | 说明 |
|----------|-----|----------|------|
| 证件号 | 110101199001011234 | 张三 | 演示客户1 |
| 证件号 | 110101199203054321 | 李四 | 演示客户2 |
| 邮箱 | zhangsan@example.com | 张三 | |
| 邮箱 | lisi@example.com | 李四 | |
| 手机号 | 13800000001 | 张三 | |
| 手机号 | 13800000002 | 李四 | |

## 测试用例（共 56 条）

### 一、登录与鉴权（5 条）

| # | 用例 | 操作 | 输入 | 预期 |
|---|------|------|------|------|
| A01 | 正确登录-admin | POST /api/login | admin/admin123 | 200, 返回token, role=ADMIN |
| A02 | 正确登录-S001 | POST /api/login | S001/123456 | 200, role=SAVINGS_CLERK |
| A03 | 正确登录-L001 | POST /api/login | L001/123456 | 200 |
| A04 | 错误密码登录 | POST /api/login | admin/wrong | success=false, E-1 |
| A05 | 未登录访问API | GET /api/me | 无token | 401 |

### 二、储蓄业务（17 条）

#### UC-102 存款（5条）
| # | 用例 | ident 输入 | amount | 预期 |
|---|------|-----------|--------|------|
| B01 | 用证件号定位存款 | 110101199001011234 | 100 | 200, 存款成功 |
| B02 | 用邮箱定位存款 | zhangsan@example.com | 200 | 200, 存款成功 |
| B03 | 用手机号定位存款 | 13800000001 | 150 | 200, 存款成功 |
| B04 | 用账号定位存款 | (张三账号) | 300 | 200, 存款成功 |
| B05 | ident不存在 | 999999999999999999 | 100 | success=false, E-NOCUST |
| B06 | 金额为0 | 110101199001011234 | 0 | success=false, E-2 |
| B07 | 金额为负 | 110101199001011234 | -50 | success=false |
| B08 | ident为空 | (空) | 100 | success=false, E-ID |

#### UC-103 取款（3条）
| B09 | 正常取款 | 110101199001011234 | 50 | 200, 取款成功 |
| B10 | 余额不足 | 110101199001011234 | 999999 | success=false, E-BAL |
| B11 | 超单日限额 | 110101199001011234 | 60000 | success=false |

#### UC-104 转账（3条）
| B12 | 本行转账(用to_ident) | ident=张三, to_ident=李四证件号 | 100 | 200, 转账成功 |
| B13 | 同一账户转账 | ident=张三, to_ident=张三 | 100 | success=false, E-3 |
| B14 | 跨行转账 | INTER, to_account_no=ext123, to_bank=工商银行 | 200 | 200, 含手续费 |

#### UC-105 查询（4条）
| B15 | 用证件号查询 | 110101199001011234 | - | 200, 返回客户+账户+流水 |
| B16 | 用手机号查询 | 13800000001 | - | 200, 返回客户+账户+流水 |
| B17 | 用账号查询 | (张三账号) | - | 200, 返回客户+账户+流水 |
| B18 | 不存在标识查询 | 000000000000000000 | - | success=false, E-1 |

#### UC-106 卡操作（3条）
| B19 | 挂失 | ident=张三证件号, op=LOSS | - | 200, 挂失成功 |
| B20 | 解挂 | ident=张三证件号, op=UNLOSS | - | 200, 解挂成功 |
| B21 | 重复挂失 | ident=张三证件号, op=LOSS (两次) | - | 第二次 success=false, E-2 |

#### UC-108 客户信息更新（2条）
| B22 | 更新手机号 | ident=张三证件号, phone=13900000001 | - | 200, 更新成功 |
| B23 | 更新为已占用邮箱 | ident=张三证件号, email=lisi@example.com | - | success=false |

### 三、贷款业务（9 条）

| C01 | 贷款申请 | ident=张三证件号, type=个人消费贷, amount=50000, term=12 | - | 200, PENDING |
| C02 | 贷款申请-黑名单 | (无黑名单客户, 跳过) | - | |
| C03 | 审批通过 | contract_no=C01返回, decision=APPROVED, approved_amount=50000, interest_rate=0.05 | - | 200, APPROVED |
| C04 | 审批拒绝 | (新申请) decision=REJECTED, reason=资料不足 | - | 200, REJECTED |
| C05 | 放款 | contract_no=C01返回 | - | 200, ACTIVE |
| C06 | 重复放款 | contract_no=C01返回(再次) | - | success=false, E-2 |
| C07 | 还款 | ident=张三证件号, amount=10000 | - | 200, 还款成功 |
| C08 | 超额还款 | ident=张三证件号, amount=99999999 | - | success=false, E-3 |
| C09 | 逾期查询 | GET, days=0 | - | 200 |

### 四、外汇业务（5 条）

| D01 | 开立外汇子户 | ident=张三证件号, currency=USD | - | 200 |
| D02 | 重复币种开户 | ident=张三证件号, currency=USD (再次) | - | success=false, E-2 |
| D03 | 实时汇率查询 | GET, currency=所有 | - | 200, 9种币种行情 |
| D04 | 外汇买入 | ident=张三证件号, currency=USD, direction=BUY, amount=100 | - | 200 |
| D05 | 外汇卖出-余额不足 | ident=张三证件号, currency=JPY, direction=SELL, amount=999999 | - | success=false |

### 五、信用卡业务（8 条）

| E01 | 信用卡申请 | ident=张三证件号, card_type=普卡 | - | 200, PENDING |
| E02 | 审批通过 | card_no=E01返回, decision=APPROVED, credit_limit=10000 | - | 200, ACTIVE |
| E03 | 预借现金 | ident=张三证件号, amount=2000 | - | 200 |
| E04 | 预借现金-额度不足 | ident=张三证件号, amount=99999 | - | success=false, E-2 |
| E05 | 账单生成 | card_no=E01返回, bill_cycle=(当前年月) | - | 200 |
| E06 | 信用卡还款 | ident=张三证件号, repay_type=FULL | - | 200, 还款成功 |
| E07 | 无账单还款 | ident=张三证件号, repay_type=FULL (再次) | - | success=false, E-3 |
| E08 | 挂失 | ident=张三证件号, op=LOSS | - | 200 |

### 六、系统管理（6 条）

| F01 | 用户列表 | GET /api/admin/users | - | 200, 5个用户 |
| F02 | 创建用户 | POST, employee_no=TEST01, name=测试员, role=SAVINGS_CLERK, password=123456 | - | 200 |
| F03 | 重复工号创建 | 同上 | - | success=false, E-1 |
| F04 | 参数列表 | GET /api/admin/params | - | 200, 多个参数 |
| F05 | 修改参数 | param_key=WITHDRAW_DAILY_LIMIT, param_value=60000 | - | 200 |
| F06 | 审计日志查询 | GET /api/admin/audit | - | 200 |

### 七、跨角色权限（3 条）

| G01 | 储蓄员访问贷款API | S001 token, POST /api/loan/apply | - | 403, E-PERM |
| G02 | 贷款员访问储蓄API | L001 token, POST /api/savings/deposit | - | 403 |
| G03 | 储蓄员访问管理API | S001 token, GET /api/admin/users | - | 403 |

### 八、储蓄销户（1 条 - 需状态依赖）
| H01 | 销户-有未结清贷款 | ident=张三证件号 | - | success=false, E-2 |

---

**总计: 56 条测试用例**
- 白盒路径: A04-A05, B05-B08, B10-B11, B13, B18, B21, B23, C06, C08, D02, D05, E04, E07, F03, G01-G03
- 黑盒功能: 其余全部
