"""The answers about the masks (experiment-v1 4.5) and their evidence (Stage 1, PR 16)."""

import numpy as np
import pytest
import tifffile

from channel_images import write_channels, write_masks
from morphobricks_preprocessing import find_masks, preflight
from morphobricks_preprocessing.axes import AxisMapping, AxisReview
from morphobricks_preprocessing.draft_answers import (
    ChannelAnswer,
    DraftAnswers,
    UnitAnswer,
)
from morphobricks_preprocessing.draft_format import ABSENT, SPEC_COMMIT
from morphobricks_preprocessing.draft_ids import id_problems, propose_id
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.mask_answers import (
    MaskAnswerRefused,
    drawn_on,
    dropped_channels,
    mask_ids,
    masks_unanswered,
    masks_without_files,
    proposed_mask_id,
    with_mask,
    without_mask,
)
from morphobricks_preprocessing.mask_evidence import mask_evidence, representative
from morphobricks_preprocessing.preview import (
    MASK_SIZE_DIFFERS,
    THUMBNAIL_SIDE,
    PreviewCache,
    PreviewRefused,
    StripOptions,
    labels_thumbnail,
    read_plane,
)

EMPTY = DraftAnswers(ABSENT, SPEC_COMMIT)
LABELS = DraftAnswers(
    ABSENT, SPEC_COMMIT, channels={"DNA": ChannelAnswer(), "ACTIN": ChannelAnswer()}
)


@pytest.mark.parametrize(
    ("name", "codes"),
    [
        ("Nuclei", ()),
        ("nuclei_seg", ()),
        ("nuclei-mask", ("E_ID_SYNTAX",)),
        ("cell", ("E_ID_RESERVED",)),
        ("cells_in_tissue", ("E_ID_RESERVED",)),  # the object-set part "in"
        ("in", ("E_ID_RESERVED",)),
        ("2nuclei", ("E_ID_SYNTAX",)),
    ],
)
def test_a_mask_id_follows_the_token_and_object_set_rules(name, codes):
    assert id_problems("masks", name) == codes
    assert propose_id("masks", name) == (None if codes else name)


def test_the_folder_name_is_proposed_when_valid_and_free():
    assert proposed_mask_id("Nuclei", EMPTY) == "Nuclei"
    assert proposed_mask_id("nuclei-mask", EMPTY) is None
    taken = with_mask(EMPTY, "other", "nuclei", "instances")
    assert proposed_mask_id("Nuclei", taken) is None  # case collision with another mask ID


def test_with_mask_saves_one_answer_per_type_and_replaces_it():
    first = with_mask(EMPTY, "Nuclei", "Nuclei", "instances")
    assert mask_ids(first) == {"Nuclei": "Nuclei"}
    renamed = with_mask(first, "Nuclei", "Nuclei_seg", "binary")
    assert set(renamed.masks) == {"Nuclei_seg"}
    assert renamed.masks["Nuclei_seg"].representation == "binary"
    assert without_mask(renamed, "Nuclei").masks == {}


@pytest.mark.parametrize(
    ("mask_id", "representation", "channels", "code"),
    [
        ("Nuclei", "objects", (), "E_ENUM"),
        ("Nuclei", None, (), "E_ENUM"),
        ("nuclei-mask", "instances", (), "E_ID_SYNTAX"),
        ("", "instances", (), "E_ID_TEXT"),
        ("Nuclei", "instances", ("PHASE",), "E_UNKNOWN_CHANNEL"),
        ("Nuclei", "instances", ("DNA", "DNA"), "E_DUPLICATE_VALUE"),
    ],
)
def test_a_wrong_answer_is_refused_with_its_code(mask_id, representation, channels, code):
    with pytest.raises(MaskAnswerRefused) as refused:
        with_mask(LABELS, "Nuclei", mask_id, representation, channels)
    assert code in refused.value.codes


def test_two_types_cannot_share_an_id_even_by_case():
    one = with_mask(EMPTY, "a", "Nuclei", "instances")
    with pytest.raises(MaskAnswerRefused) as same:
        with_mask(one, "b", "Nuclei", "instances")
    assert same.value.codes == ("id_in_use_in_draft",)
    with pytest.raises(MaskAnswerRefused) as case:
        with_mask(one, "b", "NUCLEI", "instances")
    assert case.value.codes == ("E_ID_CASE_COLLISION",)


