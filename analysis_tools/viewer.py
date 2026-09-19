"""Dash viewer for browsing and comparing electricity model run outputs."""

####################################################################################################################
# Setup

import base64
import logging
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from dash import Dash, Input, Output, dcc, html

from definitions import PROJECT_ROOT

logger = logging.getLogger(__name__)

# setting up directories
dir_output = PROJECT_ROOT / 'output'  # Path(__file__).parent

# header background image, embedded as a data URI so it renders without Dash asset serving
_header_bg_svg = PROJECT_ROOT / 'app_images' / 'energy_tech_background_pattern.svg'
_header_bg_data_uri = 'data:image/svg+xml;base64,' + base64.b64encode(
    _header_bg_svg.read_bytes()
).decode('ascii')

####################################################################################################################
# Theme: dark surfaces, IBM Plex Sans for UI text and IBM Plex Mono for tick labels / values.
# Page chrome lives in assets/viewer.css (same tokens); this block themes the plotly figures.

THEME: dict[str, str] = {
    'page': '#0f1115',
    'panel': '#171a1f',
    'panel_2': '#1c2026',
    'hairline': 'rgba(255,255,255,0.10)',
    'ink': '#e8eaed',
    'ink_2': '#aab0b8',
    'muted': '#7a8290',
    'grid': '#262b33',
    'axis': '#343a44',
}
FONT_UI = "'IBM Plex Sans', Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
FONT_MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
FONTS_URL = (
    'https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600'
    '&family=IBM+Plex+Mono:wght@400;500&display=swap'
)
# categorical colorway for charts keyed by region rather than tech (validated dark-surface set)
REGION_COLORWAY = [
    '#3987e5',
    '#d95926',
    '#199e70',
    '#c98500',
    '#d55181',
    '#008300',
    '#9085e9',
    '#e66767',
]
CHART_HEIGHT = 340
LEGEND_ROW_PX = 20

_axis_style = {
    'gridcolor': THEME['grid'],
    'gridwidth': 1,
    'zerolinecolor': THEME['axis'],
    'zerolinewidth': 1,
    'linecolor': THEME['axis'],
    'tickcolor': THEME['axis'],
    'ticks': 'outside',
    'ticklen': 4,
    'tickfont': {'family': FONT_MONO, 'size': 11, 'color': THEME['muted']},
    'title': {'font': {'family': FONT_UI, 'size': 11, 'color': THEME['ink_2']}},
    'automargin': True,
}
pio.templates['cnems_dark'] = go.layout.Template(
    layout={
        'paper_bgcolor': THEME['panel'],
        'plot_bgcolor': THEME['panel'],
        'font': {'family': FONT_UI, 'size': 12, 'color': THEME['ink_2']},
        'colorway': REGION_COLORWAY,
        'xaxis': dict(_axis_style, showline=True),
        'yaxis': dict(_axis_style, showline=False),
        # legend hugs the top edge of the figure (container coords) so it sits above the
        # facet titles, which plotly express anchors to the top of the plot area
        'legend': {
            'orientation': 'h',
            'yref': 'container',
            'yanchor': 'top',
            'y': 1,
            'xanchor': 'left',
            'x': 0,
            'font': {'size': 11, 'color': THEME['ink_2']},
            'bgcolor': 'rgba(0,0,0,0)',
            'itemsizing': 'constant',
        },
        'margin': {'l': 56, 'r': 12, 't': 72, 'b': 44},
        'hovermode': 'x unified',
        'hoverlabel': {
            'bgcolor': THEME['panel_2'],
            'bordercolor': THEME['hairline'],
            'font': {'family': FONT_MONO, 'size': 11, 'color': THEME['ink']},
        },
        'bargap': 0.35,
        'annotationdefaults': {'font': {'size': 12, 'color': THEME['ink']}},
    },
    data={
        'bar': [go.Bar(marker={'line': {'color': THEME['panel'], 'width': 1}})],
        'scatter': [go.Scatter(line={'width': 2})],
    },
)
pio.templates.default = 'cnems_dark'


def pretty(name: str) -> str:
    """Turn a snake_case column name into a sentence-case axis label."""
    return name.replace('_', ' ').strip().capitalize()


def selected_runs(run: list[str] | None) -> list[str]:
    """Runs to facet on: the dropdown selection, or every run when nothing is selected."""
    return list(run) if run else list(s_runs)


