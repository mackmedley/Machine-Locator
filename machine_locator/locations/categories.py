"""Category priors for vending placement.

Each entry maps a set of OpenStreetMap tag filters onto four industry
judgements, all on a 0-10 scale:

``traffic``     how many people pass through on a typical day
``dwell``       how long a person is stuck there with nothing to do
``captivity``   how far they are from a gas station or a fridge of their own
``access``      how easy the site is to service (parking, ground floor, hours
                a driver can actually show up)

``difficulty``  is the counterweight: 0 means walk in and ask the owner, 10
means a national contract already owns the account and you will not displace
it. Hospitals and big-box retail score high here -- they are wonderful
locations that a one-truck operator will almost never win.

These are starting priors, not gospel. Tune them in this file as your own
placements come back with real numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CategorySpec:
    key: str
    label: str
    filters: Tuple[Dict[str, str], ...]
    traffic: float
    dwell: float
    captivity: float
    access: float
    difficulty: float
    note: str = ""
    # Machine types that historically earn their keep at this category.
    fits: Tuple[str, ...] = ("snack", "drink")


def _f(**kwargs: str) -> Dict[str, str]:
    return dict(kwargs)


CATEGORY_SPECS: Tuple[CategorySpec, ...] = (
    # ---------------------------------------------------------- industrial
    CategorySpec(
        "manufacturing", "Manufacturing / plant",
        (_f(building="industrial"), _f(man_made="works"), _f(industrial="factory")),
        traffic=7.5, dwell=9.5, captivity=9.5, access=8.0, difficulty=4.0,
        note="Shift workers, unpaid short breaks, often nothing within a mile. "
             "The classic bread-and-butter vending account.",
        fits=("snack", "drink", "coffee", "food"),
    ),
    CategorySpec(
        "warehouse", "Warehouse / distribution",
        (_f(building="warehouse"), _f(landuse="industrial"), _f(industrial="warehouse")),
        traffic=6.5, dwell=9.0, captivity=9.0, access=8.5, difficulty=3.5,
        note="Night shifts and dock crews. Drinks move hard in an OKC summer.",
        fits=("snack", "drink", "coffee"),
    ),
    CategorySpec(
        "truck_stop", "Truck stop / freight yard",
        (_f(amenity="fuel", hgv="yes"), _f(amenity="truck_stop")),
        traffic=7.0, dwell=6.0, captivity=5.0, access=9.0, difficulty=6.0,
        note="High volume, but the C-store inside is your direct competitor.",
        fits=("drink", "coffee"),
    ),
    # ------------------------------------------------------------- fitness
    CategorySpec(
        "gym", "Gym / fitness center",
        (_f(leisure="fitness_centre"), _f(amenity="gym"), _f(leisure="sports_centre")),
        traffic=8.0, dwell=6.5, captivity=8.0, access=8.0, difficulty=4.5,
        note="Drinks, protein and sports recovery. Snacks underperform here.",
        fits=("drink", "healthy", "coffee"),
    ),
    # ------------------------------------------------------------- waiting
    CategorySpec(
        "laundromat", "Laundromat",
        (_f(shop="laundry"), _f(shop="dry_cleaning")),
        traffic=6.0, dwell=9.5, captivity=9.0, access=9.0, difficulty=2.0,
        note="90 minutes with nothing to do and a pocket of quarters. "
             "Small owners, easy yes.",
        fits=("snack", "drink"),
    ),
    CategorySpec(
        "auto_repair", "Auto repair / tire shop",
        (_f(shop="car_repair"), _f(shop="tyres"), _f(shop="car_parts")),
        traffic=5.5, dwell=8.5, captivity=8.5, access=9.0, difficulty=2.0,
        note="Waiting rooms are the definition of a captive audience.",
        fits=("snack", "drink", "coffee"),
    ),
    CategorySpec(
        "car_dealership", "Car dealership",
        (_f(shop="car"),),
        traffic=6.0, dwell=8.0, captivity=7.0, access=8.5, difficulty=4.0,
        note="Service lounges. Many already offer free coffee -- sell snacks.",
        fits=("snack", "drink"),
    ),
    CategorySpec(
        "car_wash", "Car wash",
        (_f(amenity="car_wash"),),
        traffic=5.5, dwell=6.0, captivity=7.5, access=9.0, difficulty=2.0,
        note="Short dwell but pure impulse buying, and rent is usually free.",
        fits=("drink",),
    ),
    # ---------------------------------------------------------- residential
    CategorySpec(
        "apartments", "Apartment complex",
        (_f(building="apartments"), _f(building="residential", residential="apartments")),
        traffic=6.5, dwell=7.0, captivity=7.5, access=7.0, difficulty=3.5,
        note="Laundry rooms and pool houses. Vandalism risk -- check lighting.",
        fits=("snack", "drink"),
    ),
    CategorySpec(
        "self_storage", "Self storage",
        (_f(shop="storage_rental"),),
        traffic=4.0, dwell=6.0, captivity=8.5, access=9.0, difficulty=2.0,
        note="Low volume, near-zero competition, easy owner conversation.",
        fits=("drink",),
    ),
    CategorySpec(
        "rv_park", "RV park / campground",
        (_f(tourism="caravan_site"), _f(tourism="camp_site")),
        traffic=4.5, dwell=8.0, captivity=9.0, access=7.5, difficulty=2.5,
        note="Captive by definition. Seasonal swing is steep.",
        fits=("snack", "drink"),
    ),
    # --------------------------------------------------------- hospitality
    CategorySpec(
        "hotel", "Hotel / motel",
        (_f(tourism="hotel"), _f(tourism="motel"), _f(tourism="hostel")),
        traffic=6.5, dwell=9.0, captivity=9.5, access=8.0, difficulty=4.5,
        note="24/7 guests who will not drive out for a soda at 11pm.",
        fits=("snack", "drink", "sundry"),
    ),
    # ------------------------------------------------------------- offices
    CategorySpec(
        "office", "Office building",
        (_f(building="office"), _f(office="company"), _f(building="commercial")),
        traffic=7.0, dwell=8.5, captivity=7.0, access=6.5, difficulty=5.0,
        note="Break rooms. Headcount matters more than square footage -- "
             "under ~40 people it rarely pays.",
        fits=("snack", "drink", "coffee"),
    ),
    CategorySpec(
        "call_center", "Call center / business park",
        (_f(office="telecommunication"), _f(office="it"), _f(landuse="commercial"),),
        traffic=7.5, dwell=9.0, captivity=8.0, access=7.0, difficulty=4.5,
        note="Scheduled breaks, no time to leave the building. Excellent.",
        fits=("snack", "drink", "coffee", "food"),
    ),
    # ---------------------------------------------------------- government
    CategorySpec(
        "government", "Government office / DMV / courthouse",
        (_f(amenity="townhall"), _f(amenity="courthouse"), _f(office="government"),
         _f(amenity="post_office")),
        traffic=7.0, dwell=8.5, captivity=8.5, access=6.0, difficulty=7.0,
        note="Long lines and long waits, but expect an RFP and state "
             "vending-facility rules rather than a handshake.",
        fits=("snack", "drink"),
    ),
    CategorySpec(
        "public_safety", "Fire / police station",
        (_f(amenity="fire_station"), _f(amenity="police")),
        traffic=4.0, dwell=9.5, captivity=8.5, access=7.5, difficulty=4.0,
        note="Small headcount, but 24-hour shifts and steady spend.",
        fits=("snack", "drink", "coffee"),
    ),
    CategorySpec(
        "library", "Library / community center",
        (_f(amenity="library"), _f(amenity="community_centre")),
        traffic=5.5, dwell=7.5, captivity=7.0, access=7.0, difficulty=5.5,
        note="Municipal approval needed, but almost no competition inside.",
        fits=("snack", "drink"),
    ),
    # ---------------------------------------------------------- healthcare
    CategorySpec(
        "hospital", "Hospital",
        (_f(amenity="hospital"),),
        traffic=9.5, dwell=9.5, captivity=8.0, access=5.0, difficulty=8.5,
        note="Superb traffic, almost always locked up by a national contract. "
             "Chase the medical office buildings around it instead.",
        fits=("snack", "drink", "coffee", "food"),
    ),
    CategorySpec(
        "clinic", "Clinic / medical office",
        (_f(amenity="clinic"), _f(amenity="doctors"), _f(healthcare="centre"),
         _f(amenity="dentist")),
        traffic=6.0, dwell=8.0, captivity=7.5, access=7.5, difficulty=3.5,
        note="Waiting rooms with no cafeteria. Reachable for a small operator.",
        fits=("snack", "drink"),
    ),
    CategorySpec(
        "care_home", "Nursing / assisted living",
        (_f(amenity="nursing_home"), _f(social_facility="nursing_home"),
         _f(amenity="social_facility")),
        traffic=5.5, dwell=9.0, captivity=9.0, access=7.5, difficulty=4.5,
        note="Staff plus visiting families, around the clock.",
        fits=("snack", "drink", "coffee"),
    ),
    # ------------------------------------------------------------ education
    CategorySpec(
        "college", "College / university / trade school",
        (_f(amenity="college"), _f(amenity="university")),
        traffic=8.5, dwell=8.5, captivity=7.0, access=6.0, difficulty=6.5,
        note="Big money, usually a campus-wide exclusive contract.",
        fits=("snack", "drink", "coffee", "food"),
    ),
    CategorySpec(
        "school", "K-12 school",
        (_f(amenity="school"),),
        traffic=7.5, dwell=8.0, captivity=8.0, access=5.5, difficulty=8.0,
        note="USDA Smart Snacks limits what you can sell during the day and "
             "districts bid the contract. Staff lounges are the realistic ask.",
        fits=("healthy", "drink"),
    ),
    # ------------------------------------------------------------ recreation
    CategorySpec(
        "recreation", "Bowling / cinema / arcade",
        (_f(leisure="bowling_alley"), _f(amenity="cinema"), _f(leisure="amusement_arcade")),
        traffic=6.5, dwell=8.0, captivity=5.0, access=8.0, difficulty=5.5,
        note="They sell concessions themselves -- expect a no unless it is a "
             "lobby they do not staff.",
        fits=("drink",),
    ),
    CategorySpec(
        "sports_complex", "Ballfields / sports complex",
        (_f(leisure="pitch"), _f(leisure="stadium"), _f(leisure="track")),
        traffic=6.0, dwell=8.5, captivity=9.0, access=7.0, difficulty=4.0,
        note="Weekend tournaments spike hard. Outdoor machines need shade.",
        fits=("drink",),
    ),
    CategorySpec(
        "transit", "Bus / transit station",
        (_f(amenity="bus_station"), _f(railway="station"), _f(aeroway="terminal")),
        traffic=8.0, dwell=7.5, captivity=8.5, access=6.0, difficulty=6.5,
        note="Great numbers, heavy shrink, and the authority controls placement.",
        fits=("snack", "drink"),
    ),
    # -------------------------------------------------------------- retail
    CategorySpec(
        "hardware", "Hardware / home improvement",
        (_f(shop="doityourself"), _f(shop="hardware"), _f(shop="trade")),
        traffic=6.5, dwell=5.0, captivity=6.0, access=8.5, difficulty=4.5,
        note="Contractor traffic first thing in the morning. Coffee and drinks.",
        fits=("drink", "coffee"),
    ),
)


# Tag pairs that indicate somewhere a customer could buy the same soda cheaper.
COMPETITION_FILTERS: Tuple[Dict[str, str], ...] = (
    _f(shop="convenience"),
    _f(shop="supermarket"),
    _f(amenity="fast_food"),
    _f(amenity="cafe"),
    _f(amenity="fuel"),
    _f(shop="kiosk"),
)

# Machines already in the ground.
VENDING_FILTERS: Tuple[Dict[str, str], ...] = (
    _f(amenity="vending_machine"),
)

BY_KEY: Dict[str, CategorySpec] = {spec.key: spec for spec in CATEGORY_SPECS}


def classify(tags: Dict[str, str]) -> Optional[CategorySpec]:
    """Pick the best category for a set of OSM tags.

    Specs are checked in declaration order and the most specific match wins:
    a filter with two tag conditions beats a single-tag filter, so a
    ``building=residential`` + ``residential=apartments`` pair is not stolen by
    a looser rule declared earlier.
    """
    best: Optional[CategorySpec] = None
    best_specificity = 0
    for spec in CATEGORY_SPECS:
        for filt in spec.filters:
            if all(tags.get(k) == v for k, v in filt.items()):
                if len(filt) > best_specificity:
                    best, best_specificity = spec, len(filt)
    return best


def all_filters() -> List[Dict[str, str]]:
    """Every tag filter we want Overpass to return, de-duplicated."""
    seen: List[Dict[str, str]] = []
    for spec in CATEGORY_SPECS:
        for filt in spec.filters:
            if filt not in seen:
                seen.append(filt)
    return seen
