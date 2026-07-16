"""储蓄业务子系统 UC-101 ~ UC-109（参与者：储蓄业务员）。

路由层：解析/校验请求 → 调 bankcore 领域层（方向感知校验、原子记账、
当日支出限额、幂等、冲正）→ 事务内持久化 → 序列化响应。
"""
from flask import Blueprint, request, g
from pymongo.errors import DuplicateKeyError

import constants as C
from db import get_db, run_in_transaction
from auth import require_role
from common import (
    ok, fail, D, dec, m, oid, now,
    validate_id_no, validate_phone, validate_email, norm_id, check_identity, match_identity,
    find_customer, resolve_account_no, write_txn, write_audit,
    get_param_dec, customer_view, account_view, txn_view, parse_date_range,
    new_customer_no, new_account_no, new_debit_card_no,
)
from bankcore import (
    AccountGuard, SavingsAccount, DailyDebitPolicy, IdempotencyGuard,
    TxnRecorder, ReversalService, CREDIT, DEBIT,
)

bp = Blueprint("savings", __name__, url_prefix="/api/savings")
clerk = require_role(C.ROLE_SAVINGS)


def _body():
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}  # 非对象体当空，避免 .get 崩 500


def _dup_response(db, txn_doc):
    """幂等命中：返回原流水与当前余额，不再次记账。"""
    acc = db.account.find_one({"_id": txn_doc["account_id"]})
    return ok({"balance": float(dec(acc["balance"])) if acc else None,
               "txn": txn_view(txn_doc), "duplicate": True}, "重复请求，返回原交易结果")


# ---------- UC-101 客户信息登记与开户注册 ----------
@bp.post("/open-account")
@clerk
def open_account():
    d = _body()
    name = (d.get("name") or "").strip()
    id_type = (d.get("id_type") or "身份证").strip()
    id_no = norm_id(d.get("id_no"))
    email = (d.get("email") or "").strip()
    phone = (d.get("phone") or "").strip()
    init = D(d.get("initial_balance") or 0)

    if not name or not id_no:
        return fail("E-REQ", "姓名和证件号为必填项", 400)
    valid, reason, id_no = validate_id_no(id_type, id_no)  # id_no 归一化（尾号 X 大写、去空格）
    if not valid:  # E-2 证件号格式非法/过期
        return fail("E-2", reason)
    eok, ereason, email = validate_email(email)  # 邮箱必填：作为交易时可替代证件号核验身份的标识
    if not eok:
        return fail("E-EMAIL", ereason, 400)
    pok, preason, phone = validate_phone(phone)  # 手机号校验（选填，非空须合法）
    if not pok:
        return fail("E-PHONE", preason, 400)
    if init < 0:
        return fail("E-AMT", "初始存款金额不能为负", 400)

    db = get_db()
    existing = db.customer.find_one({"id_no": id_no})
    # 已有在用账户才算重复；若客户曾销户(无正常账户)，允许复用客户重新开户，不永久锁死
    if existing and db.account.find_one({"customer_id": existing["_id"], "status": C.ACCOUNT_NORMAL}):
        return fail("E-1", "该证件号已关联在用账户，可转为信息更新")
    email_owner = db.customer.find_one({"email": email})  # 邮箱全局唯一（排除同一客户）
    if email_owner and email_owner.get("id_no") != id_no:
        return fail("E-EMAIL", "该邮箱已被其他客户使用", 400)

    def txn(s):
        if existing:  # 复用既有客户，仅新开账户
            cust = existing
            cid = existing["_id"]
            if not existing.get("email"):  # 老客户补登邮箱
                db.customer.update_one({"_id": cid}, {"$set": {"email": email}}, session=s)
                cust["email"] = email
        else:
            cust = {
                "customer_no": new_customer_no(s), "name": name, "id_type": id_type,
                "id_no": id_no, "email": email, "phone": phone,
                "status": C.CUSTOMER_NORMAL, "points": 0, "created_at": now(),
            }
            cid = db.customer.insert_one(cust, session=s).inserted_id
            cust["_id"] = cid
        acc = {
            "account_no": new_account_no(s), "customer_id": cid, "card_no": new_debit_card_no(s),
            "card_status": C.CARD_NORMAL, "currency": "CNY", "balance": m(init),
            "status": C.ACCOUNT_NORMAL, "created_at": now(),
        }
        aid = db.account.insert_one(acc, session=s).inserted_id
        acc["_id"] = aid
        write_txn(db, business_type=C.TXN_OPEN, amount=init, user_id=g.user["_id"],
                  customer_id=cid, account_id=aid, session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_OPEN, object_type="account",
                    object_id=acc["account_no"], result=C.RESULT_SUCCESS,
                    detail={"customer_no": cust["customer_no"], "name": name}, session=s)
        return cust, acc

    try:
        cust, acc = run_in_transaction(txn)
    except DuplicateKeyError:
        return fail("E-1", "该证件号已关联客户，可转为信息更新")
    return ok({"customer": customer_view(cust), "account": account_view(acc, cust)}, "开户成功")


