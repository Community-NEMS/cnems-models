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
    return build_transmission_network()


def test_build_transmission_network_no_self_loops(graph):
    assert nx.number_of_selfloops(graph) == 0


def test_build_transmission_network_connected(graph):
    assert nx.is_connected(graph)


def test_node_degrees_matches_graph(graph):
    degrees = node_degrees(graph)
    assert degrees.to_dict() == dict(graph.degree())
    assert degrees.min() >= 1


def test_summarize_network_runs(graph, caplog):
    degrees = node_degrees(graph)
    with caplog.at_level('INFO'):
        summarize_network(graph, degrees)
    assert 'fully connected' in caplog.text


def test_plot_network_writes_file(graph, tmp_path):
    degrees = node_degrees(graph)
    out_file = tmp_path / 'network.html'
    plot_network(graph, degrees, save_path=out_file)
    assert out_file.exists()
    assert out_file.stat().st_size > 0
