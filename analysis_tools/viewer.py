"""Dash viewer for browsing and comparing electricity model run outputs."""

####################################################################################################################
# Setup

import base64
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, dcc, html

from definitions import PROJECT_ROOT

# setting up directories
dir_output = PROJECT_ROOT / 'output'  # Path(__file__).parent

# header background image, embedded as a data URI so it renders without Dash asset serving
_header_bg_svg = PROJECT_ROOT / 'app_images' / 'energy_tech_background_pattern.svg'
_header_bg_data_uri = 'data:image/svg+xml;base64,' + base64.b64encode(
    _header_bg_svg.read_bytes()
).decode('ascii')


# add a new index using a mapping dataframe and return a dataframe with the input index
def mapping(df, mapdf, col, indexes):
    """Merge ``mapdf`` onto ``df`` by ``col`` and return only ``indexes``, dropping empty rows."""
    df = pd.merge(df, mapdf, on=col, how='outer')
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

# a loop for reading each .csv of each run folder and appending them into the dataframe and adding the a column for their run name
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

# concat all the runs into one table, there a try statements here due to whether the dataframe has values in them
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

# swithcing directory to inmport the tech mapping to tech_type and color
os.chdir(dir_output)
df_color = pd.read_csv(PROJECT_ROOT / 'analysis_tools' / 'tech_colors.csv')

# loop to create a dictionary for inputting into dash plotly
colorsetting = {}
for i in range(len(df_color)):
    tech_type = df_color['tech_type'][i]
    color = df_color['hex'][i]
    colorsetting[tech_type] = color

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
        ['run', 'tech_type', 'region', 'year', 'hour', 'generation_total'],
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
        ['run', 'tech_type', 'region', 'year', 'hour', 'storage_level'],
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
        ['run', 'tech_type', 'region', 'year', 'hour', 'storage_inflow'],
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
        ['run', 'tech_type', 'region', 'year', 'hour', 'storage_outflow'],
    )
    df_storageoutflow['Storage_flow'] = df_storageoutflow['storage_outflow']
except TypeError:
    print('Storage outflow dataframe is empty.')

