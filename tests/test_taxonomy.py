from server.taxonomy import classify, carry_columns, borrow_columns, family_display


def T(skill, **params):
    return classify(skill, params)


def test_carry_columns():
    assert carry_columns(87, 96) == [0, 1]  # 7+6=13 carries ones; 8+9+1=18 carries tens
    assert carry_columns(24, 47) == [0]      # 4+7=11
    assert carry_columns(30, 28) == []       # no carry
    assert carry_columns(98, 99) == [0, 1]


def test_borrow_columns():
    assert borrow_columns(75, 48) == [0]     # ones borrow only
    assert borrow_columns(565, 85) == [1]    # tens borrow only — the blind spot the flag missed
    assert borrow_columns(509, 63) == [1]
    assert borrow_columns(94, 22) == []
    assert borrow_columns(753, 95) == [0, 1]


def test_primitive_addition_within_20():
    t = T("addition", a=8, b=5, carry=True)
    assert t.tier == "primitive" and t.key == "add:5+8" and t.family == "add.bridge10"
    t = T("addition", a=4, b=4)
    assert t.tier == "primitive" and t.family == "add.within20"


def test_compound_addition_families_encode_carry_columns():
    t = T("addition", a=87, b=96)
    assert t.tier == "compound"
    assert t.family == "add.2d+2d.cot"       # carry in Ones and Tens
    assert t.key == t.family                  # compound items schedule at class grain
    assert t.features["crosses_hundred"] is True


def test_primitive_subtraction_within_20():
    t = T("subtraction", a=18, b=4)
    assert t.tier == "primitive" and t.key == "sub:18-4" and t.family == "sub.within20"
    t = T("subtraction", a=14, b=7)
    assert t.family == "sub.borrow20"        # 4-7 needs a borrow


def test_compound_subtraction_families():
    assert T("subtraction", a=565, b=85).family == "sub.3d-2d.bt"
    assert T("subtraction", a=94, b=22).family == "sub.2d-2d.n"
    assert T("subtraction", a=753, b=95).family == "sub.3d-2d.bot"


def test_across_zero_feature():
    t = T("subtraction", a=108, b=16)        # 8-6 ok; tens 0-1 borrows from hundreds
    assert 1 in t.features["borrow_cols"]
    t2 = T("subtraction", a=306, b=178)      # ones borrow pulls from a 0 tens digit
    assert t2.features["across_zero"] is True


def test_multiplication_table_and_beyond():
    t = T("multiplication", a=9, b=12)
    assert t.tier == "primitive" and t.key == "mul:9x12" and t.family == "mul.x12"
    t = T("multiplication", a=40, b=8)
    assert t.tier == "compound" and t.family == "mul.2dx1d"


def test_division_inverse_facts():
    t = T("division", a=56, b=7)
    assert t.tier == "primitive" and t.key == "div:7x8" and t.family == "div.x8"


def test_grounded_ops_are_compound():
    t = T("money_arithmetic", operation="split_bill", bill_amount=36, people=4)
    assert t.tier == "compound" and t.key == "money:split_bill"
    t = T("weather_math", operation="f_to_c_approx", high=68)
    assert t.key == "weather:f_to_c_approx"


def test_percent():
    t = T("percent_of", percentage=15, base=80)
    assert t.tier == "compound" and t.key == "pct:15" and t.family == "pct"


def test_family_display():
    assert family_display("sub.3d-2d.bt") == "3-digit − 2-digit, borrow in tens"
    assert family_display("add.2d+2d.cot") == "2-digit + 2-digit, carry in ones and tens"
    assert family_display("sub.borrow20") == "subtraction within 20 (borrow)"
    assert family_display("mul.x12") == "×12 table"


def test_unclassifiable_returns_none():
    assert classify("addition", {}) is None
    assert classify("unknown_skill", {"a": 1, "b": 2}) is None
