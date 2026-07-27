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
        "rh_min": 90, "wet_hours": 4, "crops": ["Lechuga", "Espinaca", "Brócoli", "Coliflor", "Vid"],
        "scout": "Manchas amarillas en el haz y vello gris-blanco en el envés de hojas viejas.",
    },
    "sclerotinia": {
        "name": "Moho blanco / caída de lechuga", "kind": "fungal", "temp_opt": (15, 22),
        "rh_min": 90, "wet_hours": 6, "crops": ["Lechuga", "Apio", "Alcachofa"],
        "scout": "Pudrición acuosa en la base y micelio algodonoso blanco con esclerocios negros.",
    },
    "botrytis": {
        "name": "Botrytis (moho gris)", "kind": "fungal", "temp_opt": (15, 25),
        "rh_min": 85, "wet_hours": 6, "crops": ["Fresa", "Lechuga", "Alcachofa", "Vid"],
        "scout": "Moho gris polvoriento sobre fruto/hojas senescentes con tejido marrón blando.",
    },
    "powdery_mildew": {
        "name": "Oídio (mildiú polvoso)", "kind": "fungal", "temp_opt": (18, 27),
        "rh_min": 70, "wet_hours": 0, "crops": ["Fresa", "Alcachofa", "Vid"],
        "scout": "Polvo blanco en el haz de las hojas; prospera con humedad alta sin agua libre.",
    },
    "dbm": {
        "name": "Polilla dorso de diamante (DBM)", "kind": "insect", "temp_opt": (20, 30),
        "dd_base": 7.3, "dd_threshold": 180, "crops": ["Brócoli", "Coliflor"],
        "scout": "Larvas verdes que hacen 'ventanas' en hojas; capullos de seda en el envés.",
    },
    "aphids": {
        "name": "Pulgón", "kind": "insect", "temp_opt": (18, 26),
        "dd_base": 5.0, "dd_threshold": 120, "crops": ["Lechuga", "Espinaca", "Brócoli", "Fresa"],
        "scout": "Colonias en brotes y envés; mielada pegajosa y hojas enrolladas.",
    },
    "thrips": {
        "name": "Trips", "kind": "insect", "temp_opt": (20, 30),
        "dd_base": 10.0, "dd_threshold": 150, "crops": ["Lechuga", "Fresa", "Apio"],
        "scout": "Plateado/raspado en hojas y puntos negros (excremento); vector de virus.",
    },
    "armyworm": {
        "name": "Gusano soldado", "kind": "insect", "temp_opt": (22, 32),
        "dd_base": 10.0, "dd_threshold": 200, "crops": ["Lechuga", "Espinaca", "Brócoli"],
        "scout": "Defoliación nocturna; larvas grandes y excremento abundante.",
    },
    "whitefly": {
        "name": "Mosca blanca", "kind": "insect", "temp_opt": (24, 32),
        "dd_base": 10.0, "dd_threshold": 160, "crops": ["Brócoli", "Fresa"],
        "scout": "Nube de adultos blancos al mover la planta; ninfas y mielada en el envés.",
    },
    # ── Vineyard-specific ──────────────────────────────────────────────────────
    "lobesia": {
        "name": "Polilla del racimo (Lobesia botrana)", "kind": "insect", "temp_opt": (20, 30),
        "dd_base": 7.0, "dd_threshold": 350, "crops": ["Vid"],
        "scout": "Perforaciones y sedas en los racimos; larvas dentro de las bayas; entrada para Botrytis.",
    },
    "vine_mealybug": {
        "name": "Cochinilla harinosa de la vid", "kind": "insect", "temp_opt": (18, 30),
        "dd_base": 10.0, "dd_threshold": 250, "crops": ["Vid"],
        "scout": "Masas algodonosas blancas en tronco/racimos y mielada con fumagina; vector de virus del enrollado.",
    },
    "sharpshooter": {
        "name": "Chicharrita (vector de Pierce's disease)", "kind": "insect", "temp_opt": (20, 32),
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
