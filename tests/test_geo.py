
from machine_locator.geo import (
    SpatialIndex, haversine_m, kmeans, order_route, route_length_m,
)

OKC = (35.4676, -97.5164)
EDMOND = (35.6528, -97.4781)


def test_haversine_known_distance():
    # OKC to Edmond is about 21 km.
    d = haversine_m(*OKC, *EDMOND)
    assert 19_000 < d < 23_000


def test_haversine_is_zero_for_same_point():
    assert haversine_m(*OKC, *OKC) == 0.0


def test_spatial_index_radius_query():
    points = [
        (35.4676, -97.5164, "center"),
        (35.4680, -97.5164, "45m north"),
        (35.6528, -97.4781, "edmond"),
    ]
    index = SpatialIndex(points, cell_m=500)
    assert index.count_within(*OKC, 100) == 2
    assert index.count_within(*OKC, 50_000) == 3
    assert len(index) == 3


def test_spatial_index_handles_empty():
    index = SpatialIndex([])
    assert index.count_within(*OKC, 1000) == 0


def test_spatial_index_matches_bruteforce():
    import random
    rng = random.Random(1)
    pts = [(35.4 + rng.random() * 0.3, -97.7 + rng.random() * 0.4, i) for i in range(400)]
    index = SpatialIndex(pts, cell_m=400)
    for radius in (150, 900, 2500):
        expected = sum(1 for lat, lon, _ in pts if haversine_m(*OKC, lat, lon) <= radius)
        assert index.count_within(*OKC, radius) == expected


def test_kmeans_separates_distinct_clusters():
    cluster_a = [(35.40 + i * 0.001, -97.60 + i * 0.001) for i in range(10)]
    cluster_b = [(35.70 + i * 0.001, -97.20 + i * 0.001) for i in range(10)]
    labels = kmeans(cluster_a + cluster_b, 2)
    assert len(set(labels[:10])) == 1
    assert len(set(labels[10:])) == 1
    assert labels[0] != labels[10]


def test_kmeans_clamps_k_to_population():
    assert len(set(kmeans([(35.0, -97.0), (35.1, -97.1)], 10))) <= 2


def test_kmeans_on_empty_input():
    assert kmeans([], 3) == []


def test_order_route_beats_naive_order():
    # A deliberately scrambled loop; 2-opt should shorten it.
    points = [
        (35.40, -97.60), (35.70, -97.20), (35.42, -97.58),
        (35.68, -97.22), (35.44, -97.56),
    ]
    naive = list(range(len(points)))
    optimised = order_route(points)
    assert sorted(optimised) == naive
    assert route_length_m(points, optimised) <= route_length_m(points, naive)


def test_order_route_starts_where_told():
    points = [(35.4, -97.6), (35.5, -97.5), (35.6, -97.4)]
    assert order_route(points, start=2)[0] == 2


def test_order_route_trivial_inputs():
    assert order_route([]) == []
    assert order_route([(35.0, -97.0)]) == [0]
