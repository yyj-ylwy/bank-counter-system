"""数据库层：MongoDB 连接、事务封装、序列号生成、索引。

- 资金类操作用 run_in_transaction 保证原子性（对应需求 5.1 / 6.4：整体提交或整体回滚）。
- 业务编号（客户号/账号/卡号/流水号等）用 counters 集合自增，模拟 SRS 里的自增主键，且可读。
"""
from pymongo import MongoClient, ASCENDING, ReturnDocument
from pymongo.errors import OperationFailure, ConnectionFailure

import config
import constants as C

_client = None


def get_client():
    """惰性创建单例连接。"""
    global _client
    if _client is None:
        _client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=8000)
    return _client


def get_db():
    return get_client()[config.DB_NAME]


# ---- 序列号：原子自增，生成可读业务编号 ----
def next_seq(name, session=None):
    doc = get_db().counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    return doc["seq"]


def gen_no(prefix, seq_name, width=8, session=None):
    """生成形如 C00000001 的业务编号。"""
    return f"{prefix}{next_seq(seq_name, session):0{width}d}"


# ---- 事务封装 ----
# ponytail: Atlas（副本集）原生支持事务；本地单机 mongod 不支持，探测到后自动降级为无事务执行，
#           以便本地也能跑起来做演示。生产部署在 Atlas 上事务正常生效。
_txn_supported = None


def _no_txn_support(err):
    # 仅当明确是「非副本集/单机」时才降级；不因某个操作在事务内非法(如 code 263)而误判，
    # 否则会把整个进程永久降级为无事务，破坏资金操作的原子性。
    msg = str(err)
    return ("Transaction numbers are only allowed" in msg
            or "replica set member or mongos" in msg
            or "Transactions are not supported" in msg)


def txn_supported():
    """探测当前 MongoDB 是否支持事务（Atlas 副本集支持，本地单机 mongod 不支持）。
    供破坏性全量操作(如数据恢复)在无事务时拒绝执行，避免半恢复损坏且谎报回滚。"""
    global _txn_supported
    if _txn_supported is not None:
        return _txn_supported
    client = get_client()
    try:
        with client.start_session() as session:
            with session.start_transaction():
                get_db().system_param.find_one({}, session=session)  # 事务内做一次读来触发探测
        _txn_supported = True
    except OperationFailure as err:
        _txn_supported = not _no_txn_support(err)
    except Exception:  # noqa: BLE001 探测失败不武断降级（Atlas 上按支持处理）
        _txn_supported = True
    return _txn_supported


def run_in_transaction(fn):
    """在事务中执行 fn(session)。fn 内所有数据库操作都要带上 session=session。"""
    global _txn_supported
    client = get_client()
    if _txn_supported is False:
        return fn(None)
    try:
        with client.start_session() as session:
            return session.with_transaction(lambda s: fn(s))
    except OperationFailure as err:
        if _no_txn_support(err):
            _txn_supported = False
            return fn(None)
        raise


def ensure_indexes():
    """启动时创建唯一/常用索引，保证业务唯一键约束（对应 SRS 的 UK/FK 完整性）。"""
    db = get_db()
    # 先预建集合：确保事务内首次写入(如 counters 自增 upsert)不会触发「事务内隐式建集合」限制
    existing = set(db.list_collection_names())
    for c in ("counters", "user_account", "customer", "account", "business_transaction",
              "loan", "fx_account", "credit_card", "credit_card_bill", "audit_log", "system_param"):
        if c not in existing:
            try:
                db.create_collection(c)
            except Exception:
                pass
    db.user_account.create_index([("employee_no", ASCENDING)], unique=True)
    db.customer.create_index([("customer_no", ASCENDING)], unique=True)
    db.customer.create_index([("id_no", ASCENDING)], unique=True)  # 证件号全局唯一
    db.customer.create_index([("email", ASCENDING)], unique=True, sparse=True)  # 邮箱唯一（仅对已登记邮箱的客户生效）
    db.customer.create_index([("phone", ASCENDING)], unique=True, sparse=True)  # 手机号全局唯一（选填）
    db.account.create_index([("account_no", ASCENDING)], unique=True)
    db.account.create_index([("card_no", ASCENDING)], unique=True)
    # 客户↔账户 1:1：同一客户只能有一个在用（非销户）储蓄账户
    # 先删旧的非唯一索引（历史遗留），再建 partial unique，避免同字段重复索引
    if "customer_id_1" in [ix["name"] for ix in db.account.list_indexes()]:
        db.account.drop_index("customer_id_1")
    db.account.create_index([("customer_id", ASCENDING)], unique=True,
                            name="uk_account_customer_active",
                            partialFilterExpression={"status": {"$ne": C.ACCOUNT_CLOSED}})
    db.business_transaction.create_index([("txn_no", ASCENDING)], unique=True)
    db.business_transaction.create_index([("account_id", ASCENDING), ("txn_time", ASCENDING)])
    db.business_transaction.create_index([("customer_id", ASCENDING)])
    # 账单生成按 (related_id, business_type) 汇总未入账流水；加索引避免全表扫描
    db.business_transaction.create_index([("related_id", ASCENDING), ("business_type", ASCENDING)])
    db.loan.create_index([("contract_no", ASCENDING)], unique=True)
    db.loan.create_index([("customer_id", ASCENDING)])
    db.fx_account.create_index([("fx_account_no", ASCENDING)], unique=True)
    # 删旧的非唯一索引（历史遗留），仅当存在才删，避免每次启动都抛/吞异常
    if "customer_id_1_currency_1" in [ix["name"] for ix in db.fx_account.list_indexes()]:
        db.fx_account.drop_index("customer_id_1_currency_1")
    try:
        db.fx_account.create_index([("customer_id", ASCENDING), ("currency", ASCENDING)], unique=True,
                                   name="uk_fx_customer_currency_active",
                                   partialFilterExpression={"status": {"$in": [C.FX_NORMAL, C.FX_FROZEN]}})
    except Exception as e:  # noqa: BLE001 - 建索引失败(多为历史重复脏数据)不阻断启动，但必须告警而非静默
        print(f"[index] 外汇唯一索引创建失败（可能存在同客户同币种重复有效子户，需排查）：{e}")
    db.credit_card.create_index([("card_no", ASCENDING)], unique=True)
    db.credit_card.create_index([("customer_id", ASCENDING)])
    db.credit_card_bill.create_index([("credit_card_id", ASCENDING), ("bill_cycle", ASCENDING)], unique=True)
    db.audit_log.create_index([("created_at", ASCENDING)])
    db.audit_log.create_index([("user_id", ASCENDING)])
    db.system_param.create_index([("param_key", ASCENDING)], unique=True)


def ping():
    """健康检查用。"""
    try:
        get_client().admin.command("ping")
        return True
    except ConnectionFailure:
        return False
