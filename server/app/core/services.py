"""Service boundaries: how one module reaches another without importing it.

Every module publishes ONE gateway, an implementation of its contract in
app/contracts/, and registers it itself when the app starts (the module's own
`register(app)`). A caller asks for a gateway by contract, bound to the caller's
session, and never imports the owning module:

    from app.contracts.catalog import CatalogGateway
    from app.core.services import gateway

    gateway(CatalogGateway, session).require_active_widget(widget_id)

A gateway works inside the caller's session and transaction: it never commits,
rolls back or opens a session of its own.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlmodel import Session

from app.core.errors import AppError

type Factory[T] = Callable[[Session], T]

_factories: dict[type, Callable[[Session], Any]] = {}


class ServiceUnavailable(AppError):
    """No module has registered this contract (yet)."""

    def __init__(self, contract: type) -> None:
        owner = getattr(contract, "OWNER", contract.__name__)
        super().__init__(
            "DEPENDENCY_NOT_AVAILABLE", f"{owner} is not connected yet", 503, {"module": owner}
        )


def provide[T](contract: type[T], factory: Factory[T] | None) -> None:
    """Register the owning module's gateway factory for `contract` (None removes it)."""
    if factory is None:
        _factories.pop(contract, None)
    else:
        _factories[contract] = factory


def gateway[T](contract: type[T], session: Session) -> T:
    """The registered gateway for `contract`, bound to the caller's session."""
    factory = _factories.get(contract)
    if factory is None:
        raise ServiceUnavailable(contract)
    return factory(session)


def optional_gateway[T](contract: type[T], session: Session) -> T | None:
    """Like gateway(), but None when nothing is registered (for optional collaborators)."""
    factory = _factories.get(contract)
    return None if factory is None else factory(session)


@contextmanager
def override[T](contract: type[T], factory: Factory[T] | None) -> Iterator[None]:
    """Swap a gateway for the duration of a block (tests), then restore the original."""
    saved = _factories.get(contract)
    provide(contract, factory)
    try:
        yield
    finally:
        provide(contract, saved)
