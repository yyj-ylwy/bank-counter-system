"""资金操作领域层（储蓄/贷款共用）：方向感知账户校验、原子记账、当日支出限额、
幂等防重放、当日冲正。savings.py / loan.py 作为路由层调用本模块，不直接操作余额。

设计约束：不修改 common.py / constants.py / db.py 中的共享实现与格式；
business_transaction 文档只增字段（request_id / reversed / reversed_by），不改既有语义。
"""
from decimal import Decimal

import constants as C
from common import D, dec, m, now, check_account, write_txn, get_param_dec, txn_view

# 冲正业务类型（本模块局部定义，txn_view 对未知类型回退显示原串，不依赖 constants.TXN_TYPE_LABEL）
TXN_REVERSAL = "REVERSAL"

# 可冲正的原交易类型（转入/手续费腿随转出腿一并冲正，不可单独冲）
_REVERSIBLE_TYPES = (C.TXN_DEPOSIT, C.TXN_WITHDRAW, C.TXN_TRANSFER_OUT)

CREDIT = "CREDIT"  # 入金方向（存款、转入、放款）
DEBIT = "DEBIT"    # 出金方向（取款、转出、还款扣款）


class AccountGuard:
    """UC-INC-2 的方向感知扩展：挂失 = 只进不出。
    DEBIT 与 common.check_account 完全同口径（冻结/销户/挂失/余额全拦）；
    CREDIT 放行挂失账户（工资照进、放款照入），仅拦冻结/销户。"""

    @staticmethod
    def check(db, account_no, *, direction, need_amount=None, session=None):
        """返回 (account, error)；error 为 (code, msg) 或 None。错误码与 check_account 一致。"""
        if direction == DEBIT:
            return check_account(db, account_no, need_amount=need_amount, session=session)
        if not account_no or not str(account_no).strip():
            return None, ("E-NOACC", "未找到账户")
        acc = db.account.find_one({"account_no": str(account_no).strip()}, session=session)
        if not acc:
            return None, ("E-NOACC", "未找到账户")
        if acc["status"] == C.ACCOUNT_CLOSED:
            return acc, ("E-CLOSED", "账户已销户")
        if acc["status"] == C.ACCOUNT_FROZEN:
            return acc, ("E-FROZEN", "账户已冻结")
        return acc, None  # 挂失（账户级或卡级）入金放行


class SavingsAccount:
    """储蓄账户资金聚合：余额变动一律条件原子更新（$inc + 前置条件过滤），
    替代读改写 $set——即使本地单机降级为无事务模式，单笔记账仍原子。"""

    def __init__(self, db, acc, session=None):
        self.db = db
        self.acc = acc
        self.session = session

    @property
    def balance(self):
        return dec(self.acc["balance"])

    def credit(self, amount):
        """入金：无前置条件（方向校验由 AccountGuard 前置完成）。"""
        amount = D(amount)
        self.db.account.update_one({"_id": self.acc["_id"]},
                                   {"$inc": {"balance": m(amount)}}, session=self.session)
        self.acc["balance"] = m(self.balance + amount)
        return None

    def debit(self, amount):
        """出金：状态正常 + 卡未挂失 + 余额充足 三条件命中才扣，未命中重读区分错误码。
        返回 (code, msg) 或 None。"""
        amount = D(amount)
        res = self.db.account.update_one(
            {"_id": self.acc["_id"], "status": C.ACCOUNT_NORMAL,
             "card_status": {"$ne": C.CARD_LOST}, "balance": {"$gte": m(amount)}},
            {"$inc": {"balance": m(-amount)}}, session=self.session)
        if res.matched_count == 0:
            return self._classify_debit_failure(amount)
        self.acc["balance"] = m(self.balance - amount)
        return None

    def _classify_debit_failure(self, amount):
        acc = self.db.account.find_one({"_id": self.acc["_id"]}, session=self.session)
        if not acc:
            return ("E-NOACC", "未找到账户")
        self.acc = acc
        if acc["status"] == C.ACCOUNT_CLOSED:
            return ("E-CLOSED", "账户已销户")
        if acc["status"] == C.ACCOUNT_FROZEN:
            return ("E-FROZEN", "账户已冻结")
        if acc["status"] == C.ACCOUNT_LOST or acc.get("card_status") == C.CARD_LOST:
            return ("E-LOST", "账户/卡片已挂失")
        if dec(acc["balance"]) < amount:
            return ("E-BAL", f"余额不足，当前余额 {dec(acc['balance'])}，缺口 {amount - dec(acc['balance'])}")
        return ("E-2", "账户状态已变化，请刷新后重试")


