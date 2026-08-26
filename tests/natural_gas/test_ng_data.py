"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/20/26

Tests for the loaders in src/models/natural_gas/data.py.

``load_region_data`` is the one loader with no fallback -- regions are definitional, so every
unusable input must raise rather than substitute a hardcoded list.  These tests therefore lean
on the failure paths as heavily as the happy one.

Each case writes its own ng_region_data.csv into ``tmp_path`` so the tests pin loader behaviour
rather than the contents of input/natural_gas/, with one exception: ``test_repo_input_file``
reads the real file, which is what catches an edit to the shipped data that the synthetic
fixtures would never see.
"""

from itertools import pairwise
from pathlib import Path
from typing import ClassVar

import pytest

from definitions import PROJECT_ROOT
from src.models.natural_gas import data as ng_data
from src.models.natural_gas.data import load_region_data
from src.models.natural_gas.ng_config import NGConfig

HEADER = 'region,domestic,international,covered_areas,label'

# Two domestic regions and one international one: the smallest file that still exercises the
# partition, the labels, and the covered_areas column that is read but not returned.
BASIC_ROWS = [
    'mountain,True,-,"AZ, CO, ID, MT, NV, NM, UT, WY",Mountain (Rockies / Permian)',
    'pacific,True,-,"AK, CA, HI, OR, WA",Pacific',
    'canada,-,True,Canada,Canada',
]


def write_region_csv(directory: Path, rows: list[str], header: str = HEADER) -> Path:
    """Write an ng_region_data.csv into ``directory`` and return its path."""
    path = directory / 'ng_region_data.csv'
    path.write_text('\n'.join([header, *rows]) + '\n')
    return path


def make_config(directory: Path, region_filter: list[str] | None = None) -> NGConfig:
    """Build an NGConfig pointed at ``directory``.

    ``directory`` is absolute, so NGConfig's ``PROJECT_ROOT / input_path`` resolution leaves it
    untouched and the config validates against the temp directory rather than the repo inputs.
    """
    return NGConfig(input_path=directory, region_filter=region_filter)


@pytest.fixture
def basic_config(tmp_path: Path) -> NGConfig:
    """An NGConfig over a temp directory holding the two-domestic-region CSV, no filter."""
    write_region_csv(tmp_path, BASIC_ROWS)
    return make_config(tmp_path)


class TestLoadRegionDataHappyPath:
    """Parsing of a well-formed ng_region_data.csv."""

    def test_returns_master_list_and_both_subsets(self, basic_config: NGConfig) -> None:
        """The master list is partitioned into the domestic and international subsets."""
        result = load_region_data(basic_config)

        assert result['regions'] == ['canada', 'mountain', 'pacific']
        assert result['regions_domestic'] == ['mountain', 'pacific']
        assert result['regions_international'] == ['canada']
        # no filter, so every domestic region is analysed
        assert result['regions_analyze'] == result['regions_domestic']
        # the subsets partition the master list: together they cover it, and they do not overlap
        domestic, international = (
            set(result['regions_domestic']),
            set(result['regions_international']),
        )
        assert domestic | international == set(result['regions'])
        assert not domestic & international

    def test_labels_cover_every_region(self, basic_config: NGConfig) -> None:
        """Labels are returned for international regions too, not just the modelled ones."""
        result = load_region_data(basic_config)

        assert result['region_labels'] == {
            'mountain': 'Mountain (Rockies / Permian)',
            'pacific': 'Pacific',
            'canada': 'Canada',
        }

    def test_covered_areas_column_is_not_returned(self, basic_config: NGConfig) -> None:
        """covered_areas documents the CSV for a reader; it is not part of the return shape."""
        result = load_region_data(basic_config)

        assert set(result) == {
            'regions',
            'regions_domestic',
            'regions_analyze',
            'regions_international',
            'region_labels',
        }

    def test_logs_the_counts_it_loaded(
        self, basic_config: NGConfig, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A successful load reports what it found, the signal a healthy run is read by."""
        with caplog.at_level('INFO', logger='src.models.natural_gas.data'):
            load_region_data(basic_config)

        assert (
            'Regions loaded from CSV (3 total: 2 domestic (2 for analysis), 1 international)'
            in (caplog.text)
        )

    @pytest.mark.parametrize(
        'flag,expected_domestic',
        [
            ('True', True),
            ('true', True),
            ('TRUE', True),
            ('  True  ', True),
            ('-', False),
            ('False', False),
            ('0', False),
            ('yes', False),
        ],
        ids=['True', 'lower', 'upper', 'padded', 'dash', 'False', 'zero', 'yes'],
    )
    def test_domestic_flag_parsing(
        self, tmp_path: Path, flag: str, expected_domestic: bool
    ) -> None:
        """Only a case-insensitive 'true' counts, matching the electricity convention.

        'yes' and '0' are included deliberately: neither is truthy here, so a CSV hand-edited
        with either drops the region from the model rather than silently including it.
        """
        write_region_csv(
            tmp_path, [f'mountain,{flag},-,"AZ, CO",Mountain', 'pacific,True,-,"CA",Pacific']
        )
        result = load_region_data(make_config(tmp_path))

        assert ('mountain' in result['regions_domestic']) is expected_domestic
        # either way the region stays in the master list
        assert 'mountain' in result['regions']

    def test_whitespace_is_stripped_from_names_and_labels(self, tmp_path: Path) -> None:
        """A hand-edited file with padded cells still matches lookups elsewhere in the model."""
        write_region_csv(
            tmp_path,
            ['  mountain  ,  True  ,-,"AZ, CO",  Mountain  ', 'canada,-,True,Canada,Canada'],
        )
        result = load_region_data(make_config(tmp_path))

        assert result['regions_domestic'] == ['mountain']
        assert result['region_labels']['mountain'] == 'Mountain'

    def test_comment_lines_are_skipped(self, tmp_path: Path) -> None:
        """Provenance headers ('# source: ...') are dropped by the shared _csv reader."""
        path = tmp_path / 'ng_region_data.csv'
        path.write_text(
            '# source: EIA census divisions\n# vintage: AEO2025\n'
            + '\n'.join([HEADER, *BASIC_ROWS])
            + '\n'
        )
        result = load_region_data(make_config(tmp_path))

        assert result['regions_domestic'] == ['mountain', 'pacific']