# ---------- UC-102 柜台存款 ----------
@bp.post("/deposit")
@clerk
def deposit():
    d = _body()
    ident = (d.get("ident") or "").strip()
    amount = D(d.get("amount") or 0)
    request_id = (d.get("request_id") or "").strip()  # 幂等键（选填）
    if amount <= 0 or amount > C.TXN_AMOUNT_MAX:  # E-2
        return fail("E-2", f"存款金额须大于 0 且不超过 {C.TXN_AMOUNT_MAX:,}")

    db = get_db()
    account_no, _cust, rerr = resolve_account_no(db, ident)  # 凭任意身份标识定位账户
    if rerr:
        return fail(rerr[0], rerr[1])

    def txn(s):
        dup = IdempotencyGuard.find_existing(db, request_id, C.TXN_DEPOSIT, s)  # 幂等：重放不重复入账
        if dup:
            return ("DUP", dup), None
        # 存款为入金：挂失账户放行（只进不出），冻结/销户拦截
        acc, err = AccountGuard.check(db, account_no, direction=CREDIT, session=s)
        if err:
            return None, err
        book = SavingsAccount(db, acc, s)
        book.credit(amount)  # 原子 $inc
        t = TxnRecorder.record(db, business_type=C.TXN_DEPOSIT, amount=amount,
                               user_id=g.user["_id"], customer_id=acc["customer_id"],
                               account_id=acc["_id"], request_id=request_id, session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_DEPOSIT, object_type="account",
                    object_id=account_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount)}, session=s)
        return (book, t), None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    tag, payload = res
    if tag == "DUP":
        return _dup_response(db, payload)
    book, t = res
    return ok({"balance": float(book.balance), "txn": txn_view(t)}, "存款成功")