class DailyDebitPolicy:
    """当日支出限额：取款 + 转出 + 跨行手续费 合并占用同一额度（堵住"转账绕过取款限额"），
    复用参数 WITHDRAW_DAILY_LIMIT；已冲正的流水不占额度。"""

    DEBIT_TYPES = (C.TXN_WITHDRAW, C.TXN_TRANSFER_OUT, C.TXN_TRANSFER_FEE)

    @staticmethod
    def spent_today(db, account_id, session=None):
        start = now().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = list(db.business_transaction.aggregate([
            {"$match": {"account_id": account_id,
                        "business_type": {"$in": list(DailyDebitPolicy.DEBIT_TYPES)},
                        "status": C.TXN_STATUS_SUCCESS,
                        "reversed": {"$ne": True},
                        "txn_time": {"$gte": start}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ], session=session))
        return dec(rows[0]["total"]) if rows else D(0)

    @staticmethod
    def check(db, account_id, amount, session=None):
        """超限返回 ("E-2", msg)，否则 None。"""
        limit = get_param_dec(db, C.P_WITHDRAW_DAILY_LIMIT, "50000")
        if DailyDebitPolicy.spent_today(db, account_id, session) + D(amount) > limit:
            return ("E-2", f"超过当日支出限额 {limit}（取款与转账合并计算）")
        return None


class IdempotencyGuard:
    """幂等防重放：请求体可选 request_id；命中同 (request_id, business_type) 的成功流水
    即视为重复请求，直接返回原流水。查找必须发生在事务回调内——with_transaction
    冲突重试会重跑整个回调，只有回调内的查找能保证"重试也只入账一次"。"""

    @staticmethod
    def find_existing(db, request_id, business_type, session=None):
        if not request_id or not str(request_id).strip():
            return None
        return db.business_transaction.find_one(
            {"request_id": str(request_id).strip(), "business_type": business_type,
             "status": C.TXN_STATUS_SUCCESS}, session=session)


class TxnRecorder:
    """UC-INC-3 包装：经 common.write_txn 落流水（保持共享文档构造单一来源），
    再同 session 补写幂等键。"""

    @staticmethod
    def record(db, *, request_id=None, session=None, **kw):
        t = write_txn(db, session=session, **kw)
        if request_id and str(request_id).strip():
            rid = str(request_id).strip()
            db.business_transaction.update_one({"_id": t["_id"]},
                                               {"$set": {"request_id": rid}}, session=session)
            t["request_id"] = rid
        return t


class ReconciliationService:
    """账实对账：每个储蓄账户 余额 ?= Σ入账流水 − Σ出账流水（README 的"可对账"承诺落地）。

    口径：
    - 已冲正的原腿（reversed=True）与红字腿（REVERSAL）成对排除——冲正语义即"当无事发生"；
    - 外汇买卖流水 amount 为外币金额，人民币腿取补充字段 cny_amount；
    - CC_REPAY 的资金来源可能是储蓄或美元子户，两种分支流水同型同挂储蓄账户，
      类型级无法判定方向 → 含该类型的账户列入 skipped（不误报差异）；
    - 方向表未登记且带 account_id 的新类型 → unknown_types 提示登记（不算差异），
      队友新增业务类型时对账不误报、不漏提示。"""

    CREDIT_TYPES = {C.TXN_OPEN, C.TXN_DEPOSIT, C.TXN_TRANSFER_IN, C.TXN_LOAN_DISBURSE,
                    C.TXN_CC_CASHBACK, C.TXN_INVEST_SELL, C.TXN_CC_CASH_PAYOUT}
    DEBIT_TYPES = {C.TXN_WITHDRAW, C.TXN_TRANSFER_OUT, C.TXN_TRANSFER_FEE,
                   C.TXN_LOAN_REPAY, C.TXN_INVEST_BUY}
    CNY_FIELD_TYPES = {C.TXN_FX_BUY: -1, C.TXN_FX_SELL: 1}   # 金额取 cny_amount 的类型及方向
    NEUTRAL_TYPES = {C.TXN_CLOSE_ACCOUNT, TXN_REVERSAL}       # 不影响余额
    UNVERIFIED_TYPES = {C.TXN_CC_REPAY}                       # 无法按类型判定方向

    @classmethod
    def run(cls, db):
        """全量核对，返回报告 dict（不写库；审计由路由层负责）。"""
        # 一次聚合出各账户各类型的金额合计（排除失败/已冲正/红字流水）
        rows = db.business_transaction.aggregate([
            {"$match": {"account_id": {"$ne": None}, "status": C.TXN_STATUS_SUCCESS,
                        "reversed": {"$ne": True}, "business_type": {"$ne": TXN_REVERSAL}}},
            {"$group": {"_id": {"acc": "$account_id", "bt": "$business_type"},
                        "total": {"$sum": "$amount"}, "cny": {"$sum": "$cny_amount"}}},
        ])
        expected, skipped_accs, unknown = {}, set(), set()
        for r in rows:
            acc_id, bt = r["_id"]["acc"], r["_id"]["bt"]
            if bt in cls.NEUTRAL_TYPES:
                continue
            if bt in cls.UNVERIFIED_TYPES:
                skipped_accs.add(acc_id)
                continue
            if bt in cls.CNY_FIELD_TYPES:
                delta = dec(r.get("cny") or 0) * cls.CNY_FIELD_TYPES[bt]
            elif bt in cls.CREDIT_TYPES:
                delta = dec(r["total"])
            elif bt in cls.DEBIT_TYPES:
                delta = -dec(r["total"])
            else:  # 方向表未登记的新类型：不猜方向，提示登记
                unknown.add(bt)
                skipped_accs.add(acc_id)
                continue
            expected[acc_id] = expected.get(acc_id, D(0)) + delta

        matched, mismatched, skipped = 0, [], []
        for acc in db.account.find({}):
            if acc["_id"] in skipped_accs:
                skipped.append({"account_no": acc["account_no"],
                                "reason": "含无法按类型核对的流水（如信用卡多渠道还款）"})
                continue
            exp = D(expected.get(acc["_id"], 0))
            bal = dec(acc["balance"])
            if abs(bal - exp) < D("0.01"):
                matched += 1
            else:
                mismatched.append({"account_no": acc["account_no"], "balance": float(bal),
                                   "expected": float(exp), "diff": float(D(bal - exp))})
        return {"checked": matched + len(mismatched) + len(skipped),
                "matched": matched, "mismatched": mismatched, "skipped": skipped,
                "unknown_types": sorted(unknown),
                "balanced": not mismatched}


class ReversalService:
    """UC-109 当日冲正：柜员错账纠正通道。
    规则：仅当日、仅成功、未被冲正过；转账以转出腿为入口，转入/手续费腿一并反向。
    做法：资金反向 + 每条被冲腿写一笔 REVERSAL 红字流水（related_id 指原腿）+
    原腿打 reversed/reversed_by 标记——不改共享的 status 枚举，其他子系统读流水不受影响。"""

    @staticmethod
    def reverse(db, txn_no, reason, operator_id, session=None):
        """返回 (result_dict, error)；error 为 (code, msg) 或 None。"""
        t = db.business_transaction.find_one({"txn_no": (txn_no or "").strip()}, session=session)
        if not t:
            return None, ("E-REV", "未找到该流水号")
        if t["business_type"] in (C.TXN_TRANSFER_IN, C.TXN_TRANSFER_FEE):
            return None, ("E-REV", "转入/手续费流水请通过对应的转出流水整体冲正")
        if t["business_type"] not in _REVERSIBLE_TYPES:
            return None, ("E-REV", f"该类型流水不支持冲正（{t['business_type']}）")
        if t["status"] != C.TXN_STATUS_SUCCESS:
            return None, ("E-REV", "仅成功流水可冲正")
        if t.get("reversed"):
            return None, ("E-REV", "该流水已被冲正，不可重复冲正")
        if not t.get("txn_time") or t["txn_time"].date() != now().date():
            return None, ("E-REV", "仅支持当日流水冲正，隔日错账请走人工调账流程")

        src = db.account.find_one({"_id": t["account_id"]}, session=session)
        if not src:
            return None, ("E-REV", "原交易账户不存在")
        if src["status"] == C.ACCOUNT_CLOSED:  # 冲正涉及向原账户退回资金，已销户不可再入账
            return None, ("E-REV", "原交易账户已销户，请走人工调账流程")
        src_book = SavingsAccount(db, src, session)
        amount = dec(t["amount"])
        legs = [t]  # 待冲腿：主腿 + 转账的关联腿

        if t["business_type"] == C.TXN_DEPOSIT:
            err = src_book.debit(amount)  # 存款冲正 = 扣回，客户已取走则余额不足
            if err:
                return None, ("E-REV", f"扣回失败：{err[1]}")
        elif t["business_type"] == C.TXN_WITHDRAW:
            src_book.credit(amount)  # 取款冲正 = 退回
        else:  # TRANSFER_OUT：区分行内（有转入腿）与跨行（只有手续费腿）
            # 转入/手续费腿在写入时即带 related_id=转出腿 _id，按此发现比依赖回填的 out.related_id 更稳
            # （单机降级模式下回填与插入非原子，崩溃可能留下未回填的转出腿）
            t_in = db.business_transaction.find_one(
                {"related_id": t["_id"], "business_type": C.TXN_TRANSFER_IN}, session=session)
            t_fee = db.business_transaction.find_one(
                {"related_id": t["_id"], "business_type": C.TXN_TRANSFER_FEE}, session=session)
            if t_in:  # 行内：先从收款方扣回（对方已花掉则冲正失败），再退回转出方
                dst = db.account.find_one({"_id": t_in["account_id"]}, session=session)
                if not dst:
                    return None, ("E-REV", "收款账户不存在")
                err = SavingsAccount(db, dst, session).debit(amount)
                if err:
                    return None, ("E-REV", f"收款方扣回失败：{err[1]}")
                legs.append(t_in)
            src_book.credit(amount)
            if t_fee:  # 手续费退回转出方
                src_book.credit(dec(t_fee["amount"]))
                legs.append(t_fee)

        reversal_views = []
        for leg in legs:  # 每条被冲腿写一笔红字流水并回标原腿
            rv = write_txn(db, business_type=TXN_REVERSAL, amount=dec(leg["amount"]),
                           user_id=operator_id, customer_id=leg.get("customer_id"),
                           account_id=leg.get("account_id"), related_id=leg["_id"], session=session)
            db.business_transaction.update_one(
                {"_id": leg["_id"]}, {"$set": {"reversed": True, "reversed_by": rv["_id"],
                                               "reverse_reason": (reason or "").strip()}},
                session=session)
            reversal_views.append(txn_view(rv))
        return {"reversed_txn_no": t["txn_no"],
                "reversed_type": t["business_type"],
                "legs_reversed": len(legs),
                "reversals": reversal_views,
                "balance": float(src_book.balance)}, None
