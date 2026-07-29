# BlueSky Prototype
### Table of Contents
- [Overview](#overview)
- [Setup](#setup)
- [Usage](#usage)
- [Documentation](#documentation)

&nbsp;
# Overview

[Project BlueSky](https://www.eia.gov/totalenergy/data/bluesky/) is an EIA initiative to develop an open source, next generation energy systems model, which will eventually be used to produce the Annual Energy Outlook ([AEO](https://www.eia.gov/outlooks/aeo/)) and International Energy Outlook ([IEO](https://www.eia.gov/outlooks/ieo/)). Our outlooks are focused on projecting realistic outcomes under a given set of assumptions, accounting for rapid technological innovation, new policy, changing consumer preferences, shifting trade patterns, and the real-world friction associated with the adoption of novel or risky technology. To address these challenges, the next generation model is designed to be modular, flexible, transparent, and robust.

The BlueSky Prototype is the first step towards creating this next generation model. Our objective in releasing the prototype is to give the modeling community an early opportunity to experiment with the new framework and provide feedback. See the [contribution file](/CONTRIBUTING.md) for information on how to give feedback.

Given current resource constraints, EIA has paused future Project BlueSky model development activity. Project BlueSky enabled EIA to explore new energy modeling approaches, and this foundational work will serve as a basis for EIA’s future long-term model development efforts as EIA continues to advance its energy modeling capabilities.

There are four key features associated with the BlueSky Prototype:

1. **A computationally efficient, modular structure that allows each sector to flexibly capture the underlying market behavior using different governing dynamics.**
The protype contains three test modules representing electricity, hydrogen, and residential demand. Both the electricity and hydrogen models employ least-cost linear optimization as a governing dynamic, while the residential sector model adjusts demand levels based on prices from the other two sectors. The prototype includes an 'integrator' module that can either execute an individual module or combine the modules into a single nonlinear optimization problem [using two different methods](src/integrator/README.md). This feature will allow the next generation model to be **modular and flexible**.

2. **Well-documented Python code.** In the prototype, we have tried to write Python code that is easy to follow. We have included both detailed, high-level descriptions of each module in markdown, as well as code documentation using [Sphinx](https://www.sphinx-doc.org/en/master/), which we use to aggregate doc strings embedded in the code to create documentation. Most open-source models develop coding and documentation styles prior to their initial release. Even for models that are easy to apply, the underlying code and documentation are not always clear. We are inviting the community to provide feedback at this early stage so that code and documentation developed for the production version are as transparent as possible. This feature will allow the next generation model to be **transparent**.

3. **An example data pipeline documented with Snakemake.** Energy system models are data intensive and require a multitude of data transformations to convert raw data from a known source into formatted model input data. We are experimenting with [Snakemake](https://snakemake.readthedocs.io/en/stable/), a tool that allows us to create a well-documented data management framework to organize, pre-process, and post-process model data. In the prototype, Snakemake is used in a standalone application to create a geospatial crosswalk involving both electricity operations and weather data. We are considering the use of Snakemake across the model to comprehensively manage input data. This feature will allow the next generation model to be **transparent**.

4. **Two methods to characterize model sensitivity.** One method uses an efficient way to approximate derivatives of model outputs with respect to inputs for systems of nonlinear equations using complex variables ([Lai et al., 2005](https://arc.aiaa.org/doi/abs/10.2514/6.2005-5944)). The method is broadly applicable to any module where the equations are continuously differentiable and has been implemented in applications beyond energy modeling. A second method is directly applicable to convex optimization problems and applies approximation methods for sensitivity analysis on optimization models ([Castillo et al., 2006](https://link.springer.com/article/10.1007/s10957-005-7557-y)). These methods will allow us to ensure **robust** model results by testing model sensitivity. Additional methods to quantify model sensitivity and uncertainty will be implemented in the future.

<img src="docs/images/PrototypeDiagram.png" alt="Prototype diagram" width="700"/>

The BlueSky Prototype is focused on demonstrating the four features above. The feedback gathered from community experimentation will be used to improve future versions of the next generation model. Given the prototype's limited scope, it does NOT provide results that are relevant to the real world. To emphasize this point, we have omitted any reference to a specific country, region, or timeframe in the input data.

&nbsp;
# Setup

Follow these steps to set up and run the BlueSky Prototype model.

### Prerequisites
To set up the appropriate Python environment, you will need to install the following:
* Anaconda (or Miniconda 3)
* Python (version 3.12)
* Git Bash
* Visual Studio Code or preferred integrated development environment (IDE) (optional)

### Steps
1. [Clone the Bluesky repository](#clone-the-bluesky-repository)
2. [Set up the environment](#setup-environment-with-anaconda-prompt)

### **Clone the BlueSky repository**

Clone the BlueSky GitHub repository to your local computer. See the tabs below for instructions using Git or VS-Code.

#### **Clone with git**
<details>

## Prerequisites

**Git**: Ensure that you have Git Bash installed on your machine. You can download it from [git-scm.com](https://git-scm.com/).

## Steps to Clone a Repository

1. **Open Your Terminal or Command Prompt**:
   - On **Windows**, you can use Git Bash or Command Prompt.
   - On **macOS** or **Linux**, open the Terminal.

<img src="docs/images/install-usage/gitbash.PNG" alt="git bash image" width="700"/>

2. **Navigate to Your Desired Directory**:
   Use the `cd` command to change to the directory where you want to clone the repository. For example:

   ```bash
   cd path/to/your/directory
   ```

3. **Get the Repository URL**:
   - Go to the GitHub repository page you want to clone.
   - Click on the **Code** button, then copy the repository URL (either HTTPS or SSH).

4. **Clone the Repository**:
   Use the `git clone` command followed by the repository URL. For example:
   ```bash
   git clone https://github.com/username/repository.git
   ```
   or if using SSH:
   ```bash
   git clone git@github.com:username/repository.git
   ```

5. **Access the Cloned Repository**:
   After cloning, navigate into the repository folder:

   ```bash
   cd repository
   ```

## Additional Notes

- If you're using HTTPS, you may be prompted to enter your GitHub username and password.

</details>

#### **Clone with Visual Studio Code**
<details>

## Prerequisites

**Visual Studio Code**: Ensure that you have VS-Code installed on your machine.

## Steps to Clone a Repository using Visual Studio Code

1. Open VS-Code and **click** `Clone Git Repository` *circled below in $`\textcolor{red}{\text{RED}}`$

<img src="docs/images/install-usage/vscode1.PNG" alt="vs code image 1" width="700"/>

2. Paste the repository link into the search bar when prompted and hit **ENTER** *circled below in $`\textcolor{red}{\text{RED}}`$

```bash
https://github.com/EIAgov/BlueSky.git
```

<img src="docs/images/install-usage/vscode2.PNG" alt="vs code image 2" width="700"/>

3. The *File Explorer* should pop up and ask where you would like to save the cloned repository.

4. **Click** on the folder destination and hit save.

The repository will be cloned to your specified location.

</details>


### **Setup Environment with Anaconda Prompt**

1. **Open Anaconda Prompt**

   Launch the Anaconda Prompt to begin the setup process.


2. **Navigate to the Working Directory**

   Use the `cd` command to navigate to the working directory for the BlueSky Prototype. For example:

   ```bash
   cd path\to\your\BlueSkyPrototype
   ```

3. **Run the Environment Creation Script**

   Note: if you have already run this script, you must instead update the environment. Go to step 4 for more information.

   For **Windows**, run the batch file to set up the Conda environment:

   ```bash
   envs\env-setup.bat
   ```

   On **Linux** or **macOS**, run the contents of the bat file in Terminal.

   ```bash
   conda env create -f envs/conda_env.yml && conda activate bsky
   ```

   This will create and set up the necessary Conda environment for the project.

4. **Update the Environment**

   The update from BlueSky version 1.0 to 1.1 has several package changes that will require an environment update. To update an environment that already exists, run the following:

   ```bash
   conda env update --f envs/conda_env.yml  --prune
   ```

   We are aware of some conda issues where this may throw errors and not correctly update. In this case, run the following to re-create the environment from scratch:

   ```bash
   conda env create --f envs/conda_env.yml  --yes
   ```


#### Setup Instructions with *Screenshots*
<details>
   <summary> Click to expand setup instructions </summary>

   ## Steps

1. **Open Anaconda Prompt**

   Launch the Anaconda Prompt to begin the setup process.

<img src="docs/images/install-usage/anaconda-prompt.PNG" alt="Image for anaconda prompt" />

2. **Navigate to the Working Directory**

   Use the `cd` command to navigate to the working directory for the BlueSky Prototype. For example:

   ```bash
   cd path\to\your\BlueSkyPrototype
   ```

<img src="docs/images/install-usage/cd-dir.PNG" alt="Image to navigate to directory" />

3. **Run the Environment Creation Script**

   On **Windows**, run the batch file to set up the Conda environment:

   ```bash
   envs\env-setup.bat
   ```

   This will create and set up the necessary Conda environment for the project.

<img src="docs/images/install-usage/env-setup.PNG" alt="Image to show environment set up process" />

</details>

&nbsp;
# Usage
The BlueSky Prototype can be executed either as a Dash app that provides a graphical user interface (GUI), or run from the command line. All scripts should be run from the top-level directory of the project via the app or ```main.py``` file, as explained below.

Note that the Prototype can be run with different configuration options to indicate the modules, regionality, temporal resolution, solver options, and other module-specific settings. These options are encoded in [```run_config.toml```](src/common/run_config.toml). When running the model from the command line, ```run_config.toml``` must be modified directly and contains detailed descriptions of the configuration settings. When running the model from the GUI, the same configuration options can be set via the interface without accessing the file directly. For GUI users, the configuration options are described in detail [here](#dash-app-features).

One of the key features of the BlueSky Prototype is the ability to run modules individually or select between two solve options for the integrated runs. The table below describes the different modes when running the model. More details on these methods can be found in the [Integrator README](src/integrator/README.md).

|Mode   | Methods File   | Demonstrates |
|:----- | :------: | :--------- |
| standalone | [```runner.py```](src/integrator/runner.py) | Apply a unified (single optimization) method to one of the modules (Electricity, Hydrogen, Residential) |
| gs-combo | [```gaussseidel.py```](src/integrator/gaussseidel.py) |  Apply an iterative (Gauss-Seidel) method to 3-way combination of modules (Electricity, Hydrogen, Residential) |
| unified-combo | [```unified.py```](src/integrator/unified.py) | Apply a unified (single optimization) method to 3-way combination of modules (Electricity, Hydrogen, Residential)


### Ways to Run the Model
* [Run Model with Dash App GUI](#dash-app-graphical-user-interface-gui-steps)
* [Dash App Features Description](#dash-app-features)
* [Run Model in the Command Prompt](#command-prompt-steps)


<!---Below is a video which goes through how to set up to create your own runs. This video goes over how to set up the temporal and spatial resolution of your runs, any additional switches to configure the electricity model, how to select run modes, and the expected output after a run is complete.

<img src="images/readmevideotrim.mp4" alt="Instruction Video" width="500"/>
-->
### Dash App Graphical User Interface (GUI) Steps

1. **Open Anaconda Prompt**

   Launch the Anaconda Prompt to begin the setup process.


2. **Navigate to the Working Directory**

   Use the `cd` command to navigate to the working directory for the BlueSky Prototype. For example:

   ```bash
   cd path\to\your\BlueSky
   ```

3. **Run the Dash App**

   To launch the BlueSky Prototype app, use the following command:

   ```bash
   .\run_dash_app
   ```

   This will start the Dash application.


4. **Access the Web Interface**

   - Once the app is running, a web browser should automatically open.
   - If the GUI doesn’t show up immediately, refresh the browser page.

You're all set! The BlueSky Prototype model should now be running, and you can interact with it through the browser interface.

<img src="docs/images/install-usage/dash_screen.PNG" alt="Dash app main screen" width="700"/>


#### Usage Instructions with *Screenshots*
<details>
   <summary> Click to expand usage instructions </summary>

   ## Steps

1. **Open Anaconda Prompt**

   Launch the Anaconda Prompt to begin the setup process.

<img src="docs/images/install-usage/anaconda-prompt.PNG" alt="Image for anaconda prompt" width="700"/>

2. **Navigate to the Working Directory**

   Use the `cd` command to navigate to the working directory for the BlueSky Prototype. For example:

   ```bash
   cd path\to\your\BlueSkyPrototype
   ```

<img src="docs/images/install-usage/cd-dir.PNG" alt="Image to navigate to directory" width="700"/>


3. **Run the Dash App**

   To launch the BlueSky Prototype app, use the following command:

   ```bash
   .\run_dash_app
   ```

   This will start the Dash application.

<img src="docs/images/install-usage/run_dash.PNG" alt="cmd Image with run dash bat" width="700"/>

4. **Access the Web Interface**

   - Once the app is running, a web browser should automatically open.
   - If the GUI doesn’t show up immediately, refresh the browser page.

<img src="docs/images/install-usage/dash_screen.PNG" alt="Image of dash app look and feel" width="700"/>

You're all set! The BlueSky Prototype model should now be running, and you can interact with it through the browser interface.

</details>

### Dash App features

<img src="docs/images/install-usage/dash_usage.PNG" alt="features of dash app" width="700"/>

<img src="docs/images/install-usage/config_save.PNG" alt="config settings and save button" width="700"/>

#### Dash Features description

1. The **Edit Configuration Settings** section allows the user the ability to overide default settings, which are found in `run_config_template.toml`. Make sure to select **Save** to save any changes to the default settings.

2. **Save** button allows users to save model configuration inputs for select model runs. This action overwrites the `run_config.toml` file, which is used as the run file for model runs.

3. **Configuration message** This message notifies the user that they have successfully saved and updated the `run_config.toml` file used for model runs.

4. **Mode Selection and Run** This is where the user selects which model mode they would like to try for a given run. After making the selection, they must *click* the **Run** button. A blue loading feature will let the user know that model is running. A blue bar will appear when the model is finished.

5. **Output message** This red message notifies the user that the model successfully ran the mode selected and created a time stamp log and model outputs folder in the **outputs** folder in the main directory.

6. **Code Documentation Button** When selected, this button opens the `html` code documentation generated by **Sphinx** in a new browser tab.

To return to default settings, refresh the web browsers and *click* the **save** button to return to the template default settings.

### Command Prompt Steps
See the tab below for instructions on how to run the model using the command prompt.

<details>

Be sure to have the 'bsky' environment activated in a terminal, navigate to the 'BlueSky' directory in your terminal, and then run the following code:

```bash
python main.py --help
```

This should return a description of ```main.py```, as well as the options available within the command line interface. Now, check to see if we can run and solve a model by executing:

```bash
python main.py --mode standalone
```

If this reports an objective value, you have successfully run the model in standalone!

The file [```main.py```](main.py) processes user options as directed by the configuration file [```run_config.toml```](src/common/run_config.toml) and builds/solves an instance of an integrated (multiple modules) or standalone (single module) model. Any runs initiated from the top level ```main.py``` file will initiate logging and report to output folder. The log file contains (currently very limited) updates on run progress, solver output, etc. The default logging level is INFO. That may be increased by setting the command line argument --debug when running main.py.

It is also possible to set the option for ```default_mode``` in ```run_config.toml```. Then, run ```main.py``` without specifying a mode:

```bash
python main.py
```

</details>

&nbsp;
## Documentation
Documentation for each of the modules is available using the links below. We also include documentation for other products within the repository, including a sample data workflow tool, a sample unit test setup, and a sample output data viewer.

* [Integrator Module](src/integrator/README.md)
* Electricity Module
   - [Model](src/models/electricity/README.md)
* Hydrogen Module
   - [Model](src/models/hydrogen/README.md)
   - [Preprocessor](src/models/hydrogen/README.md)
* Residential Module
   - [Model](src/models/residential/README.md)
   - [Preprocessor](src/models/residential/preprocessor/README.md)
* [Sensitivity](src/sensitivity/README.md)
* [Data Workflow](sample/data_pipeline/README.md)
* [Unit Tests](tests/README.md)

Users can access code documentation following these [instructions for HTML, Markdown, and PDF documentation](docs/README.md). Alternatively, code documentation can be generated by the user within the GUI by clicking the 'code documentation' button, which will run Sphinx locally and generate an html file in your open browser. See the instructions on working with the GUI for more details

### Support
See the [contribution file](/CONTRIBUTING.md) for information on feedback. Please reach out to BlueSky team for assistance: nextgenerationmodel@eia.gov

### Authors
U.S. Energy Information Administration

### License
The BlueSky code, as distributed here, is governed by the [Apache 2.0 license](https://github.com/eiagov)

### Project status
In April 2025, EIA released Version 1.1 of the Project BlueSky prototype model.  Given current resource constraints, EIA will pause future Project BlueSky model development activity. Project BlueSky enabled EIA to explore new energy modeling approaches, and this foundational work will serve as a basis for EIA’s future long-term model development efforts as EIA continues to advance its energy modeling capabilities.