# ---------- UC-103 柜台取款 ----------
@bp.post("/withdraw")
@clerk
def withdraw():
    d = _body()
    ident = (d.get("ident") or "").strip()
    amount = D(d.get("amount") or 0)
    request_id = (d.get("request_id") or "").strip()
    if amount <= 0 or amount > C.TXN_AMOUNT_MAX:
        return fail("E-AMT", f"取款金额须大于 0 且不超过 {C.TXN_AMOUNT_MAX:,}", 400)

    db = get_db()
    account_no, _cust, rerr = resolve_account_no(db, ident)  # 凭任意身份标识定位账户
    if rerr:
        return fail(rerr[0], rerr[1])

    def txn(s):
        dup = IdempotencyGuard.find_existing(db, request_id, C.TXN_WITHDRAW, s)  # 幂等：重放不重复扣款
        if dup:
            return ("DUP", dup), None
        acc, err = AccountGuard.check(db, account_no, direction=DEBIT, need_amount=amount, session=s)
        if err:  # E-1余额/E-3状态（错误码沿用 check_account 口径）
            return None, err
        lerr = DailyDebitPolicy.check(db, acc["_id"], amount, s)  # E-2 当日支出限额（取款+转账合并）
        if lerr:
            return None, lerr
        book = SavingsAccount(db, acc, s)
        derr = book.debit(amount)  # 条件原子扣款：状态+卡态+余额三条件命中才扣
        if derr:
            return None, derr
        t = TxnRecorder.record(db, business_type=C.TXN_WITHDRAW, amount=amount,
                               user_id=g.user["_id"], customer_id=acc["customer_id"],
                               account_id=acc["_id"], request_id=request_id, session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_WITHDRAW, object_type="account",
                    object_id=account_no, result=C.RESULT_SUCCESS,
                    detail={"amount": str(amount)}, session=s)
        return (book, t), None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    tag, payload = res
    if tag == "DUP":
        return _dup_response(db, payload)
    book, t = res
    return ok({"balance": float(book.balance), "txn": txn_view(t)}, "取款成功")


