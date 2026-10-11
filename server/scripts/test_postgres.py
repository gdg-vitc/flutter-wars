"""Run all tests on a private temporary PostgreSQL instance; clean up afterward."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    server = Path(__file__).resolve().parents[1]
    initdb = shutil.which("initdb")
    if initdb is None:
        print("Install PostgreSQL and put initdb/pg_ctl/createdb on PATH.", file=sys.stderr)
        return 2
    pg_bin = Path(initdb).resolve().parent
    with tempfile.TemporaryDirectory(prefix="ij-postgres-") as directory:
        directory = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        subprocess.run(
            [initdb, "-D", str(directory / "data"), "--auth=trust", "--no-locale", "-E", "UTF8"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        started = False
        try:
            subprocess.run(
                [
                    str(pg_bin / "pg_ctl"),
                    "-D",
                    str(directory / "data"),
                    "-l",
                    str(directory / "postgres.log"),
                    "-o",
                    f"-p {port} -h 127.0.0.1 -k {directory}",
                    "-w",
                    "start",
                ],
                check=True,
            )
            started = True
            subprocess.run(
                [
                    str(pg_bin / "createdb"),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(port),
                    "flutter_modules_test",
                ],
                check=True,
            )
            environment = dict(
                os.environ,
                TEST_DATABASE_URL=f"postgresql+psycopg://localhost:{port}/flutter_modules_test",
            )
            return subprocess.run(
                [sys.executable, "-m", "pytest", "-q"], cwd=server, env=environment
            ).returncode
        finally:
            if started:
                subprocess.run(
                    [
                        str(pg_bin / "pg_ctl"),
                        "-D",
                        str(directory / "data"),
                        "-m",
                        "fast",
                        "-w",
                        "stop",
                    ],
                    check=True,
                )


if __name__ == "__main__":
    raise SystemExit(main())
