"""Geometry helpers: distances, spatial bucketing, clustering, route ordering.

Deliberately dependency free -- numpy/scipy would be overkill for the few
thousand points a single metro produces.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Tuple

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def meters_to_deg_lat(meters: float) -> float:
    return meters / 111_320.0


def meters_to_deg_lon(meters: float, at_lat: float) -> float:
    scale = max(math.cos(math.radians(at_lat)), 1e-6)
    return meters / (111_320.0 * scale)


class SpatialIndex:
    """Uniform grid index. O(1)-ish radius queries without a KD-tree dep."""

    def __init__(self, points: Iterable[Tuple[float, float, object]], cell_m: float = 500.0):
        self.cell_m = cell_m
        self._cells: Dict[Tuple[int, int], List[Tuple[float, float, object]]] = defaultdict(list)
        self._count = 0
        for lat, lon, payload in points:
            self._cells[self._cell_key(lat, lon)].append((lat, lon, payload))
            self._count += 1

    def __len__(self) -> int:
        return self._count

    def _cell_key(self, lat: float, lon: float) -> Tuple[int, int]:
        return (
            int(lat / meters_to_deg_lat(self.cell_m)),
            int(lon / meters_to_deg_lon(self.cell_m, lat)),
        )

    def within(self, lat: float, lon: float, radius_m: float) -> List[Tuple[float, float, object]]:
        """Every indexed point within radius_m of (lat, lon)."""
        span = int(math.ceil(radius_m / self.cell_m))
        base_lat, base_lon = self._cell_key(lat, lon)
        found: List[Tuple[float, float, object]] = []
        for dlat in range(-span, span + 1):
            for dlon in range(-span, span + 1):
                for plat, plon, payload in self._cells.get((base_lat + dlat, base_lon + dlon), ()):
                    if haversine_m(lat, lon, plat, plon) <= radius_m:
                        found.append((plat, plon, payload))
        return found

    def count_within(self, lat: float, lon: float, radius_m: float) -> int:
        return len(self.within(lat, lon, radius_m))


def kmeans(
    points: Sequence[Tuple[float, float]],
    k: int,
    iterations: int = 50,
    seed: int = 7,
) -> List[int]:
    """Lightweight k-means over lat/lon. Returns a cluster index per point.

    Used to split a pile of scored sites into drivable service territories.
    Distances are computed in meters so the lat/lon aspect ratio does not
    stretch clusters east-west.
    """
    if not points:
        return []
    k = max(1, min(k, len(points)))
    rng = random.Random(seed)

    # k-means++ style seeding: spread the initial centroids out.
    centroids = [points[rng.randrange(len(points))]]
    while len(centroids) < k:
        dists = [
            min(haversine_m(p[0], p[1], c[0], c[1]) for c in centroids) ** 2
            for p in points
        ]
        total = sum(dists)
        if total <= 0:
            centroids.append(points[rng.randrange(len(points))])
            continue
        target = rng.random() * total
        acc = 0.0
        for point, d in zip(points, dists):
            acc += d
            if acc >= target:
                centroids.append(point)
                break

    assignments = [0] * len(points)
    for _ in range(iterations):
        changed = False
        for i, (lat, lon) in enumerate(points):
            best, best_d = 0, float("inf")
            for c_idx, (clat, clon) in enumerate(centroids):
                d = haversine_m(lat, lon, clat, clon)
                if d < best_d:
                    best, best_d = c_idx, d
            if assignments[i] != best:
                assignments[i] = best
                changed = True
        for c_idx in range(k):
            members = [p for p, a in zip(points, assignments) if a == c_idx]
            if members:
                centroids[c_idx] = (
                    sum(m[0] for m in members) / len(members),
                    sum(m[1] for m in members) / len(members),
                )
        if not changed:
            break
    return assignments


def order_route(points: Sequence[Tuple[float, float]], start: int = 0) -> List[int]:
    """Order stops for a service run: nearest-neighbour seed, then 2-opt.

    Not optimal, but for the 15-40 stops on a real vending route it lands within
    a few percent of optimal in milliseconds.
    """
    n = len(points)
    if n <= 2:
        return list(range(n))

    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_m(points[i][0], points[i][1], points[j][0], points[j][1])
            dist[i][j] = dist[j][i] = d

    unvisited = set(range(n))
    tour = [start]
    unvisited.remove(start)
    while unvisited:
        last = tour[-1]
        nxt = min(unvisited, key=lambda j: dist[last][j])
        tour.append(nxt)
        unvisited.remove(nxt)

    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b = tour[i - 1], tour[i]
                c = tour[j]
                d_node = tour[(j + 1) % n]
                delta = (
                    dist[a][c] + dist[b][d_node] - dist[a][b] - dist[c][d_node]
                )
                if delta < -1.0:  # meter of slack avoids float churn
                    tour[i : j + 1] = reversed(tour[i : j + 1])
                    improved = True
    return tour


def route_length_m(points: Sequence[Tuple[float, float]], order: Sequence[int]) -> float:
    total = 0.0
    for a, b in zip(order, list(order)[1:]):
        total += haversine_m(points[a][0], points[a][1], points[b][0], points[b][1])
    return total
