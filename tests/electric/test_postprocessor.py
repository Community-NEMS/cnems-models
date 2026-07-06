"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/5/26

Tests for the model-native variable extraction in src.models.electricity.postprocessor
"""

import pandas as pd
import pyomo.environ as pyo
import pytest

from src.models.electricity.postprocessor import (
    export_variables_to_csv,
    extract_all_variables,
    variable_to_dataframe,
    get_known_column_names,
    core_variable_indices,
)


def test_scalar_var():
    """a scalar (unindexed) Var yields a single row with just a value column"""
    m = pyo.ConcreteModel()
    m.x = pyo.Var(initialize=5.0)
    m.x.fix(5.0)

    df = variable_to_dataframe(m.x)

    assert list(df.columns) == ['value']
    assert len(df) == 1
    assert df['value'].iloc[0] == pytest.approx(5.0)


def test_dedupe_repeated_set_names():
    """crossing a Set against itself must not produce colliding column names"""
    m = pyo.ConcreteModel()
    m.A = pyo.Set(initialize=['a', 'b'])
    m.v = pyo.Var(m.A, m.A, initialize=0)
    for idx in m.v:
        m.v[idx].fix(1.0)

    df = variable_to_dataframe(m.v)

    assert list(df.columns) == ['A_1', 'A_2', 'value']
    assert len(df) == 4


def test_unsolved_var_gives_none_value():
    """an uninitialized Var entry should surface as None rather than raising"""
    m = pyo.ConcreteModel()
    m.A = pyo.Set(initialize=['a', 'b'])
    m.v = pyo.Var(m.A)

    df = variable_to_dataframe(m.v)

    assert len(df) == 2
    assert df['value'].isna().all()


def test_empty_var_returns_empty_dataframe_with_columns():
    """a Var with zero elements still returns the correct column headers"""
    m = pyo.ConcreteModel()
    m.A = pyo.Set(initialize=[])
    m.v = pyo.Var(m.A)

    df = variable_to_dataframe(m.v)

    assert df.empty
    assert list(df.columns) == ['idx_0', 'value']


def test_get_known_column_names():
    """column names are known for all variables in the PowerModel"""
    m = pyo.ConcreteModel()
    m.generation_total = pyo.Var()
    assert get_known_column_names(m.generation_total) == core_variable_indices.get(
        'generation_total'
    ), 'Lookup of column names for known variables failed'


@pytest.mark.parametrize(
    'var_name,expected_columns',
    [
        ('unmet_load', ['region', 'year', 'hour', 'value']),
        ('generation_total', ['tech', 'year', 'region', 'step', 'hour', 'value']),
    ],
    ids=['crossed-Set var', 'raw-tuple-list var'],
)
def test_column_names_on_real_model(solved_model, var_name, expected_columns):
    """column derivation on the real PowerModel: crossed Sets decompose, tuple lists fall back"""
    _, _, elec_model = solved_model
    var = getattr(elec_model, var_name)

    df = variable_to_dataframe(var)

    assert list(df.columns) == expected_columns
    assert len(df) == len(list(var))
    assert not df['value'].isna().any()


def test_extract_all_variables_covers_every_var(solved_model):
    """with core_only=False, extract_all_variables returns exactly the Vars present on the
    model, no more, no less"""
    _, _, elec_model = solved_model

    dfs = extract_all_variables(elec_model, core_only=False)

    expected_names = {v.local_name for v in elec_model.component_objects(pyo.Var, active=True)}
    assert set(dfs.keys()) == expected_names


def test_extract_all_variables_core_only_excludes_non_core_vars(solved_model):
    """the core_only default must skip active Vars that aren't in core_variable_indices"""
    _, _, elec_model = solved_model

    dfs = extract_all_variables(elec_model)

    all_names = {v.local_name for v in elec_model.component_objects(pyo.Var, active=True)}
    assert 'var_elec_request' in all_names
    assert set(dfs.keys()) == all_names & set(core_variable_indices)


def test_export_variables_to_csv_writes_files(solved_model, tmp_path):
    """CSV export writes one file per variable and round-trips row counts"""
    _, _, elec_model = solved_model

    dfs = export_variables_to_csv(elec_model, output_dir=tmp_path)

    for name, df in dfs.items():
        csv_path = tmp_path / f'{name}.csv'
        assert csv_path.exists()
        reloaded = pd.read_csv(csv_path)
        assert len(reloaded) == len(df)
