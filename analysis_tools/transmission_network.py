"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/13/26

Builds the electricity model's inter-regional transmission network from TranLimit.csv and
reports node degree using networkx.
"""

import logging
from pathlib import Path

import networkx as nx
import pandas as pd
import plotly.graph_objects as go

from definitions import PROJECT_ROOT

logger = logging.getLogger(__name__)

DEFAULT_TRAN_LIMIT_CSV = PROJECT_ROOT / 'input' / 'electricity' / 'cem_inputs' / 'TranLimit.csv'

# Multiplier applied to each edge's average TranLimit value to get its plotted line width (in
# pixels). TranLimit in the sample data ranges roughly 0.1-10, so a factor of 1.0 gives a
# visible range of hairline-to-thick lines without any single edge dominating; tune if a
# different TranLimit scale makes lines too thin/thick.
LINE_WEIGHT_SCALING_FACTOR = 1.0

# Approximate (lat, lon) centroid per region ID, hand-estimated from the region boundaries in
# EIA's AEO Electricity Market Module region map (https://www.eia.gov/outlooks/aeo/pdf/nerc_map.pdf).
# The region IDs used in this repo's input data match that map's Region ID column exactly (e.g.
# 1=TRE/Texas, 21=CANO/Northern California). Coordinates are visual estimates, not authoritative
# centroids.
REGION_COORDS: dict[int, tuple[float, float]] = {
    1: (31.0, -99.5),  # TRE - Texas
    2: (28.5, -81.5),  # FRCC - Florida
    3: (45.0, -93.5),  # MISW - Upper Mississippi Valley
    4: (39.0, -89.5),  # MISC - Middle Mississippi Valley
    5: (44.5, -85.0),  # MISE - Michigan
    6: (32.5, -91.5),  # MISS - Mississippi Delta
    7: (44.0, -70.5),  # ISNE - New England
    8: (40.7, -73.5),  # NYCW - Metropolitan New York
    9: (43.0, -75.5),  # NYUP - Upstate New York
    10: (40.0, -75.3),  # PJME - Mid-Atlantic
    11: (39.5, -81.5),  # PJMW - Ohio Valley
    12: (41.85, -87.65),  # PJMC - Metropolitan Chicago
    13: (37.5, -78.5),  # PJMD - Virginia
    14: (35.0, -80.0),  # SRCA - Carolinas
    15: (32.5, -84.0),  # SRSE - Southeast
    16: (35.8, -86.5),  # SRCE - Tennessee Valley
    17: (35.5, -97.5),  # SPPS - Southern Great Plains
    18: (38.5, -98.5),  # SPPC - Central Great Plains
    19: (45.0, -100.0),  # SPPN - Northern Great Plains
    20: (34.0, -111.5),  # SRSG - Southwest
    21: (39.5, -121.5),  # CANO - Northern California
    22: (34.2, -118.0),  # CASO - Southern California
    23: (45.5, -117.5),  # NWPP - Northwest
    24: (40.5, -106.0),  # RMRG - Rockies
    25: (39.5, -115.5),  # BASN - Great Basin
}


def build_transmission_network(csv_path: Path = DEFAULT_TRAN_LIMIT_CSV) -> nx.Graph:
    """Build an undirected graph of the transmission network from TranLimit.csv.

    Parameters
    ----------
    csv_path : Path
        Path to a TranLimit.csv-formatted file with `source_region`, `destination_region`,
        and `value` columns.

    Returns
    -------
    nx.Graph
        Undirected graph with one node per region and one edge per unique
        (source_region, destination_region) pair (self-loops excluded). Each edge has a
        `tran_limit` attribute: the TranLimit value averaged over both directions and all
        season/year rows for that region pair.
    """
    df = pd.read_csv(csv_path)
    df = df[df['source_region'] != df['destination_region']].copy()
    df['region_a'] = df[['source_region', 'destination_region']].min(axis=1)
    df['region_b'] = df[['source_region', 'destination_region']].max(axis=1)
    edge_limits = df.groupby(['region_a', 'region_b'])['value'].mean()

    graph = nx.Graph()
    for (region_a, region_b), tran_limit in edge_limits.items():
        graph.add_edge(region_a, region_b, tran_limit=tran_limit)
    return graph


def node_degrees(graph: nx.Graph) -> pd.Series:
    """Compute the degree of each node in the network.

    Parameters
    ----------
    graph : nx.Graph
        Transmission network graph, e.g. from `build_transmission_network`.

    Returns
    -------
    pd.Series
        Degree indexed by region, sorted by region number.
    """
    return pd.Series(dict(graph.degree()), name='degree').sort_index()


def summarize_network(graph: nx.Graph, degrees: pd.Series) -> None:
    """Log summary statistics and a connectivity check for the network.

    Parameters
    ----------
    graph : nx.Graph
        Transmission network graph.
    degrees : pd.Series
        Node degrees, e.g. from `node_degrees`.
    """
    logger.info(
        'Transmission network: %d nodes, %d edges', graph.number_of_nodes(), graph.number_of_edges()
    )
    logger.info(
        'Degree stats: min=%d, max=%d, mean=%.2f',
        degrees.min(),
        degrees.max(),
        degrees.mean(),
    )
    isolated = list(nx.isolates(graph))
    if isolated:
        logger.warning('Isolated regions (degree 0): %s', isolated)
    if nx.is_connected(graph):
        logger.info('Network is fully connected (single component).')
    else:
        n_components = nx.number_connected_components(graph)
        logger.warning('Network is NOT fully connected: %d components.', n_components)


def plot_network(
    graph: nx.Graph,
    degrees: pd.Series,
    save_path: Path | None = None,
) -> None:
    """Draw the network on a US map, with node size/color scaled by degree and line thickness
    scaled by TranLimit.

    Nodes are placed at approximate region centroids from `REGION_COORDS` and drawn over an
    outline map of the US with state boundaries. Each edge's line width is
    `LINE_WEIGHT_SCALING_FACTOR * tran_limit`, where `tran_limit` is the edge's averaged
    TranLimit attribute from `build_transmission_network`.

    Parameters
    ----------
    graph : nx.Graph
        Transmission network graph, with a `tran_limit` attribute on each edge.
    degrees : pd.Series
        Node degrees, e.g. from `node_degrees`.
    save_path : Path, optional
        If given, save the figure as a self-contained interactive HTML file here instead of
        opening it in a browser tab.
    """
    missing = sorted(set(graph.nodes) - set(REGION_COORDS))
    if missing:
        raise ValueError(f'No coordinates defined in REGION_COORDS for regions: {missing}')

    node_ids = list(graph.nodes)
    node_lat = [REGION_COORDS[n][0] for n in node_ids]
    node_lon = [REGION_COORDS[n][1] for n in node_ids]
    node_degree = [degrees[n] for n in node_ids]

    fig = go.Figure()
    for a, b, tran_limit in graph.edges.data('tran_limit'):
        lat_a, lon_a = REGION_COORDS[a]
        lat_b, lon_b = REGION_COORDS[b]
        fig.add_trace(
            go.Scattergeo(
                lat=[lat_a, lat_b],
                lon=[lon_a, lon_b],
                mode='lines',
                line={
                    'width': LINE_WEIGHT_SCALING_FACTOR * tran_limit,
                    'color': 'rgba(138,51,36, 0.6)',
                },
                hovertext=f'Region {a} - Region {b}: TranLimit {tran_limit:.2f}',
                hoverinfo='text',
                showlegend=False,
            )
        )
    fig.add_trace(
        go.Scattergeo(
            lat=node_lat,
            lon=node_lon,
            mode='markers+text',
            text=[str(n) for n in node_ids],
            textposition='top center',
            hovertext=[f'Region {n}: degree {d}' for n, d in zip(node_ids, node_degree)],
            hoverinfo='text',
            marker={
                'size': [15 + 4 * d for d in node_degree],
                'color': node_degree,
                'colorscale': 'Viridis',
                'colorbar': {'title': 'Degree'},
                'line': {'width': 1, 'color': 'black'},
            },
        )
    )
    fig.update_geos(
        scope='usa',
        showland=True,
        landcolor='rgb(235, 235, 235)',
        showsubunits=True,
        subunitcolor='rgb(150, 150, 150)',
        showlakes=True,
        lakecolor='white',
    )
    fig.update_layout(
        title='Electricity Model Transmission Network (node degree)',
        showlegend=False,
        margin={'l': 0, 'r': 0, 't': 40, 'b': 0},
    )

    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(save_path)
        logger.info('Saved network map to %s', save_path)
    else:
        fig.show()


def main() -> None:
    """Build the transmission network, log a summary, and plot node degree."""
    logging.basicConfig(level=logging.INFO)
    graph = build_transmission_network()
    degrees = node_degrees(graph)
    summarize_network(graph, degrees)
    plot_network(
        graph, degrees, save_path=PROJECT_ROOT / 'output' / 'transmission_network_degree.html'
    )


if __name__ == '__main__':
    main()
