"""Audit B-m7: a length converted to micrometres is one correctly rounded operation.

``to_micrometers`` multiplied by a binary approximation of the unit's factor, so ``9 nm``
became ``0.009000000000000001`` while ``0.009 µm`` stayed ``0.009``: one pixel size given
in nm by a header and in µm by ``calibration.csv`` differed, and a brick comparing sizes
exactly (MorphoFeature's square-pixel test) called the pixels non-square. A unit below the
micrometre now divides by its integer inverse factor and a larger unit multiplies by its
integer factor, so a value that is exact in binary gives the double nearest its exact
length in micrometres, whatever the unit it is given in.
"""

import random
from decimal import Decimal, localcontext
from fractions import Fraction

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    PhysicalSizes,
    inspect_images,
    read_physical_sizes,
    to_micrometers,
)

# Micrometres per unit, written out here independently of the module: (multiplier, divisor).
FACTORS = {
    "picometer": (1, 1_000_000),
    "angstrom": (1, 10_000),
    "nanometer": (1, 1_000),
    "micrometer": (1, 1),
    "millimeter": (1_000, 1),
    "centimeter": (10_000, 1),
    "meter": (1_000_000, 1),
}
SPELLINGS = {
    "picometer": ("pm", "picometre", "picometers"),
    "angstrom": ("Å", "Å", "angstroms"),
    "nanometer": ("nm", "nanometre", "nanometers"),
    "micrometer": ("µm", "μm", "um", "micron", "micrometres"),
    "millimeter": ("mm", "millimetre"),
    "centimeter": ("cm", "centimetres"),
    "meter": ("m", "metre", "meters"),
}


def expected(value, name):
    multiplier, divisor = FACTORS[name]
    return value / divisor if divisor > 1 else value * multiplier


def sample_values():
    """Whole numbers 1..20000, then 20000 seeded random doubles from 1e-6 to 1e6."""
    rng = random.Random(20261103)
    values = [float(n) for n in range(1, 20_001)]
    values += [10 ** rng.uniform(-6, 6) for _ in range(20_000)]
    return values


VALUES = sample_values()


@pytest.mark.parametrize("name", sorted(FACTORS))
def test_one_operation_with_the_integer_factor(name):
    for spelling in (name, *SPELLINGS[name]):
        assert to_micrometers(9, spelling) == expected(9, name)
    for value in VALUES:
        got = to_micrometers(value, name)
        assert got == expected(value, name), (value, name)
        assert 0 < got < float("inf")


def test_nine_nanometres_is_the_double_nearest_0_009():
    assert to_micrometers(9, "nm") == 0.009 == to_micrometers(0.009, "µm")
    assert to_micrometers(9, "nm") == 9 / 1000
    assert to_micrometers(9, "nm") != 9 * 1e-3  # the old product: 0.009000000000000001
    assert to_micrometers(90, "Å") == 0.009
    assert to_micrometers(9000, "pm") == 0.009
    assert to_micrometers(0.5, "mm") == 500.0


def binary_exact(fraction):
    """Return the double equal to ``fraction``, or None when no double is."""
    value = float(fraction)
    return value if Fraction(value) == fraction else None


def decimal_text(fraction):
    """Write a fraction whose denominator divides a power of ten as its exact decimal."""
    with localcontext() as context:
        context.prec = 200
        text = str(Decimal(fraction.numerator) / Decimal(fraction.denominator))
    assert Fraction(Decimal(text)) == fraction
    return text


def exact_lengths():
    """Lengths in micrometres that are exact in binary in at least one unit."""
    rng = random.Random(7)
    lengths = set()
    for multiplier, divisor in FACTORS.values():
        for _ in range(400):
            given = Fraction(rng.randint(1, 10**7), 2 ** rng.randint(0, 12))
            lengths.add(given * multiplier / divisor)
    for n in range(1, 2_001):
        lengths.add(Fraction(n, 1000))  # n nm, as 0.001 * n µm
        lengths.add(Fraction(n, 10_000))  # n Å
        lengths.add(Fraction(n * 1000))  # n mm
    return sorted(lengths)


def test_one_length_in_any_unit_where_it_is_exact_gives_one_double():
    compared = in_two_units = 0
    for length in exact_lengths():
        nearest = float(length)  # the double nearest the exact length (correctly rounded)
        exact_in = []
        for name, (multiplier, divisor) in FACTORS.items():
            value = binary_exact(length * divisor / multiplier)
            if value is not None:
                got = to_micrometers(value, name)
                assert got == nearest, (length, name, value)
                exact_in.append(name)
        # The same length written out in micrometres ("0.009"), as calibration.csv states it.
        assert to_micrometers(float(decimal_text(length)), "µm") == nearest
        compared += 1
        in_two_units += len(exact_in) >= 2
    assert compared > 8_000 and in_two_units > 8_000


def write_ome(path, size, unit):
    tifffile.imwrite(
        path,
        np.zeros((8, 8), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "YX",
            "PhysicalSizeX": size,
            "PhysicalSizeXUnit": unit,
            "PhysicalSizeY": size,
            "PhysicalSizeYUnit": unit,
        },
    )
    return path


def test_a_header_in_nm_and_a_stated_size_in_um_are_the_same_number(tmp_path):
    sizes = read_physical_sizes(write_ome(tmp_path / "a.ome.tif", 9, "nm"))
    stated = PhysicalSizes(x=0.009, y=0.009, space_unit="micrometer")
    assert sizes.micrometers("X") == sizes.micrometers("Y") == 0.009
    assert [sizes.micrometers(r) for r in "XY"] == [stated.micrometers(r) for r in "XY"]


def test_x_in_nm_and_y_in_um_of_one_size_are_square(tmp_path):
    # Before the fix: non_square_pixels, "X 0.009000000000000001, Y 0.009 micrometres".
    path = tmp_path / "exp/c/a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path,
        np.zeros((8, 8), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "YX",
            "PhysicalSizeX": 9,
            "PhysicalSizeXUnit": "nm",
            "PhysicalSizeY": 0.009,
            "PhysicalSizeYUnit": "µm",
        },
    )
    report = inspect_images(tmp_path / "exp", ["c/a.ome.tif"])
    assert "non_square_pixels" not in {issue.code for issue in report.issues}