class TestLoadRegionDataFilter:
    """Interaction with NGConfig.region_filter, the source of regions_analyze."""

    def test_filter_narrows_analyze_only(self, tmp_path: Path) -> None:
        """A filter moves regions_analyze; the master list and subsets are unaffected."""
        write_region_csv(tmp_path, BASIC_ROWS)
        result = load_region_data(make_config(tmp_path, region_filter=['pacific']))

        assert result['regions_analyze'] == ['pacific']
        assert result['regions_domestic'] == ['mountain', 'pacific']
        assert result['regions'] == ['canada', 'mountain', 'pacific']

    def test_subset_warns_that_results_are_not_comparable(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Dropping regions drops their production and trade, so it must not pass silently."""
        write_region_csv(tmp_path, BASIC_ROWS)
        with caplog.at_level('INFO', logger='src.models.natural_gas.data'):
            load_region_data(make_config(tmp_path, region_filter=['pacific']))

        assert 'Domestic region subset (1 of 2)' in caplog.text
        assert 'Dropped domestic regions' in caplog.text
        assert any(r.levelname == 'WARNING' for r in caplog.records)

    @pytest.mark.parametrize(
        'region_filter',
        [None, [], ['mountain', 'pacific']],
        ids=['none', 'empty', 'every domestic region'],
    )
    def test_full_coverage_filters_do_not_warn(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, region_filter: list[str] | None
    ) -> None:
        """No filter, an empty one, and one naming every region all mean 'run everything'."""
        write_region_csv(tmp_path, BASIC_ROWS)
        with caplog.at_level('INFO', logger='src.models.natural_gas.data'):
            result = load_region_data(make_config(tmp_path, region_filter=region_filter))

        assert result['regions_analyze'] == ['mountain', 'pacific']
        assert 'Domestic region subset' not in caplog.text

    def test_unknown_region_in_filter_raises(self, tmp_path: Path) -> None:
        """A typo'd filter fails here rather than later inside pyomo with an opaque index error.

        The offending name has to reach the message: naming only the good half of the filter
        would leave the reader hunting for which entry was wrong.
        """
        write_region_csv(tmp_path, BASIC_ROWS)
        config = make_config(tmp_path, region_filter=['pacific', 'atlantis'])

        with pytest.raises(ValueError, match=r"Unrecognized region filter\(s\): \['atlantis'\]"):
            load_region_data(config)

    def test_international_region_is_not_a_valid_filter(self, tmp_path: Path) -> None:
        """Canada is a trade partner, not a model region, so it is not a valid filter entry."""
        write_region_csv(tmp_path, BASIC_ROWS)

        with pytest.raises(ValueError, match='Unrecognized region filter'):
            load_region_data(make_config(tmp_path, region_filter=['canada']))


class TestLoadRegionDataRaises:
    """The no-fallback contract: every unusable input raises ValueError."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """An absent file is fatal here, unlike every other loader in data.py."""
        config = make_config(tmp_path)  # tmp_path exists but holds no CSV

        with pytest.raises(ValueError, match='no fallback'):
            load_region_data(config)

    def test_unreadable_file_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_csv() flattens 'absent' and 'present but broken' to None; both must raise.

        Mocked rather than provoked with a corrupt file: _csv swallows every read error, so
        forcing its return value is the only way to pin the malformed-file branch specifically.
        """
        write_region_csv(tmp_path, BASIC_ROWS)
        monkeypatch.setattr(ng_data, '_csv', lambda *args, **kwargs: None)

        with pytest.raises(ValueError, match='could not be read'):
            load_region_data(make_config(tmp_path))

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        """A zero-byte file has no header for pandas to parse, so _csv returns None."""
        (tmp_path / 'ng_region_data.csv').write_text('')

        with pytest.raises(ValueError, match='could not be read'):
            load_region_data(make_config(tmp_path))

    @pytest.mark.parametrize(
        'dropped', ['region', 'domestic', 'international', 'label'], ids=lambda c: f'no {c}'
    )
    def test_missing_required_column_raises(self, tmp_path: Path, dropped: str) -> None:
        """Every column the loader reads is required; covered_areas is the only optional one."""
        columns = HEADER.split(',')
        keep = [i for i, c in enumerate(columns) if c != dropped]
        header = ','.join(columns[i] for i in keep)
        # rebuild the rows without the dropped column; quoted covered_areas holds commas, so
        # split on the same csv reader the loader would use rather than on ','
        import csv as _csv_mod

        rows = [','.join(f'"{cells[i]}"' for i in keep) for cells in _csv_mod.reader(BASIC_ROWS)]
        write_region_csv(tmp_path, rows, header=header)

        with pytest.raises(ValueError, match='missing required column'):
            load_region_data(make_config(tmp_path))

    @pytest.mark.parametrize(
        'rows',
        [
            [],
            ['canada,-,True,Canada,Canada'],
            ['mountain,-,-,"AZ, CO",Mountain'],
        ],
        ids=['header only', 'international only', 'nothing flagged'],
    )
    def test_no_domestic_regions_raises(self, tmp_path: Path, rows: list[str]) -> None:
        """A file that declares no domestic region gives the model nothing to solve over."""
        write_region_csv(tmp_path, rows)

        with pytest.raises(ValueError, match='no domestic regions'):
            load_region_data(make_config(tmp_path))


class TestLoadRegionDataRepoInput:
    """The shipped input/natural_gas/ng_region_data.csv, not a synthetic fixture."""

    def test_repo_input_file(self) -> None:
        """Pin the nine census divisions plus Canada that the model is documented to run."""
        config = NGConfig(input_path=Path('input/natural_gas'))
        result = load_region_data(config)

        assert len(result['regions_domestic']) == 9
        assert result['regions_international'] == ['canada']
        assert result['regions_analyze'] == result['regions_domestic']
        assert 'west_south_central' in result['regions_domestic']
        assert result['region_labels']['west_south_central'] == 'West South Central (Gulf Coast)'
        # every region carries a non-empty label
        assert all(result['region_labels'].get(r) for r in result['regions'])

    def test_repo_input_file_resolves_relative_to_project_root(self) -> None:
        """NGConfig resolves a relative input_path, so the loader is CWD-independent."""
        config = NGConfig(input_path=Path('input/natural_gas'))

        assert config.input_path == PROJECT_ROOT / 'input/natural_gas'


# ---------------------------------------------------------------------------
# The no-fallback contract, extended to every loader
# ---------------------------------------------------------------------------

# (loader, filename it reads). Every loader in data.py that takes a data_dir; the two that take
# an NGConfig instead (load_region_data, load_sector_data) are covered by their own classes.
#
# load_storage_opex and load_qp_scalars both read ng_scalars.csv, so both appear against it.
DATA_DIR_LOADERS = [
    (ng_data.load_supply_cost_tiers, 'ng_supply_cost_tiers.csv'),
    (ng_data.load_supply_anchors, 'ng_supply_anchors.csv'),
    (ng_data.load_lng_import, 'ng_lng_import.csv'),
    (ng_data.load_lng_export, 'ng_lng_export.csv'),
    (ng_data.load_demand_elasticity, 'ng_demand_elasticity.csv'),
    (ng_data.load_base_demand, 'ng_base_demand.csv'),
    (ng_data.load_demand_growth, 'ng_demand_growth.csv'),
    (ng_data.load_pipeline_arcs, 'ng_pipeline_arcs.csv'),
    (ng_data.load_storage, 'ng_storage.csv'),
    (ng_data.load_storage_opex, 'ng_scalars.csv'),
    (ng_data.load_supply_curve_shape, 'ng_supply_curve_shape.csv'),
    (ng_data.load_tariff_curve_shape, 'ng_tariff_curve_shape.csv'),
    (ng_data.load_lng_demand_curve, 'ng_lng_demand_curve.csv'),
    (ng_data.load_gathering_charges, 'ng_gathering.csv'),
    (ng_data.load_qp_scalars, 'ng_scalars.csv'),
]

# Deliberately absent from the list above: load_pipe_loss and load_losses read OPTIONAL override
# files. Their values live in ng_scalars.csv, which is required, so an absent override file means
# "nothing departs from the scalar" rather than "the value is hiding in Python".
# See TestOptionalOverrideFiles.


class TestNoFallbackContract:
    """Every loader raises on an unusable input rather than substituting a built-in default.

    These loaders each used to hold a hardcoded fallback dict, so a missing or malformed file
    produced a warning and a plausible model. Six of the files were never shipped at all, which
    meant the fallbacks were the live values rather than emergency defaults. The values now live
    in ``input/natural_gas/`` and the fallbacks are gone; these tests are what keeps them gone.
    """

    @pytest.mark.parametrize(
        'loader,filename', DATA_DIR_LOADERS, ids=lambda a: getattr(a, '__name__', a)
    )
    def test_missing_file_raises(self, loader, filename: str, tmp_path: Path) -> None:
        """An absent input is fatal for every loader, not just load_region_data."""
        with pytest.raises(ValueError, match='no fallback|could not be read'):
            loader(data_dir=tmp_path)

    @pytest.mark.parametrize(
        'loader,filename', DATA_DIR_LOADERS, ids=lambda a: getattr(a, '__name__', a)
    )
    def test_unreadable_file_raises(
        self, loader, filename: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_csv() flattens 'absent' and 'present but broken' to None; both must raise.

        Mocked rather than provoked with a corrupt file: _csv swallows every read error, so
        forcing its return value is the only way to pin the malformed-file branch specifically.
        """
        monkeypatch.setattr(ng_data, '_csv', lambda *args, **kwargs: None)

        with pytest.raises(ValueError, match='no fallback|could not be read'):
            loader(data_dir=tmp_path)

    def test_no_loader_silently_returns_empty(self, tmp_path: Path) -> None:
        """The failure mode this class exists to prevent: an empty result read as success.

        A loader that returns {} on a missing file lets the model build with that parameter
        group absent, which is the silent-wrong-answer case rather than a crash.
        """
        for loader, _filename in DATA_DIR_LOADERS:
            with pytest.raises(ValueError):
                result = loader(data_dir=tmp_path)
                pytest.fail(f'{loader.__name__} returned {result!r} instead of raising')


class TestShippedScalars:
    """ng_scalars.csv must define every scalar load_qp_scalars requires."""

    def test_repo_scalars_are_complete(self) -> None:
        """A scalar dropped from the CSV now raises instead of reverting to a hidden default."""
        result = ng_data.load_qp_scalars(PROJECT_ROOT / 'input/natural_gas')

        assert set(result) == set(ng_data._REQUIRED_QP_SCALARS)
        assert all(isinstance(v, float) for v in result.values())

    def test_missing_scalar_raises(self, tmp_path: Path) -> None:
        """Naming the absent key is what makes the error actionable."""
        (tmp_path / 'ng_scalars.csv').write_text(
            'parameter,value,units,source\nstorage_opex,0.18,$/MMBtu,test\n'
        )

        with pytest.raises(ValueError, match='missing required scalar'):
            ng_data.load_qp_scalars(tmp_path)


class TestOptionalOverrideFiles:
    """The two loaders whose files may legitimately be absent.

    ``pipe_fuel_loss`` and the four loss scalars live in ng_scalars.csv, which IS
    required. ng_pipe_loss.csv and ng_losses.csv only override those scalars per arc / per
    region, so an absent file means "nothing differs" rather than "the value is hiding in
    Python". Neither ships in this repo.
    """

    def test_missing_file_returns_no_overrides(self, tmp_path: Path) -> None:
        """Absence is benign here and must NOT raise, unlike every other loader."""
        assert ng_data.load_pipe_loss(data_dir=tmp_path) == {}

    def test_malformed_file_still_raises(self, tmp_path: Path) -> None:
        """Optional to supply, but not optional to get right: a present file must parse."""
        (tmp_path / 'ng_pipe_loss.csv').write_text('origin,destination,loss_fraction\na,b,zzz\n')

        with pytest.raises(ValueError, match='Could not parse ng_pipe_loss.csv'):
            ng_data.load_pipe_loss(data_dir=tmp_path)

    def test_overrides_are_read_when_present(self, tmp_path: Path) -> None:
        """The override path has no coverage otherwise, since the repo ships no such file."""
        (tmp_path / 'ng_pipe_loss.csv').write_text(
            'origin,destination,loss_fraction\nmountain,pacific,0.02\n'
        )

        assert ng_data.load_pipe_loss(data_dir=tmp_path) == {('mountain', 'pacific'): 0.02}

    def test_repo_ships_no_override_file(self) -> None:
        """Pin the current state: no arc overrides the scalar.

        If someone adds ng_pipe_loss.csv, this fails and prompts a decision about whether the
        arcs it lists are meant to be exceptions or a full table.
        """
        assert ng_data.load_pipe_loss(PROJECT_ROOT / 'input/natural_gas') == {}

    def test_missing_losses_file_returns_no_overrides(self, tmp_path: Path) -> None:
        """Absence is benign: every region takes the four scalars from ng_scalars.csv."""
        assert ng_data.load_losses(data_dir=tmp_path) == {}

    def test_losses_partial_override_keeps_only_present_columns(self, tmp_path: Path) -> None:
        """Overriding is per column, so a one-column file must not imply the other three.

        ng_model resolves losses.get(region, {}).get(column, scalar), so returning a column
        the file never listed would silently pin it instead of letting the scalar apply.
        """
        (tmp_path / 'ng_losses.csv').write_text('region,plant_fuel_frac\nmountain,0.05\n')

        assert ng_data.load_losses(data_dir=tmp_path) == {'mountain': {'plant_fuel_frac': 0.05}}

    def test_losses_partial_override_keeps_only_listed_regions(self, tmp_path: Path) -> None:
        """One region in the file must not displace the other eight."""
        (tmp_path / 'ng_losses.csv').write_text(
            'region,storage_loss\nmountain,0.01\npacific,0.02\n'
        )
        result = ng_data.load_losses(data_dir=tmp_path)

        assert set(result) == {'mountain', 'pacific'}

    def test_losses_file_with_no_known_column_raises(self, tmp_path: Path) -> None:
        """A file that overrides nothing is a mistake, not a no-op: say so."""
        (tmp_path / 'ng_losses.csv').write_text('region,something_else\nmountain,0.01\n')

        with pytest.raises(ValueError, match='overrides nothing'):
            ng_data.load_losses(data_dir=tmp_path)

    def test_losses_malformed_file_still_raises(self, tmp_path: Path) -> None:
        """Optional to supply, not optional to get right."""
        (tmp_path / 'ng_losses.csv').write_text('region,storage_loss\nmountain,zzz\n')

        with pytest.raises(ValueError, match='Could not parse ng_losses.csv'):
            ng_data.load_losses(data_dir=tmp_path)

    def test_repo_ships_no_losses_override_file(self) -> None:
        """Pin the current state: no region departs from the scalar loss defaults."""
        assert ng_data.load_losses(PROJECT_ROOT / 'input/natural_gas') == {}


# ---------------------------------------------------------------------------
# Pure derivation helpers
# ---------------------------------------------------------------------------

# The shipped shape, from input/natural_gas/ng_supply_curve_shape.csv.
CRV_BELOW = [0.30, 0.15, 0.05]
CRV_ABOVE = [0.05, 0.15, 0.30]
ELAS = [0.8, 0.7, 0.5, 0.3, 0.2]


class TestSupplyQBase:
    """NGMM Eq 2 and 4: the quantity breakpoints of the elastic supply curve.

    Breakpoints 1-3 sit below the (Q0, P0) anchor and 4-6 above it, so no breakpoint equals Q0
    exactly -- the anchor sits between 3 and 4.
    """

    @pytest.mark.parametrize(
        'k,expected',
        [
            (1, 1000 * 0.70 * 0.85 * 0.95),  # all three downward factors
            (2, 1000 * 0.85 * 0.95),
            (3, 1000 * 0.95),  # just below the anchor
            (4, 1000 * 1.05),  # just above
            (5, 1000 * 1.05 * 1.15),
            (6, 1000 * 1.05 * 1.15 * 1.30),  # all three upward factors
        ],
    )
    def test_matches_hand_calculation(self, k: int, expected: float) -> None:
        """Pins the cumulative product per breakpoint against arithmetic done by hand."""
        assert ng_data.supply_qbase(1000.0, k, CRV_BELOW, CRV_ABOVE) == pytest.approx(expected)

    def test_strictly_increasing_in_k(self) -> None:
        """The curve must be monotonic, or quantities attach to the wrong prices."""
        values = [ng_data.supply_qbase(1000.0, k, CRV_BELOW, CRV_ABOVE) for k in range(1, 7)]

        assert values == sorted(values)
        assert len(set(values)) == 6

    def test_anchor_sits_between_breakpoints_3_and_4(self) -> None:
        """The two branches run their products in opposite directions.

        Reversing either one still yields a monotonic curve spanning a plausible range, so
        nothing downstream complains -- this is the assertion that would catch it.
        """
        q0 = 1000.0

        assert ng_data.supply_qbase(q0, 3, CRV_BELOW, CRV_ABOVE) < q0
        assert ng_data.supply_qbase(q0, 4, CRV_BELOW, CRV_ABOVE) > q0

    def test_scales_linearly_with_q0(self) -> None:
        """Q0 is a pure multiplier, so doubling it doubles every breakpoint."""
        single = [ng_data.supply_qbase(500.0, k, CRV_BELOW, CRV_ABOVE) for k in range(1, 7)]
        double = [ng_data.supply_qbase(1000.0, k, CRV_BELOW, CRV_ABOVE) for k in range(1, 7)]

        assert double == pytest.approx([2 * v for v in single])


class TestSupplyPBase:
    """NGMM Eq 3 and 5, in the elasticity-corrected form the model deliberately uses."""

    @pytest.mark.parametrize(
        'k,expected',
        [
            (1, 4.0 * (1 - 0.30 / 0.8) * (1 - 0.15 / 0.7) * (1 - 0.05 / 0.5)),
            (2, 4.0 * (1 - 0.15 / 0.7) * (1 - 0.05 / 0.5)),
            (3, 4.0 * (1 - 0.05 / 0.5)),
            (4, 4.0 * (1 + 0.05 / 0.5)),  # elas[2], not elas[0]
            (5, 4.0 * (1 + 0.05 / 0.5) * (1 + 0.15 / 0.3)),
            (6, 4.0 * (1 + 0.05 / 0.5) * (1 + 0.15 / 0.3) * (1 + 0.30 / 0.2)),
        ],
    )
    def test_matches_hand_calculation(self, k: int, expected: float) -> None:
        """Pins the elasticity indexing, which differs between the two branches.

        Below the anchor, elas[i] is read on the same index as crv_below[i]. Above it,
        elas[2 + i] continues into the upper half of the five-element vector.
        """
        assert ng_data.supply_pbase(4.0, k, CRV_BELOW, CRV_ABOVE, ELAS) == pytest.approx(expected)

    def test_strictly_increasing_in_k(self) -> None:
        """THE property this form exists for.

        The literal NGMM Eq 3/5 divides (1 +/- CRV) by an elasticity < 1, which is
        non-monotonic with NGMM's own default elasticities -- PBASE_2 > PBASE_3, the wrong
        direction. The model uses the elasticity-corrected form instead. If this test fails,
        someone has restored the published formula.
        """
        values = [ng_data.supply_pbase(4.0, k, CRV_BELOW, CRV_ABOVE, ELAS) for k in range(1, 7)]

        assert values == sorted(values)
        assert len(set(values)) == 6

    def test_curve_steepens_above_the_anchor(self) -> None:
        """Elasticities decline 0.8 -> 0.2, so supply gets progressively harder to expand."""
        vals = [ng_data.supply_pbase(4.0, k, CRV_BELOW, CRV_ABOVE, ELAS) for k in range(1, 7)]
        gaps = [b - a for a, b in pairwise(vals)]

        assert gaps[-1] > gaps[0]


class TestInterpLngExport:
    """Linear interpolation of the LNG export demand table, given at breakpoint years."""

    TABLE: ClassVar[dict[str, dict[int, float]]] = {
        'west_south_central': {2025: 4300.0, 2030: 5100.0, 2050: 7200.0}
    }

    def test_exact_breakpoint_year(self) -> None:
        """A year present in the table returns its value untouched."""
        assert ng_data.interp_lng_export(self.TABLE, 'west_south_central', 2030) == 5100.0

    def test_interpolates_between_breakpoints(self) -> None:
        """Midway between 2025 and 2030 is midway between 4300 and 5100."""
        got = ng_data.interp_lng_export(self.TABLE, 'west_south_central', 2027)

        assert got == pytest.approx(4300 + (2 / 5) * (5100 - 4300))

    @pytest.mark.parametrize(
        'year,expected', [(2000, 4300.0), (2099, 7200.0)], ids=['before', 'after']
    )
    def test_clamps_outside_the_table_range(self, year: int, expected: float) -> None:
        """Outside the breakpoints the series is flat, not extrapolated."""
        assert ng_data.interp_lng_export(self.TABLE, 'west_south_central', year) == expected

    def test_unknown_region_is_zero(self) -> None:
        """A region with no LNG export terminal exports nothing, rather than raising."""
        assert ng_data.interp_lng_export(self.TABLE, 'mountain', 2030) == 0.0


class TestSectorElasticityCoverage:
    """Every sector in ng_sector_data.csv must have an entry in ng_demand_elasticity.csv.

    The two files share no key, so a sector added to one and not the other would otherwise pass
    silently: ng_model reads the elasticity per sector, and an absent one used to read as 0.0,
    meaning perfectly inelastic demand. That is a modelling statement rather than a default, so
    it has to be made deliberately.
    """

    def test_repo_inputs_are_consistent(self) -> None:
        """The shipped pair covers all five sectors."""
        data_dir = PROJECT_ROOT / 'input/natural_gas'
        elasticity = ng_data.load_demand_elasticity(data_dir)
        config = NGConfig(input_path=Path('input/natural_gas'))

        assert all(sector in elasticity for sector in ng_data.load_sector_data(config))

    def test_uncovered_sector_raises_naming_it(self, tmp_path: Path) -> None:
        """Loading must fail at load time, naming the sector, not silently later."""
        import shutil

        from src.common.common_config import CommonConfig

        for csv_file in (PROJECT_ROOT / 'input/natural_gas').glob('*.csv'):
            shutil.copy(csv_file, tmp_path)
        rows = (tmp_path / 'ng_demand_elasticity.csv').read_text().splitlines()
        (tmp_path / 'ng_demand_elasticity.csv').write_text(
            '\n'.join(r for r in rows if not r.startswith('transportation')) + '\n'
        )

        common_config, remainder = CommonConfig.from_toml(
            PROJECT_ROOT / 'tests/natural_gas/basic_ng_config.toml'
        )
        ng_config = NGConfig(**remainder.pop('natural_gas'))
        ng_config.input_path = tmp_path

        with pytest.raises(ValueError, match=r"no entry for sector\(s\): \['transportation'\]"):
            ng_data.load_all(ng_config, common_config)
