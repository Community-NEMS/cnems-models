"""Test `data_ingestor`` module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from upath import UPath

from src.models.electricity import data_ingestor


def _mock_datapackage() -> dict:
    return {
        'resources': [
            {
                'name': 'test1',
                'path': 'test1.csv',
                'schema': {
                    'fields': [
                        {'name': 'region', 'type': 'string'},
                        {'name': 'region_source', 'type': 'string'},
                        {'name': 'region_dest', 'type': 'string'},
                        {'name': 'year', 'type': 'integer'},
                        {'name': 'value', 'type': 'number'},
                    ]
                },
            },
            {
                'name': 'test2',
                'path': 'test2.csv',
                'schema': {
                    'fields': [
                        {'name': 'region', 'type': 'string'},
                        {'name': 'region_source', 'type': 'string'},
                        {'name': 'region_dest', 'type': 'string'},
                        {'name': 'year', 'type': 'integer'},
                        {'name': 'value', 'type': 'number'},
                    ]
                },
            },
        ]
    }


def _mock_urlopen(datapackage: dict) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(datapackage).encode('utf-8')

    urlopen_mock = MagicMock()
    urlopen_mock.return_value.__enter__.return_value = response
    return urlopen_mock


def test_load_dataframes_w_datapackage_applies_filters(tmpdir) -> None:
    """Test loading input tables with region/year filters."""
    tmpdir.join('test1.csv').write(
        'region,region_source,region_dest,year,value\n'
        'west,west,west,2024,1.0\n'
        'east,east,east,2024,2.0\n'
        'west,west,west,2025,3.0\n'
    )
    tmpdir.join('test2.csv').write(
        'region,region_source,region_dest,year,value\nwest,west,west,2024,99.0\n'
    )

    filters = data_ingestor.FilterPackage(
        region_filter=['west'],
        region_cols=['region'],
        year_filter=[2024],
        year_col=['year'],
    )

    with patch.object(data_ingestor, 'urlopen', _mock_urlopen(_mock_datapackage())):
        result = data_ingestor.load_dataframes_w_datapackage(
            base_path=UPath(str(tmpdir)),
            filters=filters,
        )

    assert list(result['test1'].to_dict('records')) == [
        {
            'region': 'west',
            'region_source': 'west',
            'region_dest': 'west',
            'year': 2024,
            'value': 1.0,
        },
    ]
    assert list(result['test2'].to_dict('records')) == [
        {
            'region': 'west',
            'region_source': 'west',
            'region_dest': 'west',
            'year': 2024,
            'value': 99.0,
        },
    ]


def test_load_dataframes_w_datapackage_applies_filters_multiple_region_cols(tmpdir) -> None:
    """Test loading input tables with multiple region columns in filters."""
    tmpdir.join('test1.csv').write(
        'region,region_source,region_dest,year,value\n'
        'west,west,west,2024,1.0\n'
        'west,east,west,2024,2.0\n'
        'west,west,west,2025,3.0\n'
    )
    tmpdir.join('test2.csv').write(
        'region,region_source,region_dest,year,value\nwest,west,west,2024,99.0\n'
    )

    filters = data_ingestor.FilterPackage(
        region_filter=['west'],
        region_cols=['region', 'region_source', 'region_dest'],
        year_filter=[2024],
        year_col=['year'],
    )

    with patch.object(data_ingestor, 'urlopen', _mock_urlopen(_mock_datapackage())):
        result = data_ingestor.load_dataframes_w_datapackage(
            base_path=UPath(str(tmpdir)),
            filters=filters,
        )

    assert list(result['test1'].to_dict('records')) == [
        {
            'region': 'west',
            'region_source': 'west',
            'region_dest': 'west',
            'year': 2024,
            'value': 1.0,
        },
    ]
    assert list(result['test2'].to_dict('records')) == [
        {
            'region': 'west',
            'region_source': 'west',
            'region_dest': 'west',
            'year': 2024,
            'value': 99.0,
        },
    ]


def test_load_dataframes_w_datapackage_filters_tables(tmpdir) -> None:
    """Test loading subset of input tables."""
    tmpdir.join('test1.csv').write(
        'region,region_source,region_dest,year,value\nwest,west,west,2024,1.0\n'
    )
    tmpdir.join('test2.csv').write(
        'region,region_source,region_dest,year,value\neast,east,east,2024,2.0\n'
    )

    with patch.object(data_ingestor, 'urlopen', _mock_urlopen(_mock_datapackage())):
        result = data_ingestor.load_dataframes_w_datapackage(
            base_path=UPath(str(tmpdir)),
            tables_to_load=['test1'],
        )

    assert 'test1' in result
    assert 'test2' not in result


@pytest.mark.parametrize(
    'region_filter',
    [None, {'west'}],
    ids=['unfiltered', 'dupe_in_filtered_region'],
)
def test_read_parameter_csv_rejects_duplicate_index(tmp_path: Path, region_filter) -> None:
    """A repeated index is an error, even in rows the filter would drop."""
    path = tmp_path / 'p.csv'
    path.write_text('region,year,value\nwest,2030,1.0\neast,2030,2.0\neast,2030,3.0\n')
    filters = data_ingestor.FilterPackage(region_filter=region_filter)
    with pytest.raises(ValueError, match=r"Duplicate index \('east', 2030\) on line 4"):
        data_ingestor.read_parameter_csv(path, ['region', 'year'], 'value', filters)


def test_read_property_csv_rejects_duplicate_index(tmp_path: Path) -> None:
    """A repeated index row in a property file is an error."""
    path = tmp_path / 'tech.csv'
    path.write_text('tech,is_vre\nSolar,True\nGas,False\nSolar,False\n')
    with pytest.raises(ValueError, match=r"Duplicate index \('Solar',\) on line 4"):
        data_ingestor.read_property_csv(path, ['tech', 'is_vre'], ['tech'])