# ---------- UC-104 转账汇款（含 UC-10401 行内 / 10402 跨行 / 10403 同户）----------
@bp.post("/transfer")
@clerk
def transfer():
    d = _body()
    ttype = (d.get("transfer_type") or "INTRA").strip().upper()  # INTRA=行内, INTER=跨行
    to_ident = (d.get("to_ident") or "").strip()  # 收款方身份标识（本行）
    to_no = (d.get("to_account_no") or "").strip()  # 跨行收款账号（行外系统，非本行身份标识）
    to_bank = (d.get("to_bank") or "").strip()
    ident = (d.get("ident") or "").strip()  # 转出方身份标识
    amount = D(d.get("amount") or 0)
    request_id = (d.get("request_id") or "").strip()

    if ttype not in ("INTRA", "INTER"):  # 转账类型必须合法，避免非法值被静默当跨行
        return fail("E-OP", "转账类型非法（本行/跨行）", 400)
    if amount <= 0 or amount > C.TXN_AMOUNT_MAX:
        return fail("E-AMT", f"转账金额须大于 0 且不超过 {C.TXN_AMOUNT_MAX:,}", 400)

    db = get_db()
    from_no, _c, rerr = resolve_account_no(db, ident)  # 凭任意身份标识定位转出账户
    if rerr:
        return fail(rerr[0], rerr[1])
    if ttype == "INTRA":  # 本行转账：收款方也凭身份标识定位账户
        to_no = None
        if to_ident:
            to_no, _tc, terr = resolve_account_no(db, to_ident)
            if terr:
                return fail("E-1", f"收款方定位失败：{terr[1]}")
        if not to_no:
            return fail("E-1", "本行转账请填写收款方身份标识")
        if from_no == to_no:  # E-3 同一账户
            return fail("E-3", "转出与转入不能为同一账户")
    fee_rate = get_param_dec(db, C.P_TRANSFER_FEE_RATE, "0.001")

    def txn(s):
        dup = IdempotencyGuard.find_existing(db, request_id, C.TXN_TRANSFER_OUT, s)  # 幂等
        if dup:
            return ("DUP", dup), None
        fee = D(amount * fee_rate) if ttype == "INTER" else D(0)
        src, err = AccountGuard.check(db, from_no, direction=DEBIT,
                                      need_amount=amount + fee, session=s)  # E-2 余额
        if err:
            return None, err
        lerr = DailyDebitPolicy.check(db, src["_id"], amount + fee, s)  # 转账同样占用当日支出限额
        if lerr:
            return None, lerr
        src_book = SavingsAccount(db, src, s)

        if ttype == "INTRA":
            # 收款为入金方向：挂失账户可入（只进不出），冻结/销户拦截
            dst, derr = AccountGuard.check(db, to_no, direction=CREDIT, session=s)
            if derr:
                return None, ("E-1", f"转入账户不可用：{derr[1]}")
            werr = src_book.debit(amount)  # 条件原子：扣转出
            if werr:
                return None, werr
            SavingsAccount(db, dst, s).credit(amount)  # 加转入
            t_out = TxnRecorder.record(db, business_type=C.TXN_TRANSFER_OUT, amount=amount,
                                       user_id=g.user["_id"], customer_id=src["customer_id"],
                                       account_id=src["_id"], request_id=request_id, session=s)
            t_in = write_txn(db, business_type=C.TXN_TRANSFER_IN, amount=amount, user_id=g.user["_id"],
                             customer_id=dst["customer_id"], account_id=dst["_id"],
                             related_id=t_out["_id"], session=s)
            db.business_transaction.update_one({"_id": t_out["_id"]},
                                              {"$set": {"related_id": t_in["_id"]}}, session=s)
            same_owner = src["customer_id"] == dst["customer_id"]
            write_audit(db, user_id=g.user["_id"], action="TRANSFER", object_type="account",
                        object_id=from_no, result=C.RESULT_SUCCESS,
                        detail={"type": "同户" if same_owner else "行内", "to": to_no,
                                "amount": str(amount)}, session=s)
            return {"fee": 0.0, "sub": "同户转账" if same_owner else "行内转账",
                    "balance": float(src_book.balance), "txn": txn_view(t_out)}, None
        else:  # INTER 跨行：只扣转出方 + 手续费，不更新系统外收款账户
            if not to_no or not to_bank:  # E-1 收款信息不合法
                return None, ("E-1", "跨行转账需填写收款行和收款账号")
            werr = src_book.debit(amount + fee)
            if werr:
                return None, werr
            t_out = TxnRecorder.record(db, business_type=C.TXN_TRANSFER_OUT, amount=amount,
                                       user_id=g.user["_id"], customer_id=src["customer_id"],
                                       account_id=src["_id"], request_id=request_id, session=s)
            if fee > 0:
                write_txn(db, business_type=C.TXN_TRANSFER_FEE, amount=fee, user_id=g.user["_id"],
                          customer_id=src["customer_id"], account_id=src["_id"],
                          related_id=t_out["_id"], session=s)
            write_audit(db, user_id=g.user["_id"], action="TRANSFER", object_type="account",
                        object_id=from_no, result=C.RESULT_SUCCESS,
                        detail={"type": "跨行", "to_bank": to_bank, "to": to_no,
                                "amount": str(amount), "fee": str(fee)}, session=s)
            return {"fee": float(fee), "sub": "跨行转账（登记汇出，不代表真实到账）",
                    "balance": float(src_book.balance), "txn": txn_view(t_out)}, None

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    if isinstance(res, tuple) and res[0] == "DUP":
        return _dup_response(db, res[1])
    return ok(res, "转账成功")


# ---------- UC-109 当日冲正 ----------
@bp.post("/reverse")
@clerk
def reverse():
    """柜员错账纠正：当日成功流水整体冲正（转账含转入/手续费腿），资金反向 + 红字流水留痕。"""
    d = _body()
    txn_no = (d.get("txn_no") or "").strip()
    reason = (d.get("reason") or "").strip()
    if not txn_no:
        return fail("E-REQ", "请填写要冲正的流水号", 400)
    if not reason:
        return fail("E-REQ", "冲正必须填写原因（供审计追溯）", 400)
    db = get_db()

    def txn(s):
        return ReversalService.reverse(db, txn_no, reason, g.user["_id"], s)

    res, err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    write_audit(db, user_id=g.user["_id"], action="REVERSAL", object_type="transaction",
                object_id=txn_no, result=C.RESULT_SUCCESS,
                detail={"reason": reason, "legs": res["legs_reversed"]})
    return ok(res, f"冲正成功，共反向 {res['legs_reversed']} 条流水")


