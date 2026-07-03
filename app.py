"""
BlueSky Graphical User Interface.

Built using Dash - https://dash.plotly.com/.

Created on Wed Sept 19 2024 by Adam Heisey
"""

import ast
import os
import shlex
import subprocess
import sys

# Import packages
import dash
import dash_bootstrap_components as dbc
import tomli
import tomlkit
from dash import Input, Output, State, dcc, html

# Import python modules
from main import app_main

# Initialize the Dash app
app = dash.Dash(
    __name__,
    prevent_initial_callbacks=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    assets_folder='docs/images/',
)
app.title = 'BlueSky Model Runner'

docs_dir = os.path.abspath('docs/build/html')

# use the current python interpreter to run the html docs in the background
with open(os.devnull, 'w') as devnull:
    http_server_process = subprocess.Popen(
        [sys.executable, '-m', 'http.server', '8000', '--directory', docs_dir],
        stdout=devnull,
        stderr=devnull,
    )

# blusesky image in assets folder
image_src = app.get_asset_url('ProjectBlueSkywebheaderimageblack.jpg')

# Define layout
app.layout = dbc.Container(
    [
        dbc.Row(
            dbc.Col(
                dbc.Button(
                    'Code Documentation',
                    href='http://localhost:8000/index.html',
                    color='info',
                    className='mt-3',
                    target='_blank',
                ),
                width='auto',
                className='text-left',
            ),
            justify='start',
        ),
        html.H1('BlueSky Model Runner', className='text-center'),
        html.Img(src=image_src),
        html.H2(id='status', className='text-center', style={'color': 'red'}),
        html.H3(id='output-state'),
        dbc.Label('Select Mode to Run:'),
        dcc.RadioItems(
            id='mode-selector',
            options=[
                {'label': mode, 'value': mode}
                for mode in ['unified-combo', 'gs-combo', 'standalone']
            ],
            value='standalone',
        ),
        dbc.Button('Run', id='run-button', color='primary', className='mt-2'),
        dcc.Loading(dbc.Progress(id='progress', value=0, max=100, style={'height': '30px'})),
        # Section for uploading and editing TOML config file
        html.Hr(),
        html.H4('Edit Configuration Settings'),
        # dcc.Upload(id='upload-toml', children=html.Button('Upload TOML'), multiple=False),
        html.Div(id='config-editor'),
        dbc.Button('Save Changes', id='save-toml-button', className='mt-2', disabled=False),
    ],
    fluid=True,
)


# Auto load the template
@app.callback(
    Output('config-editor', 'children'),
    Output('save-toml-button', 'disabled'),
    Input('config-editor', 'id'),
    prevent_initial_call=False,
)
def auto_load_toml(selected_mode):
    """reads in the configuration settings into the app that are saved in the config template.

    Parameters
    ----------
    selected_mode :
        user selected run mode option, current options are 'unified-combo', 'gs-combo', 'standalone'

    Returns
    -------
        config default settings
    """
    config_file_path = os.path.join('src/common', 'run_config_template.toml')

    if os.path.exists(config_file_path):
        # read run config with comments
        with open(config_file_path, 'rb') as f:
            config_content = tomli.load(f)

        # Dynamically create input fields for the TOML content
        inputs = []

        for key, value in config_content.items():
            inputs.append(
                html.Div(
                    [
                        dbc.Label(f'{key}:'),
                        dbc.Input(
                            id={'type': 'config-input', 'index': key},
                            value=str(value),
                            debounce=True,
                        ),
                    ],
                    style={'margin-bottom': '10px'},
                )
            )

        return inputs, False  # Enable the Save button
    return [], True  # Disable the Save button if no file is uploaded


# Save the modified TOML file with comments
@app.callback(
    Output('output-state', 'children'),
    Input('save-toml-button', 'n_clicks'),
    State({'type': 'config-input', 'index': dash.ALL}, 'value'),
    State({'type': 'config-input', 'index': dash.ALL}, 'id'),
    prevent_initial_call=True,
)
def save_toml(n_clicks, input_values, input_ids):
    """saves the configuration settings in the app to the config file.

    Parameters
    ----------
    n_clicks :
        click to save toml button
    input_values :
        config values associated with components specified in the web app
    input_ids :
        config components associated with values specified in the web app

    Returns
    -------
        empty string
    """
    config_template = os.path.join('src/common', 'run_config_template.toml')
    config_file_path = os.path.join('src/common', 'run_config.toml')

    if n_clicks:
        # Load the original file to preserve its structure and comments
        with open(config_template, 'r') as f:
            config_doc = tomlkit.parse(f.read())

        # Update the config_doc with new values
        for item, value in zip(input_ids, input_values):
            config_doc[item['index']] = convert_value(value)

        # Write the updated content back, preserving comments
        with open(config_file_path, 'w') as f:
            f.write(tomlkit.dumps(config_doc))

        return "Configuration settings saved successfully as 'run_config.toml'."
    return ''


# Function to convert values back to original types
def convert_value(value):
    """Function to maintain the original type for config values"""
    # handle boolean speficially
    if isinstance(value, str):
        if value.lower() == 'true':
            return True
        elif value.lower() == 'false':
            return False
    # now other types
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


# Callback to handle run button click and show progress
@app.callback(
    Output('status', 'children'),
    Output('progress', 'value'),
    Input('run-button', 'n_clicks'),
    State('mode-selector', 'value'),
    prevent_initial_call=True,
)
def run_mode(n_clicks, selected_mode):
    """passes the selected mode to main.py and runs the script.

    Parameters
    ----------
    n_clicks :
        click to the run button
    selected_mode :
        user selected run mode option, current options are 'unified-combo', 'gs-combo', 'standalone'

    Returns
    -------
        message stating either: model has finished or there was an error and it wasn't able to run
    """
    # define modes allowed - sanitize user input
    modes_available = {'unified-combo', 'gs-combo', 'standalone'}

    if selected_mode not in modes_available:
        return f"Error: '{selected_mode}' is not a valide mode.", 0

    try:
        selected_mode = shlex.quote(selected_mode)

        # run selected mode
        app_main(selected_mode)

        return (
            f"{selected_mode.capitalize()} mode has finished running. See results in output/'{selected_mode}'.",
            100,
        )
    except Exception:
        error_msg = f'Error, not able to run {selected_mode}. Please check the log script/terminal, exit out of browser, and restart.'
        return error_msg, 0


if __name__ == '__main__':
    try:
        app.run_server(debug=True, host='localhost', port=8080)
    finally:
        http_server_process.terminate()
