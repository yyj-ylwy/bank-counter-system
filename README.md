# 银行柜面业务管理系统（Bank Counter Business Management System）

北京工业大学 软件工程课程设计 · 230710 班第 3 组
按《需求规格说明书》实现的银行柜面业务系统，覆盖储蓄、贷款、外汇、信用卡四大业务子系统与系统管理子系统，共 **5 类角色、29 个用例、10 张数据集合**。

## 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 后端 | Python + Flask | 轻量、可读，与课程范例一致 |
| 数据库 | MongoDB（PyMongo） | 资金类操作用事务保证原子性 |
| 前端 | 原生 HTML/CSS/JS | 无需构建，声明式表单引擎 |
| 部署 | Render + GitHub | 一键 Blueprint 部署 |

## 功能一览

- **储蓄业务员**（UC-101~108）：开户、存款、取款、转账（行内/跨行/同户）、账户明细查询、挂失/解挂/补卡、销户、客户信息更新
- **贷款业务员**（UC-201~206）：申请、审核审批、放款、还款登记、逾期管理、查询统计
- **外汇业务员**（UC-301~305）：外汇子户开立、汇率查询确认、外汇买卖、账户变更、余额历史查询
- **信用卡业务员**（UC-401~406）：申请、额度审批、账单生成、还款（全额/最低/部分）、预借现金、挂失补卡异常处理
- **系统管理员**（UC-501~504）：用户权限管理、基础参数维护、日志审计、数据备份与恢复

四个公共用例（UC-INC-1~4：身份核验、账户校验、流水登记、审计日志）实现于 `common.py`，被各子系统复用。

## 演示账号（首次启动自动创建）

| 工号 | 密码 | 角色 |
|------|------|------|
| admin | admin123 | 系统管理员 |
| S001 | 123456 | 储蓄业务员 |
| L001 | 123456 | 贷款业务员 |
| F001 | 123456 | 外汇业务员 |
| CC001 | 123456 | 信用卡业务员 |

系统还会自动创建 2 个演示客户（张三 / 李四）及其储蓄账户，方便直接演示。
> 生产环境请尽快用管理员修改这些默认密码。

## 目录结构

```
bank-system/
├── app.py            # Flask 入口：注册蓝图、JSON 编码、启动建索引+种子
├── config.py         # 环境变量配置
├── constants.py      # 角色/状态/流水类型/参数键 等常量
├── db.py             # Mongo 连接、事务封装、序列号生成、索引
├── common.py         # 金额/响应工具 + 4 个公共用例
├── auth.py           # 登录、令牌、角色装饰器
├── seed.py           # 幂等种子数据
├── savings.py        # 储蓄子系统 UC-101~108
├── loan.py           # 贷款子系统 UC-201~206
├── forex.py          # 外汇子系统 UC-301~305
├── creditcard.py     # 信用卡子系统 UC-401~406
├── admin.py          # 系统管理子系统 UC-501~504
├── test_logic.py     # 纯逻辑自测
└── static/           # 前端（index.html / style.css / app.js / operations.js）
```

## 本地运行

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # 然后编辑 .env 填入 MONGODB_URI
python app.py                     # 访问 http://localhost:5000
```

## 获取 MongoDB Atlas 连接串

在 Atlas 的 **Connect → Drivers and Client Libraries** 页面（你现在这一步就选它）：
1. Driver 选 **Python**，Version 选 **3.12 or later**；
2. 复制形如下面的连接串，把 `<db_password>` 换成你的数据库用户密码：
   ```
   mongodb+srv://<db_user>:<db_password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0
   ```
3. **重要**：到左侧 **Network Access → Add IP Address**，点 **ALLOW ACCESS FROM ANYWHERE**（`0.0.0.0/0`）。因为 Render 的出口 IP 不固定，只白名单你本机 IP 的话线上连不上数据库。

## 部署到 Render

方式一（推荐，一键）：
1. 把本仓库推到 GitHub；
2. Render → **New → Blueprint**，选中本仓库，Render 会读取 `render.yaml`；
3. 在提示处填入 `MONGODB_URI`（上面那串），其余变量自动带出；
4. 部署完成后访问分配的域名即可。

方式二（手动 Web Service）：
- Build Command：`pip install -r requirements.txt`
- Start Command：`gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- 环境变量：`MONGODB_URI`（必填）、`DB_NAME=bank_counter`、`SECRET_KEY`（任意随机串）

## 安全说明

- 业务员密码以哈希（werkzeug）存储；令牌用 itsdangerous 签名，默认 8 小时过期。
- 客户不直接登录，所有业务由柜面业务员/管理员代理办理。
- 所有操作写审计日志（操作人、对象、结果、时间）；资金变动写业务流水，历史数据不物理删除。
- `.env` 已被 `.gitignore` 忽略，数据库密码不会进入仓库。
```
