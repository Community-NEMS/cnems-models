"""
BlueSky Graphical User Interface.

Built using Dash - https://dash.plotly.com/.

Created on Wed Sept 19 2024 by Adam Heisey
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

# Import packages
import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

# Import python modules
from main import main
from src.common.config_gui import (
    CONFIG_JSON_PATH,
    ConfigValidationError,
    build_config_form,
    load_config,
    parse_form_values,
    save_configs,
)
from src.common.models_modes import RunMode

logger = logging.getLogger(__name__)

# Initialize the Dash app
app = dash.Dash(
    __name__,
    prevent_initial_callbacks=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    assets_folder='app_images/',
)
app.title = 'Model Runner'

docs_dir = Path('docs/build/html').resolve()

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
        html.H1('Model Runner', className='text-center'),
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
def load_config_editor(_):
    """Loads the common/electricity configs and renders the config editor form.

    Prefers the last saved config (`run_configs/last_app_config.json`) if present, else falls
    back to the default TOML template.

    Returns
    -------
        list of Dash components for the config editor, and whether the Save button is disabled
    """
    try:
        common_config, elec_config, _path = load_config()
    except Exception as exc:
        logger.error('Failed to load configuration: %s', exc)
        return [html.Div(f'Error loading configuration: {exc}', style={'color': 'red'})], True

    return build_config_form(common_config, elec_config), False


# Save the modified config as a combined JSON file
@app.callback(
    Output('output-state', 'children'),
    Input('save-toml-button', 'n_clicks'),
    State({'type': 'config-input', 'section': dash.ALL, 'field': dash.ALL}, 'value'),
    State({'type': 'config-input', 'section': dash.ALL, 'field': dash.ALL}, 'id'),
    prevent_initial_call=True,
)
def save_config_editor(n_clicks, input_values, input_ids):
    """Validates the edited config form values and saves them as a combined JSON file.

    Parameters
    ----------
    n_clicks :
        click to save button
    input_values :
        config values associated with components specified in the web app
    input_ids :
        config components associated with values specified in the web app

    Returns
    -------
        a success message, or a list of validation error messages if the form is invalid
    """
    if not n_clicks:
        return ''

    common_raw, elec_raw = parse_form_values(input_ids, input_values)
    try:
        save_configs(common_raw, elec_raw)
    except ConfigValidationError as exc:
        return html.Ul(
            [html.Li(error) for error in exc.errors],
            style={'color': 'red'},
        )
    return 'Configuration settings saved successfully.'


# Callback to handle run button click and show progress
@app.callback(
    Output('status', 'children'),
    Output('progress', 'value'),
    Input('run-button', 'n_clicks'),
    State('mode-selector', 'value'),
    prevent_initial_call=True,
)
def run_mode(n_clicks, selected_mode):
    """Passes the selected mode to main.py and runs the script.

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
        return f"Error: '{selected_mode}' is not a valid mode.", 0

    # verify we have a saved config file
    config_path = CONFIG_JSON_PATH
    if not config_path.exists():
        return 'Error: No config file generated.  Please save config values in GUI.', 0

    try:
        selected_mode = RunMode(selected_mode)

        # run selected mode
        # app_main(selected_mode)
        main(common_config_path=config_path, run_mode=selected_mode)

        return (
            f'{selected_mode.value.capitalize()} mode has finished running. '
            f"See results in output/'{selected_mode.value}'.",
            100,
        )
    except Exception as exc:
        logger.error('Run failed for mode %s: %s', selected_mode, exc)
        error_msg = (
            f'Error, not able to run {selected_mode}. Please check the log script/terminal, '
            'exit out of browser, and restart.'
        )
        return error_msg, 0


if __name__ == '__main__':
    try:
        app.run(debug=True, host='localhost', port=8080)
    finally:
        http_server_process.terminate()
