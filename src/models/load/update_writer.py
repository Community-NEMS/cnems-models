"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Update package writer for the load model -- a shell, as it sends no package yet.
"""

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from src.common.update_package import UpdatePackage, UpdatePackageWriter

if TYPE_CHECKING:
    # sequencer imports this module, so the model is named by string below
    from src.models.load.sequencer import LoadProfileModel


class LoadUpdateWriter(UpdatePackageWriter['LoadProfileModel']):
    """Writes the load model's outbound packages:  none yet."""

    @property
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[LoadProfileModel], UpdatePackage]]:
        """No package types are supported yet, so a run cannot ask this writer for any."""
        return {}
