import json
from unittest.mock import MagicMock, patch

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
                        {'name': 'source_region', 'type': 'string'},
                        {'name': 'destination_region', 'type': 'string'},
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
                        {'name': 'source_region', 'type': 'string'},
                        {'name': 'destination_region', 'type': 'string'},
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
    tmpdir.join('test1.csv').write(
        'region,source_region,destination_region,year,value\n'
        'west,west,west,2024,1.0\n'
        'east,east,east,2024,2.0\n'
        'west,west,west,2025,3.0\n'
    )
    tmpdir.join('test2.csv').write(
        'region,source_region,destination_region,year,value\nwest,west,west,2024,99.0\n'
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
            'source_region': 'west',
            'destination_region': 'west',
            'year': 2024,
            'value': 1.0,
        },
    ]
    assert list(result['test2'].to_dict('records')) == [
        {
            'region': 'west',
            'source_region': 'west',
            'destination_region': 'west',
            'year': 2024,
            'value': 99.0,
        },
    ]


def test_load_dataframes_w_datapackage_applies_filters_multiple_region_cols(tmpdir) -> None:
    tmpdir.join('test1.csv').write(
        'region,source_region,destination_region,year,value\n'
        'west,west,west,2024,1.0\n'
        'west,east,west,2024,2.0\n'
        'west,west,west,2025,3.0\n'
    )
    tmpdir.join('test2.csv').write(
        'region,source_region,destination_region,year,value\nwest,west,west,2024,99.0\n'
    )

    filters = data_ingestor.FilterPackage(
        region_filter=['west'],
        region_cols=['region', 'source_region', 'destination_region'],
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
            'source_region': 'west',
            'destination_region': 'west',
            'year': 2024,
            'value': 1.0,
        },
    ]
    assert list(result['test2'].to_dict('records')) == [
        {
            'region': 'west',
            'source_region': 'west',
            'destination_region': 'west',
            'year': 2024,
            'value': 99.0,
        },
    ]


def test_load_dataframes_w_datapackage_filters_tables(tmpdir) -> None:
    tmpdir.join('test1.csv').write(
        'region,source_region,destination_region,year,value\nwest,west,west,2024,1.0\n'
    )
    tmpdir.join('test2.csv').write(
        'region,source_region,destination_region,year,value\neast,east,east,2024,2.0\n'
    )

    with patch.object(data_ingestor, 'urlopen', _mock_urlopen(_mock_datapackage())):
        result = data_ingestor.load_dataframes_w_datapackage(
            base_path=UPath(str(tmpdir)),
            tables_to_load=['test1'],
        )

    assert 'test1' in result
    assert 'test2' not in result