df_storagecharge = pd.concat([df_storageinflow, df_storageoutflow])
df_storagecharge = df_storagecharge[['run', 'tech_type', 'region', 'year', 'hour', 'Storage_flow']]
df_storagecharge = (
    df_storagecharge.groupby(['run', 'tech_type', 'region', 'year', 'hour'])
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
        ['run', 'tech_type', 'region', 'year', 'capacity_builds'],
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
        ['run', 'tech_type', 'region', 'year', 'capacity_retirements'],
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
        df_capacitytotal, df_color, 'tech', ['run', 'tech_type', 'region', 'year', 'capacity_total']
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
s_regions = pd.unique(df_generation['region'])
s_regions.sort()
s_technologies = pd.unique(df_capacitytotal['tech_type'])
s_years = pd.unique(df_generation['year'])
s_years.sort()
# s_canregions = pd.unique(df_tradecan['region_international'])
# s_canregions.sort(key=lambda x: str(x))
s_runs = sorted(pd.unique(df_generation['run']))

# change directory back to the scripts folder (This is for the batch file to work.)

app = Dash(__name__)

# defines the layout of the app
app.layout = html.Div(
    [
        html.Div(
            children=[
                html.H1(children='Electricity Model Viewer'),
            ],
            style={
                'textAlign': 'center',
                'padding': '2rem',
                'backgroundImage': f'url({_header_bg_data_uri})',
                'backgroundSize': 'cover',
                'backgroundPosition': 'center',
                'boxShadow': '#e3e3e3 5px 5px 5px',
                'border-radius': '1px',
            },
        ),
        html.Div(
            children=[
                html.Label('Runs: ', style={'font-size': '17px'}),
                dcc.Dropdown(
                    id='run',
                    options=[{'label': run, 'value': run} for run in s_runs],
                    className='dropdown',
                    multi=True,
                ),
            ],
            style={'paddingTop': '2rem'},
        ),
        html.Div(
            children=[
                html.Label('Region: ', style={'font-size': '17px'}),
                dcc.Dropdown(
                    id='region',
                    options=[{'label': region, 'value': region} for region in s_regions],
                    className='dropdown',
                    multi=True,
                ),
            ],
            style={'paddingTop': '1rem', 'paddingBottom': '2rem'},
        ),
        dcc.Tabs(
            [
                dcc.Tab(
                    label='generation_total',
                    style={'font-size': '20px', 'font-weight': 'bold'},
                    children=[
                        html.Div(
                            children=[
                                html.Div(
                                    children=[
                                        html.Div(
                                            children=[
                                                html.Label(
                                                    'Year: ',
                                                    style={'padding': '5rem', 'font-size': '17px'},
                                                ),
                                                dcc.Dropdown(
                                                    id='genyear',
                                                    options=[
                                                        {'label': genyear, 'value': genyear}
                                                        for genyear in s_years
                                                    ],
                                                    className='dropdown',
                                                ),
                                            ],
                                            style={'paddingTop': '2rem'},
                                        ),
                                        html.Div(
                                            children=[
                                                html.Label(
                                                    'Technology: ',
                                                    style={'padding': '5rem', 'font-size': '17px'},
                                                ),
                                                dcc.Checklist(
                                                    id='gentech',
                                                    options=[
                                                        {'label': gentech, 'value': gentech}
                                                        for gentech in s_technologies
                                                    ],
                                                ),
                                            ],
                                            style={'paddingTop': '2rem'},
                                        ),
                                    ],
                                    style={
                                        'padding': '3rem',
                                        'margin': '2rem',
                                        'backgroundColor': 'rgb(224, 224, 224)',
                                        'boxShadow': '#e3e3e3 1px 1px 1px',
                                        'border-radius': '3px',
                                        'marginTop': '2rem',
                                    },
                                ),
                                html.Div(
                                    children=[
                                        html.H2('Generation Area Charts'),
                                        dcc.Graph(id='gen_graph_area'),
                                        html.H2('Storage Level Area Charts'),
                                        dcc.Graph(id='storage_level_graph_area'),
                                        html.H2('Storage Flow Area Charts'),
                                        dcc.Graph(id='storage_flow_graph_area'),
                                        html.H2('Unmet Load Area Charts'),
                                        dcc.Graph(id='unmet_graph_area'),
                                    ],
                                    style={
                                        'padding': '0.3rem',
                                        'marginTop': '1rem',
                                        'marginLeft': '1rem',
                                        'boxShadow': '#e3e3e3 1px 1px 1px',
                                        'border-radius': '1px',
                                    },
                                ),
                            ],
                            style={'display': 'flex', 'flexDirection': 'row'},
                        ),
                        html.Div(
                            children=[
                                html.Div(
                                    children=[
                                        html.Div(
                                            children=[
                                                html.Label(
                                                    'Technology: ',
                                                    style={'padding': '5rem', 'font-size': '17px'},
                                                ),
                                                dcc.Dropdown(
                                                    id='gentech2',
                                                    options=[
                                                        {'label': gentech2, 'value': gentech2}
                                                        for gentech2 in s_technologies
                                                    ],
                                                ),
                                            ],
                                            style={'paddingTop': '2rem'},
                                        ),
                                    ],
                                    style={
                                        'padding': '3rem',
                                        'margin': '2rem',
                                        'backgroundColor': 'rgb(224, 224, 224)',
                                        'boxShadow': '#e3e3e3 1px 1px 1px',
                                        'border-radius': '3px',
                                        'marginTop': '2rem',
                                    },
                                ),
                                html.Div(
                                    children=[
                                        html.H2('Generation Line Charts'),
                                        dcc.Graph(id='gen_graph_line'),
                                        html.H2('Storage Level Line Charts'),
                                        dcc.Graph(id='storage_level_graph_line'),
                                        html.H2('Storage Flow Line Charts'),
                                        dcc.Graph(id='storage_flow_graph_line'),
                                        html.H2('Unmet Load Line Charts'),
                                        dcc.Graph(id='unmet_graph_line'),
                                    ],
                                    style={
                                        'padding': '0.3rem',
                                        'marginTop': '1rem',
                                        'marginLeft': '1rem',
                                        'boxShadow': '#e3e3e3 1px 1px 1px',
                                        'border-radius': '1px',
                                    },
                                ),
                            ],
                            style={'display': 'flex', 'flexDirection': 'row'},
                        ),
                    ],
                ),
                dcc.Tab(
                    label='Capacity',
                    style={'font-size': '20px', 'font-weight': 'bold'},
                    children=[
                        html.Div(
                            children=[
                                html.Div(
                                    children=[
                                        html.Div(
                                            children=[
                                                html.Label(
                                                    'Technology: ',
                                                    style={'padding': '5rem', 'font-size': '17px'},
                                                ),
                                                dcc.Checklist(
                                                    id='captech',
                                                    options=[
                                                        {'label': captech, 'value': captech}
                                                        for captech in s_technologies
                                                    ],
                                                ),
                                            ],
                                            style={'paddingTop': '2rem'},
                                        ),
                                    ],
                                    style={
                                        'padding': '3rem',
                                        'margin': '2rem',
                                        'backgroundColor': 'rgb(224, 224, 224)',
                                        'boxShadow': '#e3e3e3 1px 1px 1px',
                                        'border-radius': '3px',
                                        'marginTop': '2rem',
                                    },
                                ),
                                html.Div(
                                    children=[
                                        html.H2('Capacity Total Bar Charts'),
                                        dcc.Graph(id='cap_graph_bar'),
                                        html.H2('Capacity Builds Bar Charts'),
                                        dcc.Graph(id='cap_build_graph_bar'),
                                        html.H2('Capacity Retirements Bar Charts'),
                                        dcc.Graph(id='cap_retire_graph_bar'),
                                    ],
                                    style={
                                        'padding': '0.3rem',
                                        'marginTop': '1rem',
                                        'marginLeft': '1rem',
                                        'boxShadow': '#e3e3e3 1px 1px 1px',
                                        'border-radius': '1px',
                                    },
                                ),
                            ],
                            style={'display': 'flex', 'flexDirection': 'row'},
                        ),
                    ],
                ),
                dcc.Tab(
                    label='Trade',
                    style={'font-size': '20px', 'font-weight': 'bold'},
                    children=[
                        html.Div(
                            children=[
                                html.Div(
                                    children=[
                                        html.Div(
                                            children=[
                                                html.Label(
                                                    'Year: ',
                                                    style={'padding': '5rem', 'font-size': '17px'},
                                                ),
                                                dcc.Dropdown(
                                                    id='trdyear',
                                                    options=[
                                                        {'label': trdyear, 'value': trdyear}
                                                        for trdyear in s_years
                                                    ],
                                                    className='dropdown',
                                                ),
                                            ],
                                            style={'paddingTop': '2rem'},
                                        ),
                                    ],
                                    style={
                                        'padding': '3rem',
                                        'margin': '2rem',
                                        'backgroundColor': 'rgb(224, 224, 224)',
                                        'boxShadow': '#e3e3e3 1px 1px 1px',
                                        'border-radius': '3px',
                                        'marginTop': '2rem',
                                    },
                                ),
                                html.Div(
                                    children=[
                                        html.H2('Region Trade Charts'),
                                        dcc.Graph(id='trade_graph_area'),
                                        html.H2('Region Trade Canada Charts'),
                                        dcc.Graph(id='tradecan_graph_area'),
                                    ],
                                    style={
                                        'padding': '0.3rem',
                                        'marginTop': '1rem',
                                        'marginLeft': '1rem',
                                        'boxShadow': '#e3e3e3 1px 1px 1px',
                                        'border-radius': '1px',
                                    },
                                ),
                            ],
                            style={'display': 'flex', 'flexDirection': 'row'},
                        ),
                    ],
                ),
            ]
        ),
    ]
)

# each callback and update_figure correspond to the graph id for each chart to be updated by the filter


@app.callback(
    Output('gen_graph_area', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
    Input('gentech', 'value'),
)
def update_figure(region, genyear, run, gentech):
    """Build the stacked-area generation-by-tech figure for the selected regions/year/runs."""
    filtered_df_gen = df_generation[(df_generation.year == genyear)]

    if region:
        filtered_df_gen = filtered_df_gen[filtered_df_gen['region'].isin(region)]
        filtered_df_gen = filtered_df_gen[['run', 'tech_type', 'year', 'hour', 'generation_total']]
        filtered_df_gen = (
            filtered_df_gen.groupby(['run', 'tech_type', 'year', 'hour'])
            .generation_total.sum()
            .reset_index()
        )
    else:
        filtered_df_gen = filtered_df_gen[['run', 'tech_type', 'year', 'hour', 'generation_total']]
        filtered_df_gen = (
            filtered_df_gen.groupby(['run', 'tech_type', 'year', 'hour'])
            .generation_total.sum()
            .reset_index()
        )

    if gentech:
        filtered_df_gen = filtered_df_gen[filtered_df_gen['tech_type'].isin(gentech)]

    if run:
        filtered_df_gen = filtered_df_gen[filtered_df_gen['run'].isin(run)]

    fig_gen = px.area(
        filtered_df_gen,
        x='hour',
        y='generation_total',
        color='tech_type',
        facet_col='run',
        color_discrete_map=colorsetting,
        width=1600,
        height=500,
    )
    return fig_gen


@app.callback(
    Output('storage_level_graph_area', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
)
def update_figure(region, genyear, run):
    """Build the stacked-area storage-level figure for the selected regions/year/runs."""
    filtered_df_storagelevel = df_storagelevel[(df_storagelevel.year == genyear)]

    if region:
        filtered_df_storagelevel = filtered_df_storagelevel[
            filtered_df_storagelevel['region'].isin(region)
        ]
        filtered_df_storagelevel = filtered_df_storagelevel[
            ['run', 'tech_type', 'year', 'hour', 'storage_level']
        ]
        filtered_df_storagelevel = (
            filtered_df_storagelevel.groupby(['run', 'tech_type', 'year', 'hour'])
            .storage_level.sum()
            .reset_index()
        )
    else:
        filtered_df_storagelevel = filtered_df_storagelevel[
            ['run', 'tech_type', 'year', 'hour', 'storage_level']
        ]
        filtered_df_storagelevel = (
            filtered_df_storagelevel.groupby(['run', 'tech_type', 'year', 'hour'])
            .storage_level.sum()
            .reset_index()
        )

    if run:
        filtered_df_storagelevel = filtered_df_storagelevel[
            filtered_df_storagelevel['run'].isin(run)
        ]

    fig_storagelevel = px.area(
        filtered_df_storagelevel,
        x='hour',
        y='storage_level',
        color='tech_type',
        facet_col='run',
        color_discrete_map=colorsetting,
        width=1600,
        height=500,
    )
    return fig_storagelevel


@app.callback(
    Output('storage_flow_graph_area', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
)
def update_figure(region, genyear, run):
    """Build the stacked-area storage charge/discharge figure for the selected regions/year/runs."""
    filtered_df_storagecharge = df_storagecharge[(df_storagecharge.year == genyear)]

    if region:
        filtered_df_storagecharge = filtered_df_storagecharge[
            filtered_df_storagecharge['region'].isin(region)
        ]
        filtered_df_storagecharge = filtered_df_storagecharge[
            ['run', 'tech_type', 'year', 'hour', 'Storage_flow']
        ]
        filtered_df_storagecharge = (
            filtered_df_storagecharge.groupby(['run', 'tech_type', 'year', 'hour'])
            .Storage_flow.sum()
            .reset_index()
        )
    else:
        filtered_df_storagecharge = filtered_df_storagecharge[
            ['run', 'tech_type', 'year', 'hour', 'Storage_flow']
        ]
        filtered_df_storagecharge = (
            filtered_df_storagecharge.groupby(['run', 'tech_type', 'year', 'hour'])
            .Storage_flow.sum()
            .reset_index()
        )

    if run:
        filtered_df_storagecharge = filtered_df_storagecharge[
            filtered_df_storagecharge['run'].isin(run)
        ]

    fig_storagecharge = px.area(
        filtered_df_storagecharge,
        x='hour',
        y='Storage_flow',
        color='tech_type',
        facet_col='run',
        color_discrete_map=colorsetting,
        width=1600,
        height=500,
    )
    return fig_storagecharge


@app.callback(
    Output('unmet_graph_area', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
)
def update_figure(region, genyear, run):
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
        filtered_df_unmetload, x='hour', y='unmet_load', facet_col='run', width=1600, height=500
    )
    return fig_unmetload


@app.callback(
    Output('gen_graph_line', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
    Input('gentech2', 'value'),
)
def update_figure(region, genyear, run, gentech2):
    """Build the line generation figure for a single tech across the selected regions/year/runs."""
    filtered_df_gen = df_generation[
        (df_generation.year == genyear) & (df_generation.tech_type == gentech2)
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
        width=1600,
        height=500,
    )
    return fig_gen


@app.callback(
    Output('storage_level_graph_line', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
    Input('gentech2', 'value'),
)
def update_figure(region, genyear, run, gentech2):
    """Build the line storage-level figure for one tech across the selected regions/year/runs."""
    filtered_df_storagelevel = df_storagelevel[
        (df_storagelevel.year == genyear) & (df_storagelevel.tech_type == gentech2)
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
        width=1600,
        height=500,
    )
    return fig_storagelevel


@app.callback(
    Output('storage_flow_graph_line', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
    Input('gentech2', 'value'),
)
def update_figure(region, genyear, run, gentech2):
    """Build the line storage flow figure for one tech across the selected regions/year/runs."""
    filtered_df_storagecharge = df_storagecharge[
        (df_storagecharge.year == genyear) & (df_storagecharge.tech_type == gentech2)
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
        width=1600,
        height=500,
    )
    return fig_storagecharge


@app.callback(
    Output('unmet_graph_line', 'figure'),
    Input('region', 'value'),
    Input('genyear', 'value'),
    Input('run', 'value'),
)
def update_figure(region, genyear, run):
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
        width=1600,
        height=500,
    )
    return fig_unmetload


@app.callback(
    Output('cap_graph_bar', 'figure'),
    Input('region', 'value'),
    Input('run', 'value'),
    Input('captech', 'value'),
)
def update_figure(region, run, captech):
    """Build the bar total-capacity-by-tech figure for the selected regions/techs/runs."""
    filtered_df_capacitytotal = df_capacitytotal

    if region:
        filtered_df_capacitytotal = df_capacitytotal[df_capacitytotal['region'].isin(region)]
        filtered_df_capacitytotal = filtered_df_capacitytotal[
            ['run', 'tech_type', 'year', 'capacity_total']
        ]
        filtered_df_capacitytotal = (
            filtered_df_capacitytotal.groupby(['run', 'tech_type', 'year'])
            .capacity_total.sum()
            .reset_index()
        )
    else:
        filtered_df_capacitytotal = filtered_df_capacitytotal[
            ['run', 'tech_type', 'year', 'capacity_total']
        ]
        filtered_df_capacitytotal = (
            filtered_df_capacitytotal.groupby(['run', 'tech_type', 'year'])
            .capacity_total.sum()
            .reset_index()
        )

    if captech:
        filtered_df_capacitytotal = filtered_df_capacitytotal[
            filtered_df_capacitytotal['tech_type'].isin(captech)
        ]

    if run:
        filtered_df_capacitytotal = filtered_df_capacitytotal[
            filtered_df_capacitytotal['run'].isin(run)
        ]

    fig_capacitytotal = px.bar(
        filtered_df_capacitytotal,
        x='year',
        y='capacity_total',
        color='tech_type',
        facet_col='run',
        color_discrete_map=colorsetting,
        width=1600,
        height=500,
    )
    return fig_capacitytotal


@app.callback(
    Output('cap_build_graph_bar', 'figure'),
    Input('region', 'value'),
    Input('run', 'value'),
    Input('captech', 'value'),
)
def update_figure(region, run, captech):
    """Build the bar capacity-builds-by-tech figure for the selected regions/techs/runs."""
    filtered_df_capacitybuilds = df_capacitybuilds

    if region:
        filtered_df_capacitybuilds = df_capacitybuilds[df_capacitybuilds['region'].isin(region)]
        filtered_df_capacitybuilds = filtered_df_capacitybuilds[
            ['run', 'tech_type', 'year', 'capacity_builds']
        ]
        filtered_df_capacitybuilds = (
            filtered_df_capacitybuilds.groupby(['run', 'tech_type', 'year'])
            .capacity_builds.sum()
            .reset_index()
        )
    else:
        filtered_df_capacitybuilds = filtered_df_capacitybuilds[
            ['run', 'tech_type', 'year', 'capacity_builds']
        ]
        filtered_df_capacitybuilds = (
            filtered_df_capacitybuilds.groupby(['run', 'tech_type', 'year'])
            .capacity_builds.sum()
            .reset_index()
        )

    if captech:
        filtered_df_capacitybuilds = filtered_df_capacitybuilds[
            filtered_df_capacitybuilds['tech_type'].isin(captech)
        ]

    if run:
        filtered_df_capacitybuilds = filtered_df_capacitybuilds[
            filtered_df_capacitybuilds['run'].isin(run)
        ]

    fig_capacitybuilds = px.bar(
        filtered_df_capacitybuilds,
        x='year',
        y='capacity_builds',
        color='tech_type',
        facet_col='run',
        color_discrete_map=colorsetting,
        width=1600,
        height=500,
    )
    return fig_capacitybuilds


@app.callback(
    Output('cap_retire_graph_bar', 'figure'),
    Input('region', 'value'),
    Input('run', 'value'),
    Input('captech', 'value'),
)
def update_figure(region, run, captech):
    """Build the bar capacity-retirements-by-tech figure for the selected regions/techs/runs."""
    filtered_df_capacityretire = df_capacityretire

    if region:
        filtered_df_capacityretire = df_capacityretire[df_capacityretire['region'].isin(region)]
        filtered_df_capacityretire = filtered_df_capacityretire[
            ['run', 'tech_type', 'year', 'capacity_retirements']
        ]
        filtered_df_capacityretire = (
            filtered_df_capacityretire.groupby(['run', 'tech_type', 'year'])
            .capacity_retirements.sum()
            .reset_index()
        )
    else:
        filtered_df_capacityretire = filtered_df_capacityretire[
            ['run', 'tech_type', 'year', 'capacity_retirements']
        ]
        filtered_df_capacityretire = (
            filtered_df_capacityretire.groupby(['run', 'tech_type', 'year'])
            .capacity_retirements.sum()
            .reset_index()
        )

    if captech:
        filtered_df_capacityretire = filtered_df_capacityretire[
            filtered_df_capacityretire['tech_type'].isin(captech)
        ]

    if run:
        filtered_df_capacityretire = filtered_df_capacityretire[
            filtered_df_capacityretire['run'].isin(run)
        ]

    fig_capacityretire = px.bar(
        filtered_df_capacityretire,
        x='year',
        y='capacity_retirements',
        color='tech_type',
        facet_col='run',
        color_discrete_map=colorsetting,
        width=1600,
        height=500,
    )
    return fig_capacityretire


@app.callback(
    Output('trade_graph_area', 'figure'),
    Input('region', 'value'),
    Input('trdyear', 'value'),
    Input('run', 'value'),
)
def update_figure(region, trdyear, run):
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
        width=1600,
        height=500,
    )
    return fig_trade


@app.callback(
    Output('tradecan_graph_area', 'figure'),
    Input('region', 'value'),
    Input('trdyear', 'value'),
    Input('run', 'value'),
)
def update_figure(region, trdyear, run):
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
        width=1600,
        height=500,
    )
    return fig_tradecan


# when running, ctrl+click on the http://127.0.0.1:8050/ which opens a broswer
app.run(debug=True)