def test_unanswered_gone_and_dropped_channels_are_listed():
    answers = with_mask(LABELS, "Nuclei", "Nuclei", "instances", ("DNA",))
    assert masks_unanswered(answers, ["Nuclei", "tissue"]) == ("tissue",)
    assert masks_without_files(answers, ["tissue"]) == ("Nuclei",)
    assert dropped_channels(answers) == {}
    fewer = DraftAnswers(
        ABSENT, SPEC_COMMIT, channels={"ACTIN": ChannelAnswer()}, masks=answers.masks
    )
    assert dropped_channels(fewer) == {"Nuclei": ("DNA",)}


def test_the_mask_is_drawn_on_the_channel_tagged_with_its_first_made_from_label():
    unit = ("ctrl", "rep1", "single")
    answers = with_mask(LABELS, "Nuclei", "Nuclei", "instances", ("DNA",))
    tagged = DraftAnswers(
        ABSENT,
        SPEC_COMMIT,
        channels=answers.channels,
        masks=answers.masks,
        units={unit: UnitAnswer(("ACTIN", "DNA"), "channels")},
    )
    assert drawn_on(tagged, "Nuclei", unit) == 1
    assert drawn_on(answers, "Nuclei", unit) is None  # the unit is not tagged yet
    assert drawn_on(tagged, "other", unit) is None


def test_labels_thumbnail_takes_every_nth_pixel_and_refuses_another_size(tmp_path):
    image = tmp_path / "i.tif"
    tifffile.imwrite(image, np.zeros((900, 1100), np.uint16), metadata={"axes": "YX"})
    shown = read_plane(image, AxisMapping("YX", "YX"), max_side=THUMBNAIL_SIDE)
    labels = np.random.default_rng(1).integers(0, 40, (900, 1100), dtype=np.uint16)
    thumb = labels_thumbnail(labels, shown)
    assert np.array_equal(thumb.pixels, labels[:: shown.step, :: shown.step])
    with pytest.raises(PreviewRefused) as refused:
        labels_thumbnail(labels[:450], shown)
    assert refused.value.code == MASK_SIZE_DIFFERS


def test_the_evidence_counts_values_and_draws_nothing_undeclared(tmp_path):
    root = tmp_path / "exp"
    images = {"ctrl/rep1/b.tif": 2, "ctrl/rep1/a.tif": 1}
    for rel, seed in images.items():
        write_channels(root / rel, ["DAPI", "Actin"], ["blobs", "filaments"], seed=seed)
    masks = write_masks(tmp_path / "masks", images, {"Nuclei": "labels"})
    pre = preflight(root)
    inventory = find_masks(masks, pre.discovery, pre.metadata.files)
    mapping = AxisMapping("CYX", "CYX")
    review = AxisReview(mapping, dict.fromkeys(images, mapping), ())
    bound = {rel: Fingerprint.of(root / rel) for rel in images}
    assert representative(inventory, "Nuclei", sorted(images)).image == "ctrl/rep1/a.tif"
    found = mask_evidence(
        inventory,
        "Nuclei",
        root,
        sorted(images),
        review,
        1,
        StripOptions(cache=PreviewCache(), memory_limit=2**30),
        bound,
    )
    assert (found.images_with, found.images, found.dtypes) == (2, 2, ("uint16",))
    assert found.image == "ctrl/rep1/a.tif" and found.values == 8 and found.channel == 1
    assert found.overlay_png(None) is None  # never drawn before the user says what it is
    assert found.overlay_png("instances").startswith(b"\x89PNG")
    assert found.mask_png().startswith(b"\x89PNG") and found.image_png().startswith(b"\x89PNG")
    missing = mask_evidence(
        inventory,
        "Nuclei",
        root,
        sorted(images),
        review,
        5,
        StripOptions(cache=PreviewCache(), memory_limit=2**30),
        bound,
    )
    assert missing.picture is None and missing.problem  # no channel 5: said, not guessed
