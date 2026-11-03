"""Audit B-m7: one length written in any unit gives one double in micrometres.

``to_micrometers`` multiplied by a binary approximation of the unit's factor, so ``9 nm``
became ``0.009000000000000001`` while ``0.009 µm`` stayed ``0.009``: one pixel size given
in nm by a header and in µm by ``calibration.csv`` differed, and a brick comparing sizes
exactly (MorphoFeature's square-pixel test) called the pixels non-square. Dividing by the
integer factor fixed ``9 nm`` but not ``108.37 nm`` (``108.37 / 1000`` is
``0.10837000000000001``), as for about a fifth of the decimals that are not exact in binary.
A length is now converted from the shortest decimal of its double, with the point moved by
the unit's power of ten and rounded once: for a decimal of at most 15 significant digits
that is the decimal written, so the same length in any unit gives the double ``float``
gives for it written in micrometres.
"""

import random
import struct
from decimal import ROUND_FLOOR, Context, Decimal, localcontext

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    PhysicalSizes,
    inspect_images,
    read_physical_sizes,
    to_micrometers,
)

# The power of ten from each unit to micrometres, written out here independently of the module.
POWERS = {
    "picometer": -6,
    "angstrom": -4,
    "nanometer": -3,
    "micrometer": 0,
    "millimeter": 3,
    "centimeter": 4,
    "meter": 6,
}
SPELLINGS = {
    "picometer": ("pm", "picometre", "picometers"),
    "angstrom": ("Å", "Å", "angstroms"),
    "nanometer": ("nm", "nanometre", "nanometers"),
    "micrometer": ("µm", "μm", "um", "micron", "micrometres"),
    "millimeter": ("mm", "millimetre"),
    "centimeter": ("cm", "centimetres"),
    "meter": ("m", "metre", "meters"),
}
# A decimal context wide enough to hold any double's shortest decimal moved by 10**±6 exactly.
WIDE = Context(prec=60, Emin=-2000, Emax=2000)


def oracle(value, power):
    """The double nearest ``repr(value)`` times ``10**power``, computed with exact decimals."""
    return float(Decimal(repr(value)).scaleb(power, context=WIDE))


def same_length_everywhere(mantissa, exponent):
    """Check the length ``mantissa`` x 10**``exponent`` µm, written in every unit as text."""
    in_micrometres = float(f"{mantissa}e{exponent}")  # as calibration.csv would state it
    for name, power in POWERS.items():
        written = float(f"{mantissa}e{exponent - power}")  # the same length, in that unit
        assert to_micrometers(written, name) == in_micrometres, (mantissa, exponent, name)


@pytest.mark.parametrize("name", sorted(POWERS))
def test_every_spelling_converts_by_its_power_of_ten(name):
    for spelling in (name, *SPELLINGS[name]):
        assert to_micrometers(9, spelling) == float(f"9e{POWERS[name]}")


def test_nine_and_108_37_nanometres_are_the_decimals_written_in_micrometres():
    assert to_micrometers(9, "nm") == 0.009 == to_micrometers(0.009, "µm")
    assert to_micrometers(9, "nm") != 9 * 1e-3  # the old product: 0.009000000000000001
    assert to_micrometers(108.37, "nm") == 0.10837 == to_micrometers(0.10837, "µm")
    assert to_micrometers(108.37, "nm") != 108.37 / 1000  # 0.10837000000000001
    assert to_micrometers(108.33, "nm") == 0.10833  # the header and calibration.csv example
    assert to_micrometers(0.13, "nm") == 0.00013 != 0.13 / 1000
    assert to_micrometers(90, "Å") == 0.009 == to_micrometers(9000, "pm")
    assert to_micrometers(0.5, "mm") == 500.0 and to_micrometers(1.1, "m") == 1_100_000.0


def test_every_decimal_of_four_digits_is_one_double_in_every_unit():
    # Exhaustive: 0.000000001 to 999.9 µm, every mantissa 1..9999, in all seven units.
    for exponent in range(-12, 0):
        for mantissa in range(1, 10_000):
            same_length_everywhere(mantissa, exponent)


@pytest.mark.parametrize("digits", range(1, 16))
def test_seeded_decimals_of_up_to_15_digits_are_one_double_in_every_unit(digits):
    rng = random.Random(20261103 + digits)
    for _ in range(2_000):
        mantissa = rng.randrange(10 ** (digits - 1), 10**digits)
        same_length_everywhere(mantissa, rng.randrange(-60, 40) - digits)