# ---------- UC-105 账户/明细查询 ----------
@bp.get("/query")
@clerk
def query():
    ident = (request.args.get("ident") or "").strip()
    start = request.args.get("start")
    end = request.args.get("end")

    db = get_db()
    acc = db.account.find_one({"account_no": ident}) if ident else None  # 先按账号
    if acc:
        cust = db.customer.find_one({"_id": acc["customer_id"]})
    else:  # 再按任意身份标识定位客户及其账户
        cust = find_customer(db, ident=ident or None)
        acc = db.account.find_one({"customer_id": cust["_id"]}) if cust else None

    if not cust or not acc:  # E-1 未找到
        return fail("E-1", "未找到账户，请核对 账户/证件号/邮箱")

    # 时间范围过滤
    q = {"account_id": acc["_id"]}
    rng = parse_date_range(start, end)
    if rng is None:
        return fail("E-DATE", "日期格式应为 YYYY-MM-DD", 400)
    if rng:
        q["txn_time"] = rng
    txns = list(db.business_transaction.find(q).sort("txn_time", -1).limit(500))

    write_audit(db, user_id=g.user["_id"], action="QUERY_ACCOUNT", object_type="account",
                object_id=acc["account_no"], result=C.RESULT_SUCCESS)

    view = account_view(acc, cust)
    if acc["status"] == C.ACCOUNT_CLOSED:
        view["note"] = "账户已销户，仅展示历史信息"  # E-4
    return ok({
        "customer": customer_view(cust),
        "account": view,
        # 流水视图追加冲正标记（additive，不改 txn_view 共享实现）
        "transactions": [{**txn_view(t), "reversed": bool(t.get("reversed"))} for t in txns],
        "empty_hint": None if txns else "该时段无明细流水",  # E-3
    })


# ---------- UC-106 挂失/解挂/补卡 ----------
@bp.post("/card")
@clerk
def card_op():
    d = _body()
    ident = (d.get("ident") or "").strip()
    op = (d.get("op") or "").strip().upper()  # LOSS 挂失 / UNLOSS 解挂 / REISSUE 补卡

    db = get_db()
    account_no, cust, rerr = resolve_account_no(db, ident)  # 凭任意身份标识定位账户
    if rerr:
        return fail(rerr[0], rerr[1])
    acc = db.account.find_one({"account_no": account_no})  # 归属已由 resolve 保证

    cur = acc.get("card_status")
    if op == "LOSS":
        if cur == C.CARD_LOST:  # E-2
            return fail("E-2", "卡片已处于挂失状态，无需重复操作")
        updates, new_card, msg = {"card_status": C.CARD_LOST}, acc["card_no"], \
            "挂失成功，已限制支取（存款/转入不受影响）"
    elif op == "UNLOSS":
        if cur != C.CARD_LOST:  # E-2
            return fail("E-2", "卡片未处于挂失状态，无需解挂")
        updates, new_card, msg = {"card_status": C.CARD_NORMAL}, acc["card_no"], "解挂成功"
    elif op == "REISSUE":
        new_card = new_debit_card_no()
        updates = {"card_no": new_card, "card_status": C.CARD_NORMAL}
        msg = f"补卡成功，新卡号 {new_card}（原卡已失效）"
    else:
        return fail("E-OP", "操作类型非法", 400)

    def txn(s):  # 状态变更与审计同事务，保证补卡/挂失一定留痕
        # CAS：仅当卡号/卡状态未变才写，防并发双击重复补卡生成两个新卡号
        res = db.account.update_one({"_id": acc["_id"], "card_no": acc["card_no"], "card_status": cur},
                                    {"$set": updates}, session=s)
        if res.matched_count == 0:
            return ("E-2", "卡状态已变化，请刷新后重试")
        write_audit(db, user_id=g.user["_id"], action=f"CARD_{op}", object_type="account",
                    object_id=account_no, result=C.RESULT_SUCCESS,
                    detail={"old_card": acc["card_no"], "new_card": new_card}, session=s)
        return None

    err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok({"account_no": account_no, "card_no": new_card}, msg)


