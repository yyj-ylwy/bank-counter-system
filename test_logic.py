"""纯逻辑自测（不依赖数据库）：金额精度、证件校验、到期日、汇率方向、最低还款。

运行： python test_logic.py    全部通过则打印 OK。
"""
from datetime import datetime

from common import D, dec, D6, validate_id_no
from loan import add_months
import constants as C


def test_money_precision():
    # 金额四舍五入到分，且运算精确（不出现浮点误差）
    assert str(D("100.005")) == "100.01"
    assert str(D(0.1) + D(0.2)) == "0.30"       # 若用 float 会是 0.30000000000000004
    assert str(D(D("7.25") * D(100))) == "725.00"  # 乘积须再包一层 D() 收敛到 2 位（与业务代码一致）
    assert dec(None) == D(0)


def test_validate_id():
    assert validate_id_no("身份证", "110101199001011234")[0] is True
    assert validate_id_no("身份证", "11010119900101123X")[0] is True
    assert validate_id_no("身份证", "123")[0] is False          # 长度不足
    assert validate_id_no("身份证", "")[0] is False             # 空
    assert validate_id_no("护照", "E12345678")[0] is True


def test_add_months():
    assert add_months(datetime(2026, 1, 31), 1).strftime("%Y-%m-%d") == "2026-02-28"  # 月末收敛
    assert add_months(datetime(2026, 1, 15), 12).strftime("%Y-%m-%d") == "2027-01-15" # 跨年
    assert add_months(datetime(2026, 11, 10), 3).strftime("%Y-%m-%d") == "2027-02-10" # 跨年进位


def test_fx_direction_amounts():
    # 客户买入外币用卖出价：花的人民币 = 外币 * 卖出价
    sell = D6("7.25")
    cny_buy = D(D(100) * sell)
    assert str(cny_buy) == "725.00"
    # 客户卖出外币用买入价：得到人民币 = 外币 * 买入价
    buy = D6("7.10")
    cny_sell = D(D(100) * buy)
    assert str(cny_sell) == "710.00"


def test_min_repay():
    total = D("2000")
    min_rate = D("0.10")
    assert str(D(total * min_rate)) == "200.00"


def test_transfer_fee():
    # 费率必须用 dec() 保留精度（业务代码里费率来自 get_param_dec，用的就是 dec）；
    # 若误用 D() 会把 0.001 收敛成 0.00 —— 这里正是校验这一点。
    amount = D("10000")
    fee_rate = dec("0.001")
    assert str(D(amount * fee_rate)) == "10.00"
    assert str(D("0.001")) == "0.00"  # 反向确认：D() 只保留 2 位，不能用于费率


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print("OK - 全部逻辑自测通过")
