from __future__ import annotations

import re
from typing import Any

import pint

REGISTRY = pint.UnitRegistry()
REGISTRY.define("molar = mole / liter = M")


def canonical_unit(unit: str) -> str:
    return (
        unit.replace("μ", "u").replace("µ", "u").replace("·", "*").replace("%", "percent").replace("^", "**")
    )


def quantity(value: float, unit: str) -> Any:
    raw = canonical_unit(unit).strip()
    factor = re.match(r"^(10\*\*[+-]?\d+)\s+(.+)$", raw)
    if factor:
        exponent = int(factor[1].split("**")[1])
        return value * 10**exponent * REGISTRY(factor[2])
    return value * REGISTRY(raw or "dimensionless")


def to_si(value: float, unit: str) -> float:
    q = quantity(value, unit)
    if q.check("[substance] / [length] ** 3"):
        return float(q.to("molar").magnitude)  # pharmacy concentration reference is M
    if unit in {"%", "percent"}:
        return value  # percent ranges are expressed in percent points
    return float(q.to_base_units().magnitude)


def unit_class(unit: str | None) -> str:
    if not unit:
        return "dimensionless"
    if unit in {"%", "percent"}:
        return "percent"
    try:
        q = quantity(1, unit)
        for name, expr in (
            ("concentration", "molar"),
            ("time", "second"),
            ("clearance", "milliliter/minute/kilogram"),
            ("volume_per_mass", "liter/kilogram"),
            ("permeability", "centimeter/second"),
            ("mass_concentration", "nanogram/milliliter"),
            ("auc", "nanogram*hour/milliliter"),
            ("dimensionless", "dimensionless"),
        ):
            if q.dimensionality == REGISTRY(expr).dimensionality:
                return name
    except (pint.errors.PintError, ValueError):
        pass
    return "unknown"
