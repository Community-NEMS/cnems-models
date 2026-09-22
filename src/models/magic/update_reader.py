"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package reader for the magic model -- a shell, as no package is routed to it.
"""

from functools import singledispatchmethod

from src.common.update_package import UpdatePackage, UpdatePackageReader


class MagicUpdateReader(UpdatePackageReader[None]):
    """Reader for the magic model, which takes no inbound data.

    No package names :attr:`ModelType.MAGIC` as a receiver, so no handlers are registered and any
    package received here was misrouted.
    """

    # pyrefly does not see singledispatchmethod as a descriptor, so flags the override
    @singledispatchmethod
    def apply_package(self, update_package: UpdatePackage, data: None) -> None:  # type: ignore[bad-override]
        """Reject a package; the magic model has no handlers.

        Parameters
        ----------
        update_package : UpdatePackage
            The package received.
        data : None
            The magic model has no loaded data.

        Raises
        ------
        NotImplementedError
            Always, until a handler is registered for the package type.
        """
        raise NotImplementedError(
            f'Missing single dispatch method for type: {type(update_package)}'
        )