# ---------- UC-107 销户处理 ----------
@bp.post("/close-account")
@clerk
def close_account():
    d = _body()
    ident = (d.get("ident") or "").strip()

    db = get_db()
    account_no, cust, rerr = resolve_account_no(db, ident)  # 凭任意身份标识定位账户
    if rerr:
        return fail(rerr[0], rerr[1])
    acc = db.account.find_one({"account_no": account_no})  # 归属已由 resolve 保证
    if acc["status"] != C.ACCOUNT_NORMAL:  # E-4 冻结/挂失/已销户
        return fail("E-4", f"账户当前为「{C.ACCOUNT_STATUS_LABEL.get(acc['status'])}」，不可销户")
    # E-2 未结清贷款
    if db.loan.count_documents({"customer_id": cust["_id"],
                                "status": {"$in": [C.LOAN_PENDING, C.LOAN_APPROVED,
                                                   C.LOAN_ACTIVE, C.LOAN_OVERDUE]}}):
        return fail("E-2", "该客户存在未结清贷款，请先结清后再销户")
    # E-3 未关闭外汇子户
    if db.fx_account.count_documents({"customer_id": cust["_id"],
                                      "status": {"$in": [C.FX_NORMAL, C.FX_FROZEN]}}):
        return fail("E-3", "该客户存在未关闭的外汇子户，请先关闭")
    # E-6 未结清信用卡账单
    active_cards = list(db.credit_card.find({"customer_id": cust["_id"],
                        "status": {"$in": [C.CC_ACTIVE, C.CC_FROZEN, C.CC_LOST]}}))
    if active_cards:
        card_ids = [c["_id"] for c in active_cards]
        if db.credit_card_bill.count_documents({"credit_card_id": {"$in": card_ids},
                                                "status": {"$in": [C.BILL_UNPAID, C.BILL_PARTIAL]}}):
            return fail("E-6", "该客户存在未结清信用卡账单，请先还清后再销户")
        # 已用未清额度（含预借现金后尚未出账的欠款）也需先清偿
        if any(dec(c.get("credit_limit", 0)) - dec(c.get("available_limit", 0)) > 0 for c in active_cards):
            return fail("E-6", "该客户信用卡存在已用未清额度（含未出账欠款），请先还清后再销户")
    if dec(acc["balance"]) > 0:  # E-5 余额未清零
        return fail("E-5", f"账户仍有余额 {dec(acc['balance'])}，请先取款或转账清零")

    def txn(s):
        # 销户 CAS：状态正常 + 余额为 0 同时命中才置销户，天然防"并发窗口内又入了一笔钱"
        res = db.account.update_one(
            {"_id": acc["_id"], "status": C.ACCOUNT_NORMAL, "balance": m(0)},
            {"$set": {"status": C.ACCOUNT_CLOSED, "card_status": C.CARD_INVALID}}, session=s)
        if res.matched_count == 0:
            a = db.account.find_one({"_id": acc["_id"]}, session=s)
            if a["status"] != C.ACCOUNT_NORMAL:
                return ("E-4", f"账户当前为「{C.ACCOUNT_STATUS_LABEL.get(a['status'])}」，不可销户")
            return ("E-5", f"账户仍有余额 {dec(a['balance'])}，请先取款或转账清零")
        if db.loan.count_documents({"customer_id": cust["_id"],
                "status": {"$in": [C.LOAN_PENDING, C.LOAN_APPROVED, C.LOAN_ACTIVE, C.LOAN_OVERDUE]}}, session=s):
            return ("E-2", "该客户存在未结清贷款，请先结清后再销户")  # 事务内复查，整体回滚保护
        if db.fx_account.count_documents({"customer_id": cust["_id"],
                "status": {"$in": [C.FX_NORMAL, C.FX_FROZEN]}}, session=s):
            return ("E-3", "该客户存在未关闭的外汇子户，请先关闭")
        write_txn(db, business_type=C.TXN_CLOSE_ACCOUNT, amount=0, user_id=g.user["_id"],
                  customer_id=cust["_id"], account_id=acc["_id"], session=s)
        write_audit(db, user_id=g.user["_id"], action=C.TXN_CLOSE_ACCOUNT, object_type="account",
                    object_id=account_no, result=C.RESULT_SUCCESS, session=s)
        return None

    err = run_in_transaction(txn)
    if err:
        return fail(err[0], err[1])
    return ok({"account_no": account_no}, "销户成功，账户已关闭，卡片已失效")


