"""Module registration entrypoint."""

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from app.auction.registration import register as register_routes

    register_routes(app)
