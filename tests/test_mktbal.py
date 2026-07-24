# build_mktbal 純函式：TWSE 逗號數字解析（空白/全形空白＝0、非數字＝None 的降級語意）
import build_mktbal as bm


def test_parse_num_commas():
    assert bm.parse_num("1,234,567") == 1234567
    assert bm.parse_num("30,241,914,000") == 30241914000


def test_parse_num_blank_is_zero():
    assert bm.parse_num("") == 0
    assert bm.parse_num("　") == 0   # 全形空白（TWSE 表格常見）
    assert bm.parse_num(" ") == 0


def test_parse_num_none_and_garbage():
    assert bm.parse_num(None) is None
    assert bm.parse_num("—") is None
    assert bm.parse_num("N/A") is None


def test_parse_num_float_truncates_to_int():
    assert bm.parse_num("123.9") == 123
