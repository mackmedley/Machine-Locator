import pytest

from machine_locator.geo import SpatialIndex
from machine_locator.locations.categories import BY_KEY, all_filters, classify
from machine_locator.locations.scoring import (
    SiteScorer, grade_for, hours_score, machine_recommendation, size_bonus, WEIGHTS,
)
from machine_locator.models import Site


def make_site(category="laundromat", **kwargs):
    spec = BY_KEY[category]
    defaults = dict(
        id="node/1", name="Test", category=category, category_label=spec.label,
        lat=35.4676, lon=-97.5164,
    )
    defaults.update(kwargs)
    return Site(**defaults)


def test_weights_sum_to_one():
    assert pytest.approx(sum(WEIGHTS.values()), abs=1e-9) == 1.0


def test_classify_prefers_more_specific_filter():
    spec = classify({"building": "residential", "residential": "apartments"})
    assert spec.key == "apartments"


def test_classify_returns_none_for_irrelevant_tags():
    assert classify({"amenity": "bench", "backrest": "yes"}) is None


def test_all_filters_are_deduplicated():
    filters = all_filters()
    seen = [tuple(sorted(f.items())) for f in filters]
    assert len(seen) == len(set(seen))


def test_hours_score_rewards_round_the_clock():
    assert hours_score("24/7")[0] > hours_score("Mo-Fr 09:00-17:00")[0]
    assert hours_score("")[0] == 6.0


def test_size_bonus_penalises_tiny_complexes():
    bonus, reason = size_bonus({"building:flats": "24"}, "apartments")
    assert bonus < 0 and "too small" in reason


def test_size_bonus_rewards_large_hotels():
    bonus, _ = size_bonus({"rooms": "180"}, "hotel")
    assert bonus == 1.5


def test_competition_lowers_the_score(settings):
    clean = SiteScorer(settings, SpatialIndex([]), SpatialIndex([]), SpatialIndex([]))
    crowded_points = [(35.4676, -97.5164 + i * 0.0005, "store") for i in range(6)]
    crowded = SiteScorer(settings, SpatialIndex(crowded_points), SpatialIndex([]), SpatialIndex([]))

    quiet_score = clean.score(make_site()).score
    busy_score = crowded.score(make_site()).score
    assert busy_score < quiet_score


def test_existing_machine_on_site_is_penalised(settings):
    without = SiteScorer(settings, SpatialIndex([]), SpatialIndex([]), SpatialIndex([]))
    with_machine = SiteScorer(
        settings, SpatialIndex([]), SpatialIndex([(35.4676, -97.5164, "vm")]), SpatialIndex([])
    )
    base = without.score(make_site()).score
    saturated = with_machine.score(make_site())
    assert saturated.score < base
    assert saturated.vending_nearby == 1
    assert any("already mapped" in r for r in saturated.reasons)


def test_hard_to_win_categories_score_below_easy_ones(settings):
    scorer = SiteScorer(settings, SpatialIndex([]), SpatialIndex([]), SpatialIndex([]))
    school = scorer.score(make_site("school"))
    laundromat = scorer.score(make_site("laundromat"))
    # A school has more traffic, but a one-truck operator will not win it.
    assert laundromat.score > school.score


def test_score_is_bounded_and_graded(settings):
    scorer = SiteScorer(settings, SpatialIndex([]), SpatialIndex([]), SpatialIndex([]))
    for key in BY_KEY:
        site = scorer.score(make_site(key))
        assert 0.0 <= site.score <= 100.0
        assert site.grade in {"A+", "A", "B", "C", "D"}
        assert site.reasons


def test_unknown_category_scores_zero(settings):
    scorer = SiteScorer(settings, SpatialIndex([]), SpatialIndex([]), SpatialIndex([]))
    site = Site(id="x", name="x", category="nope", category_label="?", lat=35.0, lon=-97.0)
    assert scorer.score(site).score == 0.0


def test_grade_boundaries():
    assert grade_for(85) == "A+"
    assert grade_for(84.9) == "A"
    assert grade_for(0) == "D"


def test_machine_recommendation_is_category_specific():
    assert "cold drink" in machine_recommendation(make_site("gym"))
    assert machine_recommendation(Site(id="x", name="x", category="?", category_label="?",
                                       lat=0, lon=0)) == ""
