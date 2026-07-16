"""贷款领域层纯逻辑自测（不依赖数据库）：利息策略计划表、还款瀑布分配、
提前结清减免、罚息按期计提与水位、状态派生、审贷分离。

运行： python test_loan_domain.py    全部通过则打印 OK。
"""
from datetime import timedelta
from decimal import Decimal

import constants as C
from common import D, dec, m, now
from loan_domain import (
    STRATEGIES, build_schedule, sched_to_db, sched_from_db,
    PenaltyCalculator, RepaymentAllocator, LoanBook,
    derive_status, first_overdue, remaining_amounts, settle_quote, add_months,
)


def _total_principal(sched):
    return sum((dec(i["principal_due"]) for i in sched), D(0))


def test_schedule_principal_sums_exact():
    # 三种策略 Σ本金 == 借款本金（精确，末期吸收尾差），任意"除不尽"参数都成立
    for method in STRATEGIES:
        for principal, n in ((D("100000"), 12), (D("77777.77"), 7), (D("50000"), 1), (D("333.33"), 36)):
            sched = build_schedule(method, principal, Decimal("0.0435"), n, now())
            assert _total_principal(sched) == principal, (method, principal, n)
            assert len(sched) == (1 if method == "一次性还本付息" else n)


def test_equal_installment_pmt_constant():
    # 等额本息：每期还款额（本+息）恒定，仅末期允许尾差
    sched = build_schedule("等额本息", D("120000"), Decimal("0.06"), 12, now())
    pmts = [dec(i["principal_due"]) + dec(i["interest_due"]) for i in sched]
    assert len(set(pmts[:-1])) == 1  # 前 n-1 期完全相等
    assert abs(pmts[-1] - pmts[0]) < D("1")  # 末期尾差在 1 元以内
    # 利息逐期递减（本金逐期递增）
    interests = [dec(i["interest_due"]) for i in sched]
    assert all(a >= b for a, b in zip(interests, interests[1:]))


def test_equal_principal_interest_decreasing():
    # 等额本金：每期本金相等（末期吸收尾差），首期利息 = P × 月利率
    P, rate, n = D("120000"), Decimal("0.0435"), 12
    sched = build_schedule("等额本金", P, rate, n, now())
    assert dec(sched[0]["principal_due"]) == D(P / n)
    assert dec(sched[0]["interest_due"]) == D(P * rate / 12)
    assert dec(sched[-1]["interest_due"]) == D(dec(sched[-1]["principal_due"]) * rate / 12)


def test_lump_sum_interest():
    # 一次性还本付息：利息 = P × 年利率 × 期限/12；到期日 = 起始 + n 月
    start = now()
    sched = build_schedule("一次性还本付息", D("50000"), Decimal("0.0435"), 12, start)
    assert len(sched) == 1
    assert dec(sched[0]["interest_due"]) == D("2175.00")
    assert sched[0]["due_date"] == add_months(start, 12)


def test_zero_rate_schedule():
    # 零利率：等额本息退化为等额本金，利息全 0
    sched = build_schedule("等额本息", D("1200"), Decimal("0"), 12, now())
    assert _total_principal(sched) == D("1200")
    assert all(dec(i["interest_due"]) == 0 for i in sched)


def test_allocator_penalty_first_then_waterfall():
    # 罚息优先，再按期序 利息→本金
    sched = [dict(period=1, due_date=now() - timedelta(days=30), principal_due=D("1000"),
                  interest_due=D("100"), principal_paid=D(0), interest_paid=D(0),
                  waived_interest=D(0), status="PENDING"),
             dict(period=2, due_date=now() + timedelta(days=30), principal_due=D("1000"),
                  interest_due=D("80"), principal_paid=D(0), interest_paid=D(0),
                  waived_interest=D(0), status="PENDING")]
    res, err = RepaymentAllocator.allocate(sched, D("50"), D("60"))
    assert err is None
    assert res["penalty_part"] == D("50") and res["interest_part"] == D("10")
    assert res["principal_part"] == 0 and res["penalty_remaining"] == 0
    # 跨期瀑布：1150 = 罚息50 + 期1利息100 + 期1本金1000 → 期1 结清、期2 未动
    res2, _ = RepaymentAllocator.allocate(sched, D("50"), D("1150"))
    assert res2["schedule"][0]["status"] == "PAID"
    assert res2["schedule"][1]["status"] == "PENDING"
    assert res2["principal_remaining"] == D("1000") and res2["interest_remaining"] == D("80")
    # 恰好全清 → settled
    res3, _ = RepaymentAllocator.allocate(sched, D("50"), D("2230"))
    assert res3["settled"] is True
    # 超额 → E-3
    res4, err4 = RepaymentAllocator.allocate(sched, D("50"), D("2230.01"))
    assert res4 is None and err4[0] == "E-3"


