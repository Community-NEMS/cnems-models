"""Tests for ``analysis_tools.transmission_network``."""

import networkx as nx
import pytest

from analysis_tools.transmission_network import (
    build_transmission_network,
    node_degrees,
    plot_network,
    summarize_network,
)


@pytest.fixture(scope='module')
def graph() -> nx.Graph:
    """Transmission network built from the repo's default input data."""
    return build_transmission_network()


def test_build_transmission_network_no_self_loops(graph):
    """No region should be wired to itself in the built network."""
    assert nx.number_of_selfloops(graph) == 0


def test_build_transmission_network_connected(graph):
    """Every region is reachable from every other region."""
    assert nx.is_connected(graph)


def test_node_degrees_matches_graph(graph):
    """node_degrees reproduces networkx's own degree view, with no isolated nodes."""
    degrees = node_degrees(graph)
    assert degrees.to_dict() == dict(graph.degree())
    assert degrees.min() >= 1


def test_summarize_network_runs(graph, caplog):
    """summarize_network logs its summary, including the connectivity verdict."""
    degrees = node_degrees(graph)
    with caplog.at_level('INFO'):
        summarize_network(graph, degrees)
    assert 'fully connected' in caplog.text


def test_plot_network_writes_file(graph, tmp_path):
    """plot_network writes a non-empty file to the requested save path."""
    degrees = node_degrees(graph)
    out_file = tmp_path / 'network.html'
    plot_network(graph, degrees, save_path=out_file)
    assert out_file.exists()
    assert out_file.stat().st_size > 0