def test_16_and_17_digits_are_one_double_when_the_decimal_is_the_shortest():
    # At 16 or 17 digits two decimals can give one double; the one written is then lost.
    rng = random.Random(17)
    shortest = 0
    for _ in range(20_000):
        digits = rng.choice((16, 17))
        mantissa = rng.randrange(10 ** (digits - 1), 10**digits)
        exponent = rng.randrange(-30, 10) - digits
        for name, power in POWERS.items():
            text = f"{mantissa}e{exponent - power}"  # the length written in that unit
            if Decimal(repr(float(text))) == Decimal(text):
                assert to_micrometers(float(text), name) == float(f"{mantissa}e{exponent}")
                shortest += 1
    assert 10_000 < shortest < 140_000
    # "0.95408556734169085" is the double of "0.9540855673416908", which is what converts.
    assert float("0.95408556734169085") == float("0.9540855673416908")
    assert to_micrometers(0.95408556734169085, "nm") == float("0.0009540855673416908")
    assert float("0.0009540855673416908") != float("0.00095408556734169085")


def random_doubles(count, seed):
    """Seeded positive finite doubles over the whole range, subnormals included."""
    rng = random.Random(seed)
    values = []
    while len(values) < count:
        (value,) = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(63)))
        if value > 0 and np.isfinite(value):
            values.append(value)
    return [*values, 5e-324, 2.2250738585072014e-308, 1.7976931348623157e308]


@pytest.mark.parametrize("name", sorted(POWERS))
def test_any_positive_finite_double_converts_deterministically_or_is_refused(name):
    power = POWERS[name]
    previous = 0.0
    for value in sorted(random_doubles(20_000, 3 + power)):
        expected = oracle(value, power)
        if expected == 0 or expected == float("inf"):
            with pytest.raises(ValueError, match="not a positive finite number of micrometres"):
                to_micrometers(value, name)
            continue
        got = to_micrometers(value, name)
        assert got == expected == to_micrometers(value, name), (value, name)
        assert 0 < got < float("inf") and got >= previous  # never decreasing
        previous = got
        if power == 0:
            assert got == value


def test_the_decimal_context_is_not_used():
    values = [108.37, 0.13, 9.0, 0.95408556734169085, 123456789012345.6]
    before = [to_micrometers(v, u) for v in values for u in POWERS]
    with localcontext() as context:
        context.prec, context.rounding = 2, ROUND_FLOOR
        assert [to_micrometers(v, u) for v in values for u in POWERS] == before


@pytest.mark.parametrize(
    ("value", "unit"),
    [(1e308, "m"), (1.7976931348623157e308, "mm"), (1e-320, "pm"), (5e-324, "nm"), (1e-322, "Å")],
)
def test_a_length_beyond_the_float_range_in_micrometres_is_refused(value, unit):
    with pytest.raises(ValueError, match="not a positive finite number of micrometres"):
        to_micrometers(value, unit)


def test_the_smallest_and_largest_doubles_in_micrometres_are_kept():
    assert to_micrometers(5e-324, "µm") == 5e-324
    assert to_micrometers(1.7976931348623157e308, "µm") == 1.7976931348623157e308
    assert to_micrometers(5e-321, "nm") == 5e-324
    assert to_micrometers(1.5e302, "m") == 1.5e308


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


@pytest.mark.parametrize(("size", "stated"), [(9, 0.009), (108.37, 0.10837), (108.33, 0.10833)])
def test_a_header_in_nm_and_a_stated_size_in_um_are_the_same_number(tmp_path, size, stated):
    sizes = read_physical_sizes(write_ome(tmp_path / "a.ome.tif", size, "nm"))
    given = PhysicalSizes(x=stated, y=stated, space_unit="micrometer")
    assert sizes.micrometers("X") == sizes.micrometers("Y") == stated
    assert [sizes.micrometers(r) for r in "XY"] == [given.micrometers(r) for r in "XY"]


@pytest.mark.parametrize(("in_nm", "in_um"), [(9, 0.009), (108.37, 0.10837)])
def test_x_in_nm_and_y_in_um_of_one_size_are_square(tmp_path, in_nm, in_um):
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
            "PhysicalSizeX": in_nm,
            "PhysicalSizeXUnit": "nm",
            "PhysicalSizeY": in_um,
            "PhysicalSizeYUnit": "µm",
        },
    )
    report = inspect_images(tmp_path / "exp", ["c/a.ome.tif"])
    assert "non_square_pixels" not in {issue.code for issue in report.issues}