def test_allocator_settle_waives_future_interest():
    # 提前结清：已到期利息照收、未到期利息减免、本金全收
    sched = [dict(period=1, due_date=now() - timedelta(days=10), principal_due=D("1000"),
                  interest_due=D("100"), principal_paid=D(0), interest_paid=D(0),
                  waived_interest=D(0), status="PENDING"),
             dict(period=2, due_date=now() + timedelta(days=50), principal_due=D("1000"),
                  interest_due=D("80"), principal_paid=D(0), interest_paid=D(0),
                  waived_interest=D(0), status="PENDING")]
    amount, res = RepaymentAllocator.allocate_settle(sched, D("30"))
    assert amount == D("30") + D("100") + D("2000")  # 罚息 + 到期利息 + 全部本金
    assert res["waived_interest"] == D("80")
    assert res["settled"] is True
    assert all(i["status"] == "PAID" for i in res["schedule"])
    # settle_quote 与 allocate_settle 同口径
    assert settle_quote({"schedule": sched_to_db(sched)}, D("30")) == amount


def test_penalty_accrual_and_watermark():
    # 单期逾期 40 天：50000 × 0.0005 × 40 = 1000
    overdue_day = now() - timedelta(days=40)
    sched = sched_to_db(build_schedule("一次性还本付息", D("50000"), Decimal("0"), 1, now()))
    sched[0]["due_date"] = overdue_day
    loan = {"penalty_due": m(0), "penalty_asof": None, "schedule": sched}
    penalty, asof = PenaltyCalculator.accrue(loan, "0.0005")
    assert penalty == D("1000.00") and asof.date() == now().date()
    # 水位推进后同日重复计提不翻倍
    loan2 = {"penalty_due": m(penalty), "penalty_asof": asof, "schedule": sched}
    penalty2, _ = PenaltyCalculator.accrue(loan2, "0.0005")
    assert penalty2 == D("1000.00")
    # 部分还本后按剩余本金续提：再过 10 天 → 1000 + 25000×0.0005×10 = 1125
    sched_half = sched_from_db(sched)
    sched_half[0]["principal_paid"] = D("25000")
    loan3 = {"penalty_due": m(penalty), "penalty_asof": asof,
             "schedule": sched_to_db(sched_half)}
    penalty3, _ = PenaltyCalculator.accrue(loan3, "0.0005", today=now() + timedelta(days=10))
    assert penalty3 == D("1125.00")
    # 参数未维护（None）：不计提、不推进水位
    p4, a4 = PenaltyCalculator.accrue(loan, None)
    assert p4 == 0 and a4 is None


def test_derive_status():
    future, past = now() + timedelta(days=30), now() - timedelta(days=1)
    base = dict(principal_due=D("100"), interest_due=D("10"), principal_paid=D(0),
                interest_paid=D(0), waived_interest=D(0), status="PENDING")
    assert derive_status([{**base, "period": 1, "due_date": future}], D(0)) == C.LOAN_ACTIVE
    assert derive_status([{**base, "period": 1, "due_date": past}], D(0)) == C.LOAN_OVERDUE
    paid = {**base, "principal_paid": D("100"), "interest_paid": D("10"), "status": "PAID"}
    assert derive_status([{**paid, "period": 1, "due_date": past}], D(0)) == C.LOAN_PAID_OFF
    # 全部还清但罚息未缴 → 仍不能结清（逾期）
    assert derive_status([{**paid, "period": 1, "due_date": past}], D("1")) != C.LOAN_PAID_OFF
    assert first_overdue([{**base, "period": 1, "due_date": past}])["period"] == 1
    p, i = remaining_amounts([{**base, "period": 1, "due_date": future}])
    assert p == D("100") and i == D("10")


def test_sod_check():
    # 审贷分离：申请人不得自审、审批人不得自放；换人即放行
    loan = {"user_id": "u1", "approved_by": "u2"}
    assert LoanBook.sod_check(loan, "u1", "approve")[0] == "E-SOD"
    assert LoanBook.sod_check(loan, "u2", "approve") is None
    assert LoanBook.sod_check(loan, "u2", "disburse")[0] == "E-SOD"
    assert LoanBook.sod_check(loan, "u3", "disburse") is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print("OK - 贷款领域层自测全部通过")
