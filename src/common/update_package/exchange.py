"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/12/26

Which update packages a run exchanges, and the region crosswalks its writers map them with.

An iterative run names its standard set of exchanges (package class names) in its config.
:meth:`ExchangeSpec.build` keeps those the run's selected models can carry -- sender and every
receiver selected -- and loads the crosswalks between the selected models.  The result is frozen and
picklable; each sequencer hands its model's share to its writer.  A standalone run uses
:meth:`ExchangeSpec.empty`, so its writer writes nothing.
"""

import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from src.common.models_modes import ModelType
from src.common.update_package.update_package import PACKAGE_TYPES, UpdatePackage
from src.integrator.region_crosswalk import CrosswalkSet

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExchangeSpec:
    """The packages each model of a run writes, and the crosswalks to write them with.

    Attributes
    ----------
    outbound : Mapping of ModelType to frozenset of type[UpdatePackage]
        ``{sender: package types it writes}``.  A model with no entry writes nothing.
    crosswalks : CrosswalkSet
        The region crosswalks between the run's models.
    """

    outbound: Mapping[ModelType, frozenset[type[UpdatePackage]]] = field(default_factory=dict)
    crosswalks: CrosswalkSet = field(default_factory=CrosswalkSet)

    @classmethod
    def empty(cls) -> ExchangeSpec:
        """A spec that exchanges nothing, for a standalone run."""
        return cls()

    @classmethod
    def build(cls, exchanges: Sequence[str], models: Collection[ModelType]) -> ExchangeSpec:
        """Keep the named exchanges the selected models can carry, and load their crosswalks.

        A package whose sender or any receiver is not among ``models`` is left out, and all such
        packages are named in one warning; the run proceeds without them.

        Parameters
        ----------
        exchanges : Sequence of str
            Package class names (keys of ``PACKAGE_TYPES``), the run's standard exchanges.
        models : Collection of ModelType
            The models the run selected.

        Returns
        -------
        ExchangeSpec

        Raises
        ------
        ValueError
            If a name is not a known package type.
        KeyError
            From ``CrosswalkSet.for_models``.
        """
        unknown = sorted(set(exchanges) - set(PACKAGE_TYPES))
        if unknown:
            raise ValueError(
                f'Unknown update package type(s) in exchanges:  {unknown}; '
                f'known types are {sorted(PACKAGE_TYPES)}'
            )
        selected = set(models)
        outbound: dict[ModelType, set[type[UpdatePackage]]] = {}
        excluded: list[str] = []
        for name in dict.fromkeys(exchanges):
            package_type = PACKAGE_TYPES[name]
            # receivers is a dataclass field whose default sits on the class; pyrefly sees the
            # base's abstract property instead
            ends = {package_type.sender, *package_type.receivers}  # pyrefly: ignore[not-iterable]
            absent = ends - selected
            if absent:
                excluded.append(f'{name} (needs {sorted(m.value for m in absent)})')
                continue
            outbound.setdefault(package_type.sender, set()).add(package_type)
        if excluded:
            logger.warning(
                'Exchanges left out of this run, as models they connect are not selected:  %s',
                '; '.join(excluded),
            )
        spec = cls(
            outbound={sender: frozenset(types) for sender, types in outbound.items()},
            crosswalks=CrosswalkSet.for_models(selected),
        )
        logger.info(
            'Exchanges for this run:  %s',
            sorted(t.__name__ for t in spec.expected) or 'none',
        )
        return spec

    # TODO:  `expected` is the basis for checking each iteration's packages -- an expected package
    #        missing, or one not expected.  How that feeds route_updates and the run/iteration
    #        status is not yet decided; nothing checks against it yet.
    @property
    def expected(self) -> frozenset[type[UpdatePackage]]:
        """Every package type this run should produce, across all senders."""
        return frozenset(t for types in self.outbound.values() for t in types)

    def outbound_for(self, model: ModelType) -> frozenset[type[UpdatePackage]]:
        """Return the package types ``model`` writes; empty if none."""
        return self.outbound.get(model, frozenset())
