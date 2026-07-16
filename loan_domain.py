"""贷款领域层：还款计划（策略模式）、逾期罚息计提、还款瀑布分配、状态派生。

核心模型：放款时按还款方式生成逐期计划表（schedule）存入贷款文档；
还款按 罚息 → 期序（利息 → 本金）瀑布冲抵；全部期结清且罚息为零才结清（PAID_OFF）；
存在过期未清的期即逾期（OVERDUE），状态在触碰时自动派生，不再依赖手工置位。

本模块除 LoanBook.ensure_schedule 外均为纯计算（不读写库），可单元测试。
金额一律 Decimal 运算；落库/读库转换由 sched_to_db / sched_from_db 承担。
"""
from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP

from bson.decimal128 import Decimal128

import constants as C
from common import D, dec, m, now

INST_PENDING = "PENDING"
INST_PAID = "PAID"


def add_months(dt, months):
    """给日期加 months 个月（日取 min(day,28)，避免月末溢出；沿用原 loan.py 口径）。"""
    y, mth = dt.year, dt.month - 1 + int(months)
    y += mth // 12
    mth = mth % 12 + 1
    day = min(dt.day, 28)
    return dt.replace(year=y, month=mth, day=day)


def rate4(rate):
    """利率存 4 位小数（DECIMAL(6,4) 语义）。"""
    return Decimal128(Decimal(str(rate)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _new_inst(period, due_date, principal_due, interest_due):
    return {"period": period, "due_date": due_date,
            "principal_due": D(principal_due), "interest_due": D(interest_due),
            "principal_paid": D(0), "interest_paid": D(0),
            "waived_interest": D(0),  # 提前结清减免的利息（留痕，保证计划表可对账）
            "status": INST_PENDING}


# ============ 利息策略（Strategy 模式）============
class InterestStrategy(ABC):
    """还款方式策略基类：给定 本金/年利率/期限/起始日，生成逐期还款计划。
    不变式：Σ principal_due == 本金（精确成立，末期吸收舍入尾差）。"""

    key = None  # 前端/接口使用的中文方式名

    @abstractmethod
    def build_schedule(self, principal, annual_rate, term_months, start):
        """返回 list[installment dict]，金额为 2 位 Decimal。"""


class EqualInstallment(InterestStrategy):
    """等额本息：每期还款额恒定 pmt = P·i·(1+i)^n / ((1+i)^n − 1)。
    每期 利息 = 剩余本金×月利率（2 位），本金 = pmt − 利息；末期本金 = 剩余全部。"""

    key = "等额本息"

    def build_schedule(self, principal, annual_rate, term_months, start):
        P, n = D(principal), int(term_months)
        i = dec(annual_rate) / 12  # 月利率保留高精度，逐期金额才舍入
        pmt = None
        if i > 0:
            f = (1 + i) ** n
            pmt = D(P * i * f / (f - 1))
        insts, remaining = [], P
        for k in range(1, n + 1):
            interest = D(remaining * i)
            if k == n:
                pk = remaining  # 末期吸收尾差，Σ本金 == P 精确成立
            elif i > 0:
                pk = min(pmt - interest, remaining)
            else:
                pk = min(D(P / n), remaining)  # 零利率退化为等额本金
            insts.append(_new_inst(k, add_months(start, k), pk, interest))
            remaining -= pk
        return insts


class EqualPrincipal(InterestStrategy):
    """等额本金：每期本金 = P/n（末期吸收尾差），利息 = 剩余本金×月利率，逐期递减。"""

    key = "等额本金"

    def build_schedule(self, principal, annual_rate, term_months, start):
        P, n = D(principal), int(term_months)
        i = dec(annual_rate) / 12
        base = D(P / n)
        insts, remaining = [], P
        for k in range(1, n + 1):
            interest = D(remaining * i)
            pk = remaining if k == n else min(base, remaining)
            insts.append(_new_inst(k, add_months(start, k), pk, interest))
            remaining -= pk
        return insts


class LumpSum(InterestStrategy):
    """一次性还本付息：单期，到期还 本金 + P×年利率×期限/12。
    也是历史无计划表贷款懒升级的兜底形态。"""

    key = "一次性还本付息"

    def build_schedule(self, principal, annual_rate, term_months, start):
        P, n = D(principal), int(term_months)
        interest = D(P * dec(annual_rate) * n / 12)
        return [_new_inst(1, add_months(start, n), P, interest)]


STRATEGIES = {s.key: s for s in (EqualInstallment(), EqualPrincipal(), LumpSum())}


def build_schedule(repay_method, principal, annual_rate, term_months, start):
    """按还款方式派发策略。方式非法由审批环节拦截，这里兜底 KeyError 即编程错误。"""
    return STRATEGIES[repay_method].build_schedule(principal, annual_rate, term_months, start)


# ============ 计划表 存库/读库 转换 ============
_MONEY_FIELDS = ("principal_due", "interest_due", "principal_paid", "interest_paid", "waived_interest")


def sched_to_db(schedule):
    """金额 Decimal → Decimal128（全系统金额落库不变式）。"""
    return [{**inst, **{f: m(inst.get(f, 0)) for f in _MONEY_FIELDS}} for inst in schedule]


def sched_from_db(schedule):
    """金额 Decimal128 → Decimal，供纯计算使用。"""
    return [{**inst, **{f: dec(inst.get(f, 0)) for f in _MONEY_FIELDS}} for inst in (schedule or [])]


def sched_view(schedule):
    """计划表序列化（float 仅在视图层出现）。"""
    return [{"period": i["period"],
             "due_date": i["due_date"].strftime("%Y-%m-%d") if i.get("due_date") else None,
             "principal_due": float(dec(i["principal_due"])),
             "interest_due": float(dec(i["interest_due"])),
             "principal_paid": float(dec(i.get("principal_paid", 0))),
             "interest_paid": float(dec(i.get("interest_paid", 0))),
             "waived_interest": float(dec(i.get("waived_interest", 0))),
             "status": i["status"]}
            for i in (schedule or [])]


# ============ 剩余额与状态派生 ============
def _inst_unpaid_principal(inst):
    return dec(inst["principal_due"]) - dec(inst.get("principal_paid", 0))


def _inst_unpaid_interest(inst):
    return dec(inst["interest_due"]) - dec(inst.get("interest_paid", 0)) - dec(inst.get("waived_interest", 0))


def remaining_amounts(schedule):
    """返回 (未还本金, 未还利息)。"""
    p = sum((_inst_unpaid_principal(i) for i in schedule), D(0))
    it = sum((_inst_unpaid_interest(i) for i in schedule), D(0))
    return D(p), D(it)


def derive_status(schedule, penalty_due, today=None):
    """全部期结清且罚息为零 → PAID_OFF；存在过期未清期 → OVERDUE；否则 ACTIVE。"""
    today = (today or now()).date()
    principal, interest = remaining_amounts(schedule)
    if principal <= 0 and interest <= 0 and D(penalty_due) <= 0:
        return C.LOAN_PAID_OFF
    for inst in schedule:
        if inst["status"] != INST_PAID and inst["due_date"].date() < today:
            return C.LOAN_OVERDUE
    return C.LOAN_ACTIVE


def first_overdue(schedule, today=None):
    """最早的逾期未清期（无则 None），供 overdue_days 计算。"""
    today = (today or now()).date()
    for inst in schedule:  # schedule 按期序存储
        if inst["status"] != INST_PAID and inst["due_date"].date() < today:
            return inst
    return None


# ============ 罚息计提 ============
class PenaltyCalculator:
    """逾期罚息按期计提：对每个过期未清的期，按『该期未还本金 × 日罚息率 ×
    自 max(该期到期日, 上次计提水位) 以来的日历天数』增量累加。
    水位（penalty_asof）整笔一个：只要计提过，各期的起算点取 max(due, asof) 即不重不漏。
    纯计算不写库；还款与逾期列表共用同一口径。"""

    @staticmethod
    def accrue(loan, daily_rate, today=None):
        """入参 loan 为贷款文档（schedule 可为库格式）。返回 (应收未缴罚息, 新水位)。
        daily_rate 为 None（参数未维护）时不计提、不推进水位。"""
        penalty_due = dec(loan.get("penalty_due", 0))
        asof = loan.get("penalty_asof")
        today = today or now()
        if daily_rate is None:
            return D(penalty_due), asof
        rate = dec(daily_rate)
        schedule = sched_from_db(loan.get("schedule"))
        for inst in schedule:
            unpaid = _inst_unpaid_principal(inst)
            if inst["status"] == INST_PAID or unpaid <= 0:
                continue
            start = inst["due_date"]
            if asof and asof.date() > start.date():
                start = asof
            days = (today.date() - start.date()).days  # 日历日粒度，避免时分秒 off-by-one
            if days > 0:
                penalty_due += D(unpaid * rate * days)
        return D(penalty_due), today


# ============ 还款瀑布分配 ============
class RepaymentAllocator:
    """还款分配（纯函数）：罚息 → 按期序（利息 → 本金）逐期冲抵，允许冲入未到期期次
    （提前还款按计划表利息、不打折）。返回分配结果或错误。"""

    @staticmethod
    def allocate(schedule, penalty_due, amount):
        """返回 (result, error)。schedule 接受库格式或 Decimal 格式（sched_from_db 统一归一并复制，
        不改动入参），输出为 Decimal 格式副本。"""
        sched = sched_from_db(schedule)
        amount, penalty = D(amount), D(penalty_due)
        principal_rem, interest_rem = remaining_amounts(sched)
        total_due = principal_rem + interest_rem + penalty
        if amount > total_due:
            return None, ("E-3", f"还款金额超过应还合计 {total_due}"
                                 f"（本金 {principal_rem} + 利息 {interest_rem} + 罚息 {penalty}）")

        left = amount
        pay_penalty = min(left, penalty)  # 罚息优先
        left -= pay_penalty
        details = []
        for inst in sched:
            if left <= 0:
                break
            if inst["status"] == INST_PAID:
                continue
            pay_i = min(left, _inst_unpaid_interest(inst))
            inst["interest_paid"] = D(inst["interest_paid"] + pay_i)
            left -= pay_i
            pay_p = min(left, _inst_unpaid_principal(inst))
            inst["principal_paid"] = D(inst["principal_paid"] + pay_p)
            left -= pay_p
            if _inst_unpaid_principal(inst) <= 0 and _inst_unpaid_interest(inst) <= 0:
                inst["status"] = INST_PAID
            if pay_i > 0 or pay_p > 0:
                details.append({"period": inst["period"], "interest": pay_i, "principal": pay_p})

        return RepaymentAllocator._result(sched, amount, pay_penalty, penalty - pay_penalty,
                                          D(0), details), None

    @staticmethod
    def allocate_settle(schedule, penalty_due):
        """提前结清：应付 = 罚息 + 已到期未还利息 + 全部剩余本金；未到期利息减免（留痕）。
        返回 (应付金额, result)。"""
        sched = sched_from_db(schedule)
        penalty, today = D(penalty_due), now().date()
        details, waived_total, amount = [], D(0), penalty
        for inst in sched:
            if inst["status"] == INST_PAID:
                continue
            unpaid_i, unpaid_p = _inst_unpaid_interest(inst), _inst_unpaid_principal(inst)
            if inst["due_date"].date() <= today:  # 已到期利息照收
                pay_i = unpaid_i
            else:  # 未到期利息减免
                pay_i = D(0)
                inst["waived_interest"] = D(inst["waived_interest"] + unpaid_i)
                waived_total += unpaid_i
            inst["interest_paid"] = D(inst["interest_paid"] + pay_i)
            inst["principal_paid"] = D(inst["principal_paid"] + unpaid_p)
            inst["status"] = INST_PAID
            amount += pay_i + unpaid_p
            details.append({"period": inst["period"], "interest": pay_i, "principal": unpaid_p})
        result = RepaymentAllocator._result(sched, D(amount), penalty, D(0), waived_total, details)
        return D(amount), result

    @staticmethod
    def _result(sched, amount, pay_penalty, penalty_remaining, waived, details):
        principal_rem, interest_rem = remaining_amounts(sched)
        return {"amount": amount,
                "penalty_part": pay_penalty,
                "interest_part": D(sum((d["interest"] for d in details), D(0))),
                "principal_part": D(sum((d["principal"] for d in details), D(0))),
                "waived_interest": waived,
                "details": details,
                "schedule": sched,
                "penalty_remaining": penalty_remaining,
                "principal_remaining": principal_rem,
                "interest_remaining": interest_rem,
                "settled": principal_rem <= 0 and interest_rem <= 0 and penalty_remaining <= 0}


def settle_quote(loan, penalty_due):
    """结清试算（不改状态）：罚息 + 已到期未还利息 + 剩余本金。供还款 SETTLE 与视图展示。"""
    amount, _ = RepaymentAllocator.allocate_settle(loan.get("schedule") or [], penalty_due)
    return amount


# ============ 贷款簿记（唯一带持久化的入口）============
class LoanBook:
    """贷款聚合的持久化操作。计算全部委托上面的纯函数，便于单测。"""

    def __init__(self, db, session=None):
        self.db = db
        self.session = session

    def ensure_schedule(self, loan):
        """历史 ACTIVE/OVERDUE 贷款（无计划表）懒升级：按单期一次性还本生成，
        本金 = 当前余额、利息 0（放款时未约定计息，不追溯收息）、到期日沿用原 due_date。"""
        if loan.get("schedule") or loan["status"] not in (C.LOAN_ACTIVE, C.LOAN_OVERDUE):
            return loan
        inst = _new_inst(1, loan["due_date"], dec(loan["balance"]), 0)
        loan["schedule"] = sched_to_db([inst])
        self.db.loan.update_one({"_id": loan["_id"]},
                                {"$set": {"schedule": loan["schedule"]}}, session=self.session)
        return loan

    @staticmethod
    def sod_check(loan, operator_id, stage):
        """审贷分离（同角色不同工号的 maker-checker）：
        approve 阶段拒绝申请经办人自审；disburse 阶段拒绝审批人自放。"""
        if stage == "approve" and operator_id == loan.get("user_id"):
            return ("E-SOD", "审贷分离：贷款申请经办人不得审批本笔申请，请由其他贷款业务员办理")
        if stage == "disburse" and operator_id == loan.get("approved_by"):
            return ("E-SOD", "审贷分离：本笔贷款的审批人不得执行放款，请由其他贷款业务员办理")
        return None
