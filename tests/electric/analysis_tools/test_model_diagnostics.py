"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/13/26

Tests for tests/model_diagnostics.py::breakdown_obj_terms
"""

import pytest

from analysis_tools.model_diagnostics import breakdown_obj_terms

# variable names that should appear in the objective for the baseline test config
# (capacity_expansion / regional_exchange / ramping / reserves all disabled, so
# total_cost reduces to dispatch_cost + unmet_load_cost)
_EXPECTED_VAR_NAMES = {
    'generation_total',
    'storage_inflow',
    'storage_outflow',
    'storage_level',
    'unmet_load',
}


def test_breakdown_obj_terms(solved_model, capsys):
    """breakdown_obj_terms should print a header row followed by (variable, index,
    coefficient) triplets, sorted by descending coefficient magnitude, with duplicate
    coefficients collapsed into a single row plus an "... and x others" summary line.
    """
    _, _, elec_model = solved_model

    breakdown_obj_terms(elec_model)

    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()

    header = lines[0]
    assert 'Variable' in header
    assert 'Index' in header
    assert 'Coefficient' in header

    data_lines = [line for line in lines[1:] if not line.startswith('  ...')]
    summary_lines = [line for line in lines[1:] if line.startswith('  ...')]

    # at least a handful of term rows, and some collapsing of duplicate coefficients
    assert len(data_lines) > 5
    assert summary_lines

    for line in summary_lines:
        assert line.split()[-1] == 'coefficient'
        assert int(line.split()[-5]) > 0

    # inspect the first few data rows for plausible content and descending sort order
    coefficients = [float(line.split()[-1]) for line in data_lines[:5]]
    for line, coefficient in zip(data_lines[:5], coefficients):
        var_name = line.split()[0]
        assert var_name in _EXPECTED_VAR_NAMES
        assert coefficient >= 0

    assert coefficients == sorted(coefficients, reverse=True)


verbose = False


@pytest.mark.skipif(not verbose, reason='only run verbosely')
def test_obj_function_scaling(solved_model):
    """Print to screen the objective coefficients."""
    _, _, elec_model = solved_model
    breakdown_obj_terms(elec_model)
