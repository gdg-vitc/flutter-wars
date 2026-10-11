"""Module K server-side CLI. Run ON THE SERVER (or locally against a DB you control):

    python -m app.modules.admin.cli add-owner --email lead@gdg.example --name "Event Lead"
    python -m app.modules.admin.cli list

There is deliberately NO HTTP route that creates the first OWNER: if there were, whoever
called it first would own the event. After the first owner exists, use /admin/organizers.
"""

import argparse
import sys

from sqlmodel import Session

from app.core.db import get_engine
from app.core.errors import AppError
from app.modules.admin import repository as repo
from app.modules.admin import service
from app.modules.admin.schemas import normalize_email


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules.admin.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add-owner", help="create an OWNER organizer (bootstrap)")
    add.add_argument("--email", required=True)
    add.add_argument("--name", required=True)
    sub.add_parser("list", help="list organizers")
    args = parser.parse_args(argv)

    with Session(get_engine()) as s:
        if args.cmd == "add-owner":
            try:
                email = normalize_email(args.email)
            except ValueError:
                print("error: invalid email", file=sys.stderr)
                return 2
            try:
                org = service.bootstrap_owner(s, email=email, display_name=args.name)
            except AppError as e:
                print(f"error: {e.code}: {e.message}", file=sys.stderr)
                return 1
            s.commit()
            print(f"created OWNER {org.email} ({org.id})")
        else:
            for o in repo.list_organizers(s):
                print(f"{o.email:40} {o.role:9} {'active' if o.active else 'INACTIVE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
