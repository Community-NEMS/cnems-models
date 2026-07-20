"""Build/run/solve orchestration for the electricity model.

The functionality that previously lived here (``build_elec_model``, ``solve_elec_model``,
``run_elec_model``, and the linear-learning helpers ``init_old_cap`` / ``set_new_cap`` /
``cost_learning_func`` / ``update_expansion_cost``) has moved to ``sequencer.py``, where it is implemented on
the :class:`~src.models.electricity.sequencer.ElectricitySequencer` (an
``IntegratedModelSequencer``). Import those names from ``src.models.electricity.sequencer`` instead.
"""
