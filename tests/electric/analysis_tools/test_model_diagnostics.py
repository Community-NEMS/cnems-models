"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/13/26

Tests for tests/model_diagnostics.py::breakdown_obj_terms
"""

import pytest

from analysis_tools.model_diagnostics import breakdown_obj_terms
from src.models.electricity.electricity_model import PowerModel
from tests.electric.mini_model import add_gas_ct

# (variable, coefficient, terms sharing it) for the mini model below, all weights 1, sorted by
# descending coefficient as breakdown_obj_terms prints them
_EXPECTED_ROWS = [
    ('unmet_load', 5.0e4, 16),  # the unmet load penalty, 2 regions x 2 years x 4 hours
    ('generation_total', 30.0, 16),  # Gas
    ('generation_total', 20.0, 16),  # GasCT
    ('capacity_total', 10.0, 8),  # FOM, Gas and GasCT
    ('capacity_total', 2.0, 4),  # FOM, Battery
    ('storage_inflow', 0.5, 32),  # half the Battery price, on inflow and outflow
    ('storage_level', 1.0e-11, 16),  # the storage level cost
]


@pytest.fixture
def diagnostics_model(mini_data, build_mini) -> PowerModel:
    """The mini model with GasCT and a Battery, so the objective has several coefficients."""
    add_gas_ct(mini_data)
    mini_data.add_tech('Battery', flags=['is_storage'])
    mini_data.add_capacity('Battery', 20.0)
    mini_data.add_operating_costs('Battery', generation_cost=1.0, fom_cost=2.0)
    mini_data.add_rows('storage_efficiency', {('Battery',): 0.85})
    mini_data.add_rows('storage_duration', {('Battery',): 4.0})
    sequencer, model = build_mini()
    sequencer.solve_model()
    return model


def test_breakdown_obj_terms(diagnostics_model, capsys):
    """breakdown_obj_terms prints a header row followed by (variable, index, coefficient) triplets.

    Triplets are sorted by descending coefficient magnitude, with duplicate coefficients collapsed
    into a single row plus an "... and x others" summary line.
    """
    breakdown_obj_terms(diagnostics_model)

    lines = capsys.readouterr().out.strip().splitlines()

    header = lines[0]
    assert 'Variable' in header
    assert 'Index' in header
    assert 'Coefficient' in header

    rows = []
    for line in lines[1:]:
        if line.startswith('  ...'):
            # "  ... and n others with same coefficient" adds n terms to the row above
            name, coefficient, count = rows[-1]
            rows[-1] = (name, coefficient, count + int(line.split()[2]))
        else:
            rows.append((line.split()[0], float(line.split()[-1]), 1))

    assert rows == [
        (name, pytest.approx(coefficient), count) for name, coefficient, count in _EXPECTED_ROWS
    ]


verbose = False


@pytest.mark.skipif(not verbose, reason='only run verbosely')
def test_obj_function_scaling(diagnostics_model):
    """Print to screen the objective coefficients."""
    breakdown_obj_terms(diagnostics_model)