# ---------- UC-108 客户信息更新 ----------
@bp.post("/update-customer")
@clerk
def update_customer():
    d = _body()
    ident = (d.get("ident") or "").strip()
    db = get_db()

    cust = find_customer(db, ident=ident or None)
    if not cust:  # E-1
        return fail("E-1", "客户不存在，请重新输入查询条件")
    if not match_identity(cust, ident):  # E-2 身份核验：ident须确为该客户的证件号/邮箱/手机号
        return fail("E-2", "身份核验失败，请使用证件号、邮箱或手机号定位客户")

    updates = {}
    old = {}
    for f in ("phone", "address", "occupation"):
        if f in d and d[f] is not None:
            updates[f] = str(d[f]).strip()[:C.TEXT_MAX]  # 文本长度上限，防超长脏数据
            old[f] = cust.get(f)
    if "phone" in updates:  # E-3 手机号格式（与开户同一套规则）
        pok, preason, normalized = validate_phone(updates["phone"])
        if not pok:
            return fail("E-3", preason)
        updates["phone"] = normalized
    if d.get("email") is not None and str(d.get("email")).strip():  # 允许更新邮箱（校验+唯一）
        eok, ereason, new_email = validate_email(d.get("email"))
        if not eok:
            return fail("E-3", ereason)
        dup = db.customer.find_one({"email": new_email, "_id": {"$ne": cust["_id"]}})
        if dup:
            return fail("E-3", "该邮箱已被其他客户使用")
        old["email"] = cust.get("email")
        updates["email"] = new_email

    # E-4 关键信息（姓名/证件号）变更需二次确认
    for f in ("name", "new_id_no"):
        if d.get(f):
            if not d.get("confirm"):
                return fail("E-4", "涉及姓名/证件号等关键信息变更，请二次确认（confirm=true）并填写原因")
            target = "name" if f == "name" else "id_no"
            new_val = str(d[f]).strip()
            if target == "name" and not new_val:  # 姓名不能被清空（与开户一致的非空不变式）
                return fail("E-REQ", "姓名不能为空", 400)
            if target == "name":
                new_val = new_val[:C.TEXT_MAX]
            if target == "id_no":
                valid, reason, new_val = validate_id_no(cust.get("id_type", "身份证"), new_val)  # 归一化
                if not valid:
                    return fail("E-3", reason)
                if db.customer.find_one({"id_no": new_val, "_id": {"$ne": cust["_id"]}}):
                    return fail("E-3", "新证件号已被其他客户占用")
            old[target] = cust.get(target)
            updates[target] = new_val

    if not updates:
        return fail("E-REQ", "没有需要更新的字段", 400)

    db.customer.update_one({"_id": cust["_id"]}, {"$set": updates})
    write_audit(db, user_id=g.user["_id"], action="UPDATE_CUSTOMER", object_type="customer",
                object_id=cust["customer_no"], result=C.RESULT_SUCCESS,
                detail={"old": old, "new": updates, "reason": d.get("reason")})
    cust.update(updates)
    return ok({"customer": customer_view(cust)}, "客户信息更新成功")
