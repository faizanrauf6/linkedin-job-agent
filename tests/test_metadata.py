from agent.browser import metadata
from agent.models import Search


def test_salary_requires_explicit_currency_and_period():
    value = metadata("United States · Remote · USD 100,000 - 140,000 per year", Search())
    assert value["salary_min"] == 100000
    assert value["salary_max"] == 140000
    assert value["salary_currency"] == "USD"
    assert value["salary_period"] == "year"
    assert "salary_min" not in metadata("$100K - $140K · 5 years experience", Search())
    assert "salary_min" not in metadata("USD 100K - CAD 140K per year", Search())


def test_hourly_salary_and_sponsorship_are_not_guessed():
    value = metadata("EUR 40–60 per hour. Visa sponsorship available", Search())
    assert value["salary_min"] == 40
    assert value["salary_period"] == "hour"
    assert value["sponsorship_available"] is True
    assert metadata("We do not offer visa sponsorship", Search())["sponsorship_available"] is False
    assert "sponsorship_available" not in metadata("Visa sponsorship may be available", Search())


def test_multiple_pay_ranges_and_trailing_sponsorship_qualifiers_stay_unknown():
    assert "salary_min" not in metadata("US: USD 150k per year; Canada: USD 80k per year", Search())
    assert metadata("Visa sponsorship available: No", Search()).get("sponsorship_available") is not True
    assert metadata("Visa sponsorship available only for exceptional candidates", Search()).get("sponsorship_available") is not True