def finish_figure(fig: go.Figure, y_title: str) -> go.Figure:
    """Apply the shared chart chrome to a faceted plotly-express figure.

    Strips the ``run=`` prefix from facet titles, puts the y title on the first facet only,
    separates stacked-area bands with a surface-coloured hairline gap, reserves enough top
    margin for the (wrapping) legend above the facet titles, and sizes the figure to fill
    its card.
    """
    fig.for_each_annotation(lambda a: a.update(text=a.text.split('=', 1)[-1]))
    fig.for_each_xaxis(lambda ax: ax.update(title_text=pretty(ax.title.text or '')))
    fig.update_yaxes(title_text='')
    fig.layout.yaxis.title.text = y_title
    # plotly fills an area with its line colour unless fillcolor is set, so pin the tech colour
    # to the fill, then draw the band outline in the surface colour: a gap between bands that
    # also stays invisible in the legend keys
    fig.for_each_trace(
        lambda t: (
            t.update(fillcolor=t.line.color, line={'width': 1, 'color': THEME['panel']})
            if getattr(t, 'stackgroup', None)
            else None
        )
    )
    # unified hover: one row per series as "name  value", without px's label=/run= prefixes
    fig.update_traces(hovertemplate='%{y:,.3f}<extra></extra>')
    # legend rows sit in the top margin above the facet titles; assume ~6 entries per row
    n_entries = len({t.name for t in fig.data if t.showlegend is not False})
    legend_rows = max(1, -(-n_entries // 6))
    fig.update_layout(
        height=CHART_HEIGHT,
        autosize=True,
        legend_title_text='',
        margin_t=28 + LEGEND_ROW_PX * legend_rows,
    )
    return fig


# add a new index using a mapping dataframe and return a dataframe with the input index
def mapping(df: pd.DataFrame, mapdf: pd.DataFrame, col: str, indexes: list[str]) -> pd.DataFrame:
    """Merge ``mapdf`` onto ``df`` by ``col`` and return only ``indexes``, dropping empty rows.

    ``col`` is cast to ``str`` on both sides first: a run's ``tech_data.csv`` may carry sub-tech
    ids such as ``10_seasonal``, so it reads as ``str`` while output CSVs holding only plain ids
    read as ``int64``, and pandas refuses to merge the two.
    """
    df = df.assign(**{col: df[col].astype(str)})
    mapdf = mapdf.assign(**{col: mapdf[col].astype(str)})
    df = pd.merge(df, mapdf, on=col, how='left')
    df = df[indexes]
    df = df.dropna(how='any', axis=0)

    return df


# read the .csv while appending the dataframe to a list
def read_run_csv(csv, run, df_list):
    """Read ``csv``, tag it with the ``run`` name, append it to ``df_list``, and return the list."""
    df = pd.read_csv(csv)
    df['run'] = run
    df_list.append(df)

    return df_list


# get all the electricity run names from the output file
os.chdir(Path(dir_output))
all_runs = []
for root, dirs, _files in os.walk(dir_output):
    if 'electricity' in dirs:
        all_runs.append(Path(root, 'electricity').relative_to(Path.cwd()))
all_runs = [item for item in all_runs if 'test' not in str(item)]

# check to see if there are any electrcity outputs to view
try:
    if len(all_runs) == 0:
        raise ValueError
except ValueError:
    print('there are no electricity outputs to review, try running the model')


# empty dataframes for the variarables output
df_generation = []
df_capacitybuilds = []
df_capacityretire = []
df_capacitytotal = []
df_storagelevel = []
df_storageinflow = []
df_storageoutflow = []
df_trade = []
df_tradecan = []
df_unmetload = []
df_techdata = []

# a loop for reading each .csv of each run folder and appending them into the dataframe
# and adding the a column for their run name
for i in range(len(all_runs)):
    run_output = Path(dir_output, all_runs[i])

    # switches to the variables folder
    run_variables = Path(run_output, 'variables')
    os.chdir(run_variables)

    # grabs the run name (scenario folder name, stripping the trailing 'electricity' segment)
    runname = str(all_runs[i].parent)

    # the try and except is for when file is missing when happens if the model is turned off
    try:
        df_generation = read_run_csv('generation_total.csv', runname, df_generation)
    except FileNotFoundError:
        pass

    try:
        df_capacitybuilds = read_run_csv('capacity_builds.csv', runname, df_capacitybuilds)
    except FileNotFoundError:
        pass

    try:
        df_capacityretire = read_run_csv('capacity_retirements.csv', runname, df_capacityretire)
    except FileNotFoundError:
        pass

    try:
        df_capacitytotal = read_run_csv('capacity_total.csv', runname, df_capacitytotal)
    except FileNotFoundError:
        pass

    try:
        df_storagelevel = read_run_csv('storage_level.csv', runname, df_storagelevel)
    except FileNotFoundError:
        pass

    try:
        df_storageinflow = read_run_csv('storage_inflow.csv', runname, df_storageinflow)
    except FileNotFoundError:
        pass

    try:
        df_storageoutflow = read_run_csv('storage_outflow.csv', runname, df_storageoutflow)
    except FileNotFoundError:
        pass

    try:
        df_trade = read_run_csv('trade_interregional.csv', runname, df_trade)
    except FileNotFoundError:
        pass

    try:
        df_tradecan = read_run_csv('trade_international.csv', runname, df_tradecan)
    except FileNotFoundError:
        pass

    try:
        df_unmetload = read_run_csv('unmet_load.csv', runname, df_unmetload)
    except FileNotFoundError:
        pass

    # tech descriptors (id, label, hex color) exported by the run's postprocessor
    try:
        df_techdata = read_run_csv(Path(run_output, 'tech_data.csv'), runname, df_techdata)
    except FileNotFoundError:
        pass

# concat all the runs into one table, there a try statements here due to whether the
# dataframe has values in them
try:
    df_generation = pd.concat(df_generation)
except ValueError:
    print('generation_total dataframe is empty.')
try:
    df_capacitybuilds = pd.concat(df_capacitybuilds)
except ValueError:
    print('Capacity build dataframe is empty.')
try:
    df_capacityretire = pd.concat(df_capacityretire)
except ValueError:
    print('Capacity retirement dataframe is empty.')
try:
    df_capacitytotal = pd.concat(df_capacitytotal)
except ValueError:
    print('Capacity total dataframe is empty.')
try:
    df_storagelevel = pd.concat(df_storagelevel)
except ValueError:
    print('Storage level dataframe is empty.')
try:
    df_storageinflow = pd.concat(df_storageinflow)
except ValueError:
    print('Storage inflow dataframe is empty.')
try:
    df_storageoutflow = pd.concat(df_storageoutflow)
except ValueError:
    print('Storage outflow dataframe is empty.')
try:
    df_trade = pd.concat(df_trade)
except ValueError:
    print('Trade dataframe is empty.')
try:
    df_tradecan = pd.concat(df_tradecan)
except ValueError:
    print('Canada trade dataframe is empty.')
try:
    df_unmetload = pd.concat(df_unmetload)
except ValueError:
    print('Unmet load dataframe is empty.')

# merge the tech descriptors (tech -> label, color) discovered across all runs.  Runs built
# from different input sets contribute different tech ids; the first run to describe a tech wins.
os.chdir(dir_output)
try:
    df_color = pd.concat(df_techdata)
    df_color = df_color.drop(columns='run').drop_duplicates(subset='tech', keep='first')
except ValueError:
    print('No tech_data.csv found in any run; techs will be unlabeled and uncolored.')
    df_color = pd.DataFrame(columns=['tech', 'label', 'abbreviation', 'color'])

# label -> hex color dictionary for plotly; first-seen wins, disagreements are logged
colorsetting: dict[str, str] = {}
for label, color in zip(df_color['label'], df_color['color'], strict=True):
    if label in colorsetting and colorsetting[label] != color:
        logger.warning(
            "Label '%s' has conflicting colors %s and %s across runs; keeping %s",
            label,
            colorsetting[label],
            color,
            colorsetting[label],
        )
        continue
    colorsetting.setdefault(label, color)

# sum the steps in the generation table
try:
    df_generation = df_generation[['run', 'tech', 'region', 'year', 'hour', 'generation_total']]
    df_generation = (
        df_generation.groupby(['run', 'tech', 'region', 'year', 'hour'])
        .generation_total.sum()
        .reset_index()
    )
    df_generation = mapping(
        df_generation,
        df_color,
        'tech',
        ['run', 'label', 'region', 'year', 'hour', 'generation_total'],
    )
except TypeError:
    print('generation_total dataframe is empty.')

# sum the steps in the storage tables
try:
    df_storagelevel = df_storagelevel[['run', 'tech', 'region', 'year', 'hour', 'storage_level']]
    df_storagelevel = (
        df_storagelevel.groupby(['run', 'tech', 'region', 'year', 'hour'])
        .storage_level.sum()
        .reset_index()
    )
    df_storagelevel = mapping(
        df_storagelevel,
        df_color,
        'tech',
        ['run', 'label', 'region', 'year', 'hour', 'storage_level'],
    )
except TypeError:
    print('Storage level dataframe is empty.')

try:
    df_storageinflow = df_storageinflow[['run', 'tech', 'region', 'year', 'hour', 'storage_inflow']]
    df_storageinflow = (
        df_storageinflow.groupby(['run', 'tech', 'region', 'year', 'hour'])
        .storage_inflow.sum()
        .reset_index()
    )
    df_storageinflow = mapping(
        df_storageinflow,
        df_color,
        'tech',
        ['run', 'label', 'region', 'year', 'hour', 'storage_inflow'],
    )
    df_storageinflow['Storage_flow'] = df_storageinflow['storage_inflow'] * -1
except TypeError:
    print('Storage inflow dataframe is empty.')

try:
    df_storageoutflow = df_storageoutflow[
        ['run', 'tech', 'region', 'year', 'hour', 'storage_outflow']
    ]
    df_storageoutflow = (
        df_storageoutflow.groupby(['run', 'tech', 'region', 'year', 'hour'])
        .storage_outflow.sum()
        .reset_index()
    )
    df_storageoutflow = mapping(
        df_storageoutflow,
        df_color,
        'tech',
        ['run', 'label', 'region', 'year', 'hour', 'storage_outflow'],
    )
    df_storageoutflow['Storage_flow'] = df_storageoutflow['storage_outflow']
except TypeError:
    print('Storage outflow dataframe is empty.')

df_storagecharge = pd.concat([df_storageinflow, df_storageoutflow])
df_storagecharge = df_storagecharge[['run', 'label', 'region', 'year', 'hour', 'Storage_flow']]
df_storagecharge = (
    df_storagecharge.groupby(['run', 'label', 'region', 'year', 'hour'])
    .Storage_flow.sum()
    .reset_index()
)

# sum the steps in capacity tables
try:
    df_capacitybuilds = df_capacitybuilds[['run', 'tech', 'region', 'year', 'capacity_builds']]
    df_capacitybuilds = (
        df_capacitybuilds.groupby(['run', 'tech', 'region', 'year'])
        .capacity_builds.sum()
        .reset_index()
    )
    df_capacitybuilds = mapping(
        df_capacitybuilds,
        df_color,
        'tech',
        ['run', 'label', 'region', 'year', 'capacity_builds'],
    )
except TypeError:
    print('Capacity build dataframe is empty.')

try:
    df_capacityretire = df_capacityretire[['run', 'tech', 'region', 'year', 'capacity_retirements']]
    df_capacityretire = (
        df_capacityretire.groupby(['run', 'tech', 'region', 'year'])
        .capacity_retirements.sum()
        .reset_index()
    )
    df_capacityretire = mapping(
        df_capacityretire,
        df_color,
        'tech',
        ['run', 'label', 'region', 'year', 'capacity_retirements'],
    )
except TypeError:
    print('Capacity retirement dataframe is empty.')

# do the max, then sum the steps
try:
    df_capacitytotal = df_capacitytotal[['run', 'tech', 'region', 'year', 'step', 'capacity_total']]
    df_capacitytotal = (
        df_capacitytotal.groupby(['run', 'tech', 'region', 'year', 'step'])
        .capacity_total.max()
        .reset_index()
    )
    df_capacitytotal = df_capacitytotal[['run', 'tech', 'region', 'year', 'capacity_total']]
    df_capacitytotal = (
        df_capacitytotal.groupby(['run', 'tech', 'region', 'year'])
        .capacity_total.sum()
        .reset_index()
    )
    df_capacitytotal = mapping(
        df_capacitytotal, df_color, 'tech', ['run', 'label', 'region', 'year', 'capacity_total']
    )
except TypeError:
    print('Capacity total dataframe is empty.')

# sum th steps in the trade to Canada
try:
    df_tradecan = df_tradecan[
        ['run', 'region_domestic', 'region_international', 'year', 'hour', 'trade_international']
    ]
    df_tradecan = (
        df_tradecan.groupby(['run', 'region_domestic', 'region_international', 'year', 'hour'])
        .trade_international.sum()
        .reset_index()
    )
except TypeError:
    print('Canada trade dataframe is empty.')

# create unique list of indexes
# regions may mix int ids (input/electricity) and str ids (input/electricity_light) across runs
s_regions = sorted(pd.unique(df_generation['region']), key=str)
s_technologies = pd.unique(df_capacitytotal['label'])
# storage techs plot differently from generators (level / charge-discharge rather than output),
# so each gets its own tab.  The split follows the T_stor membership flag each run exports with
# its tech_data.csv; outputs predating that column fall back to whatever the storage files hold.
if 'T_stor' in df_color.columns:
    _storage_labels = set(df_color.loc[df_color['T_stor'].fillna(False).astype(bool), 'label'])
else:
    _storage_labels = set(df_storagelevel['label']) if len(df_storagelevel) else set()
s_storage_techs = [tech for tech in s_technologies if tech in _storage_labels]
s_generation_techs = [tech for tech in s_technologies if tech not in _storage_labels]
s_years = pd.unique(df_generation['year'])
s_years.sort()
# s_canregions = pd.unique(df_tradecan['region_international'])
# s_canregions.sort(key=lambda x: str(x))
s_runs = sorted(pd.unique(df_generation['run']))

# change directory back to the scripts folder (This is for the batch file to work.)

app = Dash(__name__, external_stylesheets=[FONTS_URL], title='C-NEMS Electricity Viewer')


def filter_block(label: str, control) -> html.Div:
    """A labelled filter control for the top bar or a tab's control rail."""
    return html.Div([html.Label(label, className='filter-label'), control], className='filter')


def chart_card(title: str, graph_id: str) -> html.Div:
    """A titled card holding one responsive, toolbar-free graph."""
    return html.Div(
        [
            html.H2(title, className='card-title'),
            # responsive=True lets the plot follow the card's width but drops the figure's own
            # height, so the container must carry it or the card collapses onto its neighbour
            dcc.Graph(
                id=graph_id,
                className='chart',
                responsive=True,
                style={'height': f'{CHART_HEIGHT}px'},
                config={'displayModeBar': False},
            ),
        ],
        className='card',
    )


def tech_options(techs: list[str]) -> list[dict]:
    """Checklist options for ``techs``, each with its colour swatch."""
    return [
        {
            'label': html.Span(
                [
                    html.Span(
                        className='swatch',
                        style={'background': colorsetting.get(tech, THEME['muted'])},
                    ),
                    html.Span(tech),
                ],
                className='check-label',
            ),
            'value': tech,
        }
        for tech in techs
    ]


def tech_checklist(checklist_id: str, techs: list[str]) -> dcc.Checklist:
    """A compact checklist of ``techs`` with colour swatches."""
    return dcc.Checklist(
        id=checklist_id,
        options=tech_options(techs),
        className='check-list',
        labelClassName='check-item',
        inputClassName='check-input',
    )


def tech_dropdown(dropdown_id: str, techs: list[str]) -> dcc.Dropdown:
    """Single-select tech dropdown defaulting to the first of ``techs``."""
    return dcc.Dropdown(
        id=dropdown_id,
        options=[{'label': tech, 'value': tech} for tech in techs],
        value=techs[0] if techs else None,
        clearable=False,
        className='dd',
    )


def year_dropdown(dropdown_id: str) -> dcc.Dropdown:
    """Single-select year dropdown defaulting to the first modelled year."""
    return dcc.Dropdown(
        id=dropdown_id,
        options=[{'label': str(int(year)), 'value': year} for year in s_years],
        value=s_years[0] if len(s_years) else None,
        clearable=False,
        className='dd',
    )


def tab(label: str, value: str, controls: list, charts: list) -> dcc.Tab:
    """A tab whose body is a narrow control rail beside a column of chart cards."""
    return dcc.Tab(
        label=label,
        value=value,
        className='tab',
        selected_className='tab--active',
        children=html.Div(
            [html.Aside(controls, className='controls'), html.Section(charts, className='charts')],
            className='tab-body',
        ),
    )


app.layout = html.Div(
    [
        html.Header(
            [
                html.Div(
                    [
                        html.H1('Electricity Model Viewer'),
                        html.Span('C-NEMS · run comparison', className='brand-sub'),
                    ],
                    className='brand',
                ),
                html.Div(
                    [
                        filter_block(
                            'Runs',
                            dcc.Dropdown(
                                id='run',
                                options=[{'label': run, 'value': run} for run in s_runs],
                                multi=True,
                                placeholder='All runs',
                                className='dd',
                            ),
                        ),
                        filter_block(
                            'Region',
                            dcc.Dropdown(
                                id='region',
                                options=[{'label': str(r), 'value': r} for r in s_regions],
                                multi=True,
                                placeholder='All regions',
                                className='dd',
                            ),
                        ),
                    ],
                    className='global-filters',
                ),
            ],
            className='topbar',
            style={
                'backgroundImage': (
                    'linear-gradient(90deg, rgba(23,26,31,0.94), rgba(23,26,31,0.99)), '
                    f'url({_header_bg_data_uri})'
                )
            },
        ),
        dcc.Tabs(
            id='tabs',
            value='generation',
            className='tabs',
            parent_className='tabs-parent',
            content_className='tabs-content',
            children=[
                tab(
                    'Generation',
                    'generation',
                    controls=[
                        filter_block('Year', year_dropdown('genyear')),
                        filter_block('Technologies', tech_checklist('gentech', s_generation_techs)),
                    ],
                    charts=[
                        chart_card('Generation by technology', 'gen_graph_area'),
                        chart_card('Unmet load', 'unmet_graph_area'),
                        chart_card('Unmet load by region', 'unmet_graph_line'),
                    ],
                ),
                tab(
                    'Technology',
                    'technology',
                    controls=[
                        filter_block('Year', year_dropdown('techyear')),
                        filter_block('Technology', tech_dropdown('gentech2', s_generation_techs)),
                    ],
                    charts=[chart_card('Generation by region', 'gen_graph_line')],
                ),
                tab(
                    'Storage',
                    'storage',
                    controls=[
                        filter_block('Year', year_dropdown('storyear')),
                        filter_block('Technologies', tech_checklist('stortech', s_storage_techs)),
                        filter_block(
                            'Single technology', tech_dropdown('stortech2', s_storage_techs)
                        ),
                    ],
                    charts=[
                        chart_card('Storage level by technology', 'storage_level_graph_area'),
                        chart_card('Storage charge / discharge', 'storage_flow_graph_area'),
                        chart_card('Storage level by region', 'storage_level_graph_line'),
                        chart_card(
                            'Storage charge / discharge by region', 'storage_flow_graph_line'
                        ),
                    ],
                ),
                tab(
                    'Capacity',
                    'capacity',
                    controls=[
                        filter_block(
                            'Technologies', tech_checklist('captech', list(s_technologies))
                        )
                    ],
                    charts=[
                        chart_card('Total capacity', 'cap_graph_bar'),
                        chart_card('Capacity builds', 'cap_build_graph_bar'),
                        chart_card('Capacity retirements', 'cap_retire_graph_bar'),
                    ],
                ),
                tab(
                    'Trade',
                    'trade',
                    controls=[filter_block('Year', year_dropdown('trdyear'))],
                    charts=[
                        chart_card('Interregional trade', 'trade_graph_area'),
                        chart_card('International trade', 'tradecan_graph_area'),
                    ],
                ),
            ],
        ),
    ],
    className='app',
)

# each callback and its update function correspond to the graph id for each chart to be
# updated by the filter


@app.callback(
    Output('gen_graph_area', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
    Input('gentech', 'value'),
)
def update_gen_area_figure(region, genyear, run, gentech):
    """Build the stacked-area generation-by-tech figure for the selected regions/year/runs."""
    filtered_df_gen = df_generation[(df_generation.year == genyear)]

    if region:
        filtered_df_gen = filtered_df_gen[filtered_df_gen['region'].isin(region)]
        filtered_df_gen = filtered_df_gen[['run', 'label', 'year', 'hour', 'generation_total']]
        filtered_df_gen = (
            filtered_df_gen.groupby(['run', 'label', 'year', 'hour'])
            .generation_total.sum()
            .reset_index()
        )
    else:
        filtered_df_gen = filtered_df_gen[['run', 'label', 'year', 'hour', 'generation_total']]
        filtered_df_gen = (
            filtered_df_gen.groupby(['run', 'label', 'year', 'hour'])
            .generation_total.sum()
            .reset_index()
        )

    if gentech:
        filtered_df_gen = filtered_df_gen[filtered_df_gen['label'].isin(gentech)]

    if run:
        filtered_df_gen = filtered_df_gen[filtered_df_gen['run'].isin(run)]

    fig_gen = px.area(
        filtered_df_gen,
        x='hour',
        y='generation_total',
        color='label',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_gen, pretty('generation_total'))


@app.callback(
    Output('storage_level_graph_area', 'figure'),
    Input('region', 'value'),
    Input('storyear', 'value'),
    Input('run', 'value'),
    Input('stortech', 'value'),
)
def update_storage_level_area_figure(region, storyear, run, stortech):
    """Build the stacked-area storage-level figure for the selected regions/year/runs/techs."""
    filtered_df_storagelevel = df_storagelevel[(df_storagelevel.year == storyear)]

    if region:
        filtered_df_storagelevel = filtered_df_storagelevel[
            filtered_df_storagelevel['region'].isin(region)
        ]
        filtered_df_storagelevel = filtered_df_storagelevel[
            ['run', 'label', 'year', 'hour', 'storage_level']
        ]
        filtered_df_storagelevel = (
            filtered_df_storagelevel.groupby(['run', 'label', 'year', 'hour'])
            .storage_level.sum()
            .reset_index()
        )
    else:
        filtered_df_storagelevel = filtered_df_storagelevel[
            ['run', 'label', 'year', 'hour', 'storage_level']
        ]
        filtered_df_storagelevel = (
            filtered_df_storagelevel.groupby(['run', 'label', 'year', 'hour'])
            .storage_level.sum()
            .reset_index()
        )

    if run:
        filtered_df_storagelevel = filtered_df_storagelevel[
            filtered_df_storagelevel['run'].isin(run)
        ]

    if stortech:
        filtered_df_storagelevel = filtered_df_storagelevel[
            filtered_df_storagelevel['label'].isin(stortech)
        ]

    fig_storagelevel = px.area(
        filtered_df_storagelevel,
        x='hour',
        y='storage_level',
        color='label',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_storagelevel, pretty('storage_level'))


@app.callback(
    Output('storage_flow_graph_area', 'figure'),
    Input('region', 'value'),
    Input('storyear', 'value'),
    Input('run', 'value'),
    Input('stortech', 'value'),
)
def update_storage_flow_area_figure(region, storyear, run, stortech):
    """Build the stacked-area storage charge/discharge figure for the selected regions/techs."""
    filtered_df_storagecharge = df_storagecharge[(df_storagecharge.year == storyear)]

    if region:
        filtered_df_storagecharge = filtered_df_storagecharge[
            filtered_df_storagecharge['region'].isin(region)
        ]
        filtered_df_storagecharge = filtered_df_storagecharge[
            ['run', 'label', 'year', 'hour', 'Storage_flow']
        ]
        filtered_df_storagecharge = (
            filtered_df_storagecharge.groupby(['run', 'label', 'year', 'hour'])
            .Storage_flow.sum()
            .reset_index()
        )
    else:
        filtered_df_storagecharge = filtered_df_storagecharge[
            ['run', 'label', 'year', 'hour', 'Storage_flow']
        ]
        filtered_df_storagecharge = (
            filtered_df_storagecharge.groupby(['run', 'label', 'year', 'hour'])
            .Storage_flow.sum()
            .reset_index()
        )

    if run:
        filtered_df_storagecharge = filtered_df_storagecharge[
            filtered_df_storagecharge['run'].isin(run)
        ]

    if stortech:
        filtered_df_storagecharge = filtered_df_storagecharge[
            filtered_df_storagecharge['label'].isin(stortech)
        ]

    fig_storagecharge = px.area(
        filtered_df_storagecharge,
        x='hour',
        y='Storage_flow',
        color='label',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_storagecharge, pretty('Storage_flow'))


@app.callback(
    Output('unmet_graph_area', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
)
def update_unmet_area_figure(region, genyear, run):
    """Build the stacked-area unmet-load figure for the selected regions/year/runs."""
    filtered_df_unmetload = df_unmetload[(df_unmetload.year == genyear)]

    if region:
        filtered_df_unmetload = filtered_df_unmetload[filtered_df_unmetload['region'].isin(region)]
        filtered_df_unmetload = filtered_df_unmetload[['run', 'year', 'hour', 'unmet_load']]
        filtered_df_unmetload = (
            filtered_df_unmetload.groupby(['run', 'year', 'hour']).unmet_load.sum().reset_index()
        )
    else:
        filtered_df_unmetload = filtered_df_unmetload[['run', 'year', 'hour', 'unmet_load']]
        filtered_df_unmetload = (
            filtered_df_unmetload.groupby(['run', 'year', 'hour']).unmet_load.sum().reset_index()
        )

    if run:
        filtered_df_unmetload = filtered_df_unmetload[filtered_df_unmetload['run'].isin(run)]

    fig_unmetload = px.area(
        filtered_df_unmetload,
        x='hour',
        y='unmet_load',
        facet_col='run',
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_unmetload, pretty('unmet_load'))


@app.callback(
    Output('gen_graph_line', 'figure'),
    Input('region', 'value'),
    Input('techyear', 'value'),
    Input('run', 'value'),
    Input('gentech2', 'value'),
)
def update_gen_line_figure(region, genyear, run, gentech2):
    """Build the line generation figure for a single tech across the selected regions/year/runs."""
    filtered_df_gen = df_generation[
        (df_generation.year == genyear) & (df_generation.label == gentech2)
    ]

    if region:
        filtered_df_gen = filtered_df_gen[filtered_df_gen['region'].isin(region)]

    if run:
        filtered_df_gen = filtered_df_gen[filtered_df_gen['run'].isin(run)]

    fig_gen = px.line(
        filtered_df_gen,
        x='hour',
        y='generation_total',
        color='region',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_gen, pretty('generation_total'))


@app.callback(
    Output('storage_level_graph_line', 'figure'),
    Input('region', 'value'),
    Input('storyear', 'value'),
    Input('run', 'value'),
    Input('stortech2', 'value'),
)
def update_storage_level_line_figure(region, storyear, run, stortech2):
    """Build the line storage-level figure for one tech across the selected regions/year/runs."""
    filtered_df_storagelevel = df_storagelevel[
        (df_storagelevel.year == storyear) & (df_storagelevel.label == stortech2)
    ]

    if region:
        filtered_df_storagelevel = filtered_df_storagelevel[
            filtered_df_storagelevel['region'].isin(region)
        ]

    if run:
        filtered_df_storagelevel = filtered_df_storagelevel[
            filtered_df_storagelevel['run'].isin(run)
        ]

    fig_storagelevel = px.line(
        filtered_df_storagelevel,
        x='hour',
        y='storage_level',
        color='region',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_storagelevel, pretty('storage_level'))


@app.callback(
    Output('storage_flow_graph_line', 'figure'),
    Input('region', 'value'),
    Input('storyear', 'value'),
    Input('run', 'value'),
    Input('stortech2', 'value'),
)
def update_storage_flow_line_figure(region, storyear, run, stortech2):
    """Build the line storage flow figure for one tech across the selected regions/year/runs."""
    filtered_df_storagecharge = df_storagecharge[
        (df_storagecharge.year == storyear) & (df_storagecharge.label == stortech2)
    ]

    if region:
        filtered_df_storagecharge = filtered_df_storagecharge[
            filtered_df_storagecharge['region'].isin(region)
        ]

    if run:
        filtered_df_storagecharge = filtered_df_storagecharge[
            filtered_df_storagecharge['run'].isin(run)
        ]

    fig_storagecharge = px.line(
        filtered_df_storagecharge,
        x='hour',
        y='Storage_flow',
        color='region',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_storagecharge, pretty('Storage_flow'))


@app.callback(
    Output('unmet_graph_line', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
)
def update_unmet_line_figure(region, genyear, run):
    """Build the line unmet-load figure for the selected regions/year/runs."""
    filtered_df_unmetload = df_unmetload[(df_unmetload.year == genyear)]

    if region:
        filtered_df_unmetload = filtered_df_unmetload[filtered_df_unmetload['region'].isin(region)]

    if run:
        filtered_df_unmetload = filtered_df_unmetload[filtered_df_unmetload['run'].isin(run)]

    fig_unmetload = px.line(
        filtered_df_unmetload,
        x='hour',
        y='unmet_load',
        color='region',
        facet_col='run',
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_unmetload, pretty('unmet_load'))


@app.callback(
    Output('cap_graph_bar', 'figure'),
    Input('region', 'value'),
    Input('run', 'value'),
    Input('captech', 'value'),
)
def update_cap_bar_figure(region, run, captech):
    """Build the bar total-capacity-by-tech figure for the selected regions/techs/runs."""
    filtered_df_capacitytotal = df_capacitytotal

    if region:
        filtered_df_capacitytotal = df_capacitytotal[df_capacitytotal['region'].isin(region)]
        filtered_df_capacitytotal = filtered_df_capacitytotal[
            ['run', 'label', 'year', 'capacity_total']
        ]
        filtered_df_capacitytotal = (
            filtered_df_capacitytotal.groupby(['run', 'label', 'year'])
            .capacity_total.sum()
            .reset_index()
        )
    else:
        filtered_df_capacitytotal = filtered_df_capacitytotal[
            ['run', 'label', 'year', 'capacity_total']
        ]
        filtered_df_capacitytotal = (
            filtered_df_capacitytotal.groupby(['run', 'label', 'year'])
            .capacity_total.sum()
            .reset_index()
        )

    if captech:
        filtered_df_capacitytotal = filtered_df_capacitytotal[
            filtered_df_capacitytotal['label'].isin(captech)
        ]

    if run:
        filtered_df_capacitytotal = filtered_df_capacitytotal[
            filtered_df_capacitytotal['run'].isin(run)
        ]

    fig_capacitytotal = px.bar(
        filtered_df_capacitytotal,
        x='year',
        y='capacity_total',
        color='label',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_capacitytotal, pretty('capacity_total'))


@app.callback(
    Output('cap_build_graph_bar', 'figure'),
    Input('region', 'value'),
    Input('run', 'value'),
    Input('captech', 'value'),
)
def update_cap_build_bar_figure(region, run, captech):
    """Build the bar capacity-builds-by-tech figure for the selected regions/techs/runs."""
    filtered_df_capacitybuilds = df_capacitybuilds

    if region:
        filtered_df_capacitybuilds = df_capacitybuilds[df_capacitybuilds['region'].isin(region)]
        filtered_df_capacitybuilds = filtered_df_capacitybuilds[
            ['run', 'label', 'year', 'capacity_builds']
        ]
        filtered_df_capacitybuilds = (
            filtered_df_capacitybuilds.groupby(['run', 'label', 'year'])
            .capacity_builds.sum()
            .reset_index()
        )
    else:
        filtered_df_capacitybuilds = filtered_df_capacitybuilds[
            ['run', 'label', 'year', 'capacity_builds']
        ]
        filtered_df_capacitybuilds = (
            filtered_df_capacitybuilds.groupby(['run', 'label', 'year'])
            .capacity_builds.sum()
            .reset_index()
        )

    if captech:
        filtered_df_capacitybuilds = filtered_df_capacitybuilds[
            filtered_df_capacitybuilds['label'].isin(captech)
        ]

    if run:
        filtered_df_capacitybuilds = filtered_df_capacitybuilds[
            filtered_df_capacitybuilds['run'].isin(run)
        ]

    fig_capacitybuilds = px.bar(
        filtered_df_capacitybuilds,
        x='year',
        y='capacity_builds',
        color='label',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_capacitybuilds, pretty('capacity_builds'))


@app.callback(
    Output('cap_retire_graph_bar', 'figure'),
    Input('region', 'value'),
    Input('run', 'value'),
    Input('captech', 'value'),
)
def update_cap_retire_bar_figure(region, run, captech):
    """Build the bar capacity-retirements-by-tech figure for the selected regions/techs/runs."""
    filtered_df_capacityretire = df_capacityretire

    if region:
        filtered_df_capacityretire = df_capacityretire[df_capacityretire['region'].isin(region)]
        filtered_df_capacityretire = filtered_df_capacityretire[
            ['run', 'label', 'year', 'capacity_retirements']
        ]
        filtered_df_capacityretire = (
            filtered_df_capacityretire.groupby(['run', 'label', 'year'])
            .capacity_retirements.sum()
            .reset_index()
        )
    else:
        filtered_df_capacityretire = filtered_df_capacityretire[
            ['run', 'label', 'year', 'capacity_retirements']
        ]
        filtered_df_capacityretire = (
            filtered_df_capacityretire.groupby(['run', 'label', 'year'])
            .capacity_retirements.sum()
            .reset_index()
        )

    if captech:
        filtered_df_capacityretire = filtered_df_capacityretire[
            filtered_df_capacityretire['label'].isin(captech)
        ]

    if run:
        filtered_df_capacityretire = filtered_df_capacityretire[
            filtered_df_capacityretire['run'].isin(run)
        ]

    fig_capacityretire = px.bar(
        filtered_df_capacityretire,
        x='year',
        y='capacity_retirements',
        color='label',
        facet_col='run',
        color_discrete_map=colorsetting,
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_capacityretire, pretty('capacity_retirements'))


@app.callback(
    Output('trade_graph_area', 'figure'),
    Input('region', 'value'),
    Input('trdyear', 'value'),
    Input('run', 'value'),
)
def update_trade_area_figure(region, trdyear, run):
    """Build the stacked-area interregional trade figure for the selected regions/year/runs."""
    filtered_df_trade = df_trade[(df_trade.year == trdyear)]

    if region:
        filtered_df_trade = filtered_df_trade[filtered_df_trade['region_source'].isin(region)]
        filtered_df_trade = filtered_df_trade[
            ['run', 'region_destination', 'year', 'hour', 'trade_interregional']
        ]
        filtered_df_trade = (
            filtered_df_trade.groupby(['run', 'region_destination', 'year', 'hour'])
            .trade_interregional.sum()
            .reset_index()
        )
    else:
        filtered_df_trade = filtered_df_trade[
            ['run', 'region_destination', 'year', 'hour', 'trade_interregional']
        ]
        filtered_df_trade = (
            filtered_df_trade.groupby(['run', 'region_destination', 'year', 'hour'])
            .trade_interregional.sum()
            .reset_index()
        )

    if run:
        filtered_df_trade = filtered_df_trade[filtered_df_trade['run'].isin(run)]

    fig_trade = px.area(
        filtered_df_trade,
        x='hour',
        y='trade_interregional',
        color='region_destination',
        facet_col='run',
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_trade, pretty('trade_interregional'))


@app.callback(
    Output('tradecan_graph_area', 'figure'),
    Input('region', 'value'),
    Input('trdyear', 'value'),
    Input('run', 'value'),
)
def update_tradecan_area_figure(region, trdyear, run):
    """Build the stacked-area international trade figure for the selected regions/year/runs."""
    filtered_df_tradecan = df_tradecan[(df_tradecan.year == trdyear)]

    if region:
        filtered_df_tradecan = filtered_df_tradecan[
            filtered_df_tradecan['region_domestic'].isin(region)
        ]
        filtered_df_tradecan = filtered_df_tradecan[
            ['run', 'region_international', 'year', 'hour', 'trade_international']
        ]
        filtered_df_tradecan = (
            filtered_df_tradecan.groupby(['run', 'region_international', 'year', 'hour'])
            .trade_international.sum()
            .reset_index()
        )
    else:
        filtered_df_tradecan = filtered_df_tradecan[
            ['run', 'region_international', 'year', 'hour', 'trade_international']
        ]
        filtered_df_tradecan = (
            filtered_df_tradecan.groupby(['run', 'region_international', 'year', 'hour'])
            .trade_international.sum()
            .reset_index()
        )

    if run:
        filtered_df_tradecan = filtered_df_tradecan[filtered_df_tradecan['run'].isin(run)]

    fig_tradecan = px.area(
        filtered_df_tradecan,
        x='hour',
        y='trade_international',
        color='region_international',
        facet_col='run',
        category_orders={'run': selected_runs(run)},
    )
    return finish_figure(fig_tradecan, pretty('trade_international'))


# when running, ctrl+click on the http://127.0.0.1:8050/ which opens a broswer
app.run(debug=True)
