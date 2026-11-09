"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Hourly load profiles of the 25 EMM regions from annual end-use loads (python port of the NEMS
LDSM algorithm).

In C-NEMS the model is run through LoadSequencer (sequencer.py) like the other models; the pieces
are load_model.py (the algorithm), calendars.py (day-type calendars), data_validation.py (input
checks, including the output checks), hydrogen.py (hydrogen demand) and run.py (one run from
keyword arguments).  See README.md.
"""
