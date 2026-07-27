"""Pest & disease catalog for coastal-California vegetables.

Predefined, agronomically-parameterised catalog the farmer can activate/extend
per zone. Each entry carries the parameters the model needs:

- ``kind``: "fungal" (leaf-wetness + temperature driven) or "insect" (degree-day driven)
- ``temp_opt``: (lo, hi) °C window favourable to development
- ``rh_min``: relative humidity (%) above which leaf wetness / infection rises (fungal)
- ``wet_hours``: leaf-wetness hours (RH≥90%) needed for an infection event (fungal)
- ``dd_base``: developmental base temperature °C for degree-day accumulation (insect)
- ``dd_threshold``: accumulated degree-days that mark active pressure / a generation (insect)
- ``crops``: coastal crops most affected
- ``scout``: what to look for in the field (ground-truth tip)

Honest limitation: satellites localise stress, not species. This model raises
*calibrated risk* from real weather + phenology; field scouting confirms identity.
"""

from __future__ import annotations

PEST_CATALOG: dict[str, dict] = {
    "downy_mildew": {
        "name": "Mildiú velloso", "kind": "fungal", "temp_opt": (10, 20),
        # UC IPM: sporulation occurs 5-25°C broadly; under Central Coast California
        # conditions specifically, 4h of leaf wetness is enough for infection — our
        # wet_hours already matched this exactly. ipm.ucanr.edu/agriculture/lettuce/downy-mildew/
        "rh_min": 90, "wet_hours": 4, "crops": ["Lechuga", "Espinaca", "Brócoli", "Coliflor", "Vid"],
        "scout": "Manchas amarillas en el haz y vello gris-blanco en el envés de hojas viejas.",
    },
    "sclerotinia": {
        "name": "Moho blanco / caída de lechuga", "kind": "fungal", "temp_opt": (15, 22),
        # UC IPM: S. minor (the coastal-California species) has optimum ~18°C,
        # range 6-30°C — window kept centered on that. ipm.ucanr.edu/agriculture/lettuce/lettuce-drop/
        # NOTE rh_min/wet_hours are NOT sourced: this disease is driven by soil
        # moisture, which our model has no input for — RH/leaf-wetness is a proxy,
        # kept as a reasoned estimate rather than a cited number.
        "rh_min": 90, "wet_hours": 6, "crops": ["Lechuga", "Apio", "Alcachofa"],
        "scout": "Pudrición acuosa en la base y micelio algodonoso blanco con esclerocios negros.",
    },
    "botrytis": {
        "name": "Botrytis (moho gris)", "kind": "fungal", "temp_opt": (18, 24),
        # UC IPM: most active 65-75°F (18-24°C); infection needs continuous wetness
        # ≥6h OR RH>90% for ≥6h — both figures now match exactly.
        # ipm.ucanr.edu/home-and-landscape/botrytis-rot-gray-mold-on-strawberries/
        "rh_min": 90, "wet_hours": 6, "crops": ["Fresa", "Lechuga", "Alcachofa", "Vid"],
        "scout": "Moho gris polvoriento sobre fruto/hojas senescentes con tejido marrón blando.",
    },
    "powdery_mildew": {
        "name": "Oídio (mildiú polvoso)", "kind": "fungal", "temp_opt": (21, 29),
        # UC Davis Gubler-Thomas risk index: optimal fungal growth 70-85°F (21-29°C).
        # wet_hours=0 confirmed correct — unlike other fungal diseases here, powdery
        # mildew does NOT need free water/leaf wetness to infect. rh_min has no
        # equivalent citation in the Gubler-Thomas model (it's temperature-driven,
        # not RH-driven) — kept as a reasoned estimate. apsnet.org/edcenter/apsnetfeatures/Pages/UCDavisRisk.aspx
        "rh_min": 70, "wet_hours": 0, "crops": ["Fresa", "Alcachofa", "Vid"],
        "scout": "Polvo blanco en el haz de las hojas; prospera con humedad alta sin agua libre.",
    },
    "dbm": {
        "name": "Polilla dorso de diamante (DBM)", "kind": "insect", "temp_opt": (20, 30),
        # Back-calculated from UC IPM's published development times (29/16/12 days
        # at 20/25/30.5°C) via linear regression — NOT a directly-cited DD base
        # (UC IPM's own degree-day table wasn't reachable via search); treat as a
        # derived estimate, more grounded than the prior placeholder but not a
        # primary-source number. ipm.ucanr.edu/PMG/r108301311.html
        "dd_base": 12.6, "dd_threshold": 215, "crops": ["Brócoli", "Coliflor"],
        "scout": "Larvas verdes que hacen 'ventanas' en hojas; capullos de seda en el envés.",
    },
    "aphids": {
        "name": "Pulgón", "kind": "insect", "temp_opt": (18, 27),
        # Green peach aphid (Myzus persicae): base ~4°C is the converging figure
        # across studies (3.9-4.0°C); optimum growth 26.7-27.5°C; egg-to-adult
        # phenology model reports ~174 DD above a 4°C base.
        # academic.oup.com/ee/article/27/2/337/2464574
        "dd_base": 4.0, "dd_threshold": 174, "crops": ["Lechuga", "Espinaca", "Brócoli", "Fresa"],
        "scout": "Colonias en brotes y envés; mielada pegajosa y hojas enrolladas.",
    },
    "thrips": {
        "name": "Trips", "kind": "insect", "temp_opt": (20, 30),
        # Western flower thrips: egg-to-adult requires 268 DD above a 7.9°C base
        # (McDonald, Bale & Walters 1998); a separate GDD model uses 7.2°C — close
        # agreement. eje.cz/pdfs/eje/1998/02/14.pdf
        "dd_base": 7.9, "dd_threshold": 268, "crops": ["Lechuga", "Fresa", "Apio"],
        "scout": "Plateado/raspado en hojas y puntos negros (excremento); vector de virus.",
    },
    "armyworm": {
        "name": "Gusano soldado", "kind": "insect", "temp_opt": (20, 32),
        # Beet armyworm (Spodoptera exigua) meta-analysis: base threshold 10.7-12.2°C
        # and thermal constant 397-458 DD depending on host plant; vegetable-host
        # figures (green pea/potato) averaged here. Development continues 15-35°C,
        # enhanced above 20°C. ncbi.nlm.nih.gov/pmc/articles/PMC11855858/
        "dd_base": 11.0, "dd_threshold": 440, "crops": ["Lechuga", "Espinaca", "Brócoli"],
        "scout": "Defoliación nocturna; larvas grandes y excremento abundante.",
    },
    "whitefly": {
        "name": "Mosca blanca", "kind": "insect", "temp_opt": (23, 32),
        # Silverleaf whitefly (Bemisia tabaci): commonly-cited developmental
        # threshold ~12.5°C; thermal constants range 250-385 DD by host (midpoint
        # used); optimal development 23-32°C.
        # academic.oup.com/aesa/article-abstract/89/3/375/100953
        "dd_base": 12.5, "dd_threshold": 300, "crops": ["Brócoli", "Fresa"],
        "scout": "Nube de adultos blancos al mover la planta; ninfas y mielada en el envés.",
    },
    # ── Vineyard-specific ──────────────────────────────────────────────────────
    "lobesia": {
        "name": "Polilla del racimo (Lobesia botrana)", "kind": "insect", "temp_opt": (20, 30),
        # Published day-degree models for L. botrana commonly use a ~7.3°C lower
        # threshold (e.g. Iberian-population studies); upper threshold ~33°C.
        # dd_threshold (generation total) NOT found from a primary source for
        # California populations — kept as the prior reasoned estimate.
        "dd_base": 7.3, "dd_threshold": 350, "crops": ["Vid"],
        "scout": "Perforaciones y sedas en los racimos; larvas dentro de las bayas; entrada para Botrytis.",
    },
    "vine_mealybug": {
        "name": "Cochinilla harinosa de la vid", "kind": "insect", "temp_opt": (18, 30),
        # Vine mealybug (Planococcus ficus) GDD model: lower threshold 16.6°C,
        # upper 35.6°C — meaningfully higher than the prior placeholder (10°C).
        # dd_threshold (generation total) not found from a primary source — kept
        # as the prior reasoned estimate. blog.pestprophet.com/how-to-use-the-vine-mealybug-growing-degree-day-model/
        "dd_base": 16.6, "dd_threshold": 250, "crops": ["Vid"],
        "scout": "Masas algodonosas blancas en tronco/racimos y mielada con fumagina; vector de virus del enrollado.",
    },
    "sharpshooter": {
        "name": "Chicharrita (vector de Pierce's disease)", "kind": "insect", "temp_opt": (20, 32),
        # NOT calibrated — left as the prior reasoned estimate. Flagging a bigger
        # issue found while researching this one: real Pierce's disease risk
        # (the thing that actually matters, not the vector's summer degree-days)
        # is modeled from WINTER minimum temperatures (cold kills Xylella in the
        # vine over winter — the Purcell Pierce's Disease Risk Index), not from
        # warm-season insect development like every other entry here. Scoring
        # this one the same way as an aphid/thrips is structurally the wrong
        # model, not just an uncalibrated threshold — worth a real fix, out of
        # scope for a value-only calibration pass.
        "dd_base": 10.0, "dd_threshold": 200, "crops": ["Vid"],
        "scout": "Adultos saltadores en cañas; vector de Xylella (Pierce's): hojas quemadas y secado de brazos.",
    },
}


def default_pests_for_crop(crop_type: str | None) -> list[str]:
    """Catalog keys relevant to a crop (used as the default active set)."""
    crop = (crop_type or "").strip()
    keys = [k for k, v in PEST_CATALOG.items() if crop in v.get("crops", [])]
    return keys or list(PEST_CATALOG.keys())[:4]


def get_pest(key: str) -> dict | None:
    return PEST_CATALOG.get(key)
