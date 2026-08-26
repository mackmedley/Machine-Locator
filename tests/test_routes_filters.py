from machine_locator.routes.filters import (
    locality, parse_financials, parse_machine_count, parse_money, score_relevance,
)


def test_parse_money_formats():
    assert parse_money("$45,000") == 45_000
    assert parse_money("$45K") == 45_000
    assert parse_money("$1.2M") == 1_200_000
    assert parse_money("Asking price: $ 78,500 obo") == 78_500


def test_parse_money_returns_none_when_absent():
    assert parse_money("Price on request") is None
    assert parse_money("") is None
    assert parse_money(None) is None


def test_parse_machine_count():
    assert parse_machine_count("Route of 32 machines on location") == 32
    assert parse_machine_count("18 vending machines") == 18
    assert parse_machine_count("consists of 12 stops") == 12
    assert parse_machine_count("no numbers here") is None


def test_parse_machine_count_rejects_absurd_values():
    assert parse_machine_count("999999 machines") is None


def test_parse_financials_finds_labelled_amounts():
    result = parse_financials("Asking $120,000. Cash Flow: $38,500. Gross Revenue: $210,000")
    assert result["cash_flow"] == 38_500
    assert result["gross_revenue"] == 210_000


def test_parse_financials_on_silent_ad():
    assert parse_financials("Great opportunity!") == {"cash_flow": None, "gross_revenue": None}


def test_locality_detects_metro_and_state():
    assert locality("Oklahoma City, OK") == (True, "OK")
    assert locality("Edmond, Oklahoma") == (True, "OK")
    assert locality("Tulsa, OK 74103") == (False, "OK")
    assert locality("Dallas, TX") == (False, "")


def test_relevance_ranks_a_real_local_route_highest():
    strong, _, is_local = score_relevance(
        "Established Vending Route - 32 Machines",
        "Cash Flow: $38,000 per year",
        "Oklahoma City, OK",
    )
    weak, _, _ = score_relevance("Vending machine business", "", "Dallas, TX")
    assert strong > weak
    assert is_local


def test_relevance_rejects_parts_listings():
    score, reasons, _ = score_relevance("Vending machine parts lot", "", "Tulsa, OK")
    assert score < 20
    assert any("not a route" in r for r in reasons)


def test_relevance_ignores_unrelated_ads():
    score, reasons, _ = score_relevance("Used sofa for sale", "", "OKC")
    assert score == 0.0
    assert reasons == ["no vending terms found"]


def test_relevance_flags_biz_op_language():
    _, reasons, _ = score_relevance(
        "Vending Route", "no experience necessary, locations guaranteed", "OKC"
    )
    assert any("biz-op" in r for r in reasons)
