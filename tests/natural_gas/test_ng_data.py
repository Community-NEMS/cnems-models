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

from pathlib import Path

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
