"""Exercise the actual Docker host through the public client, including restart."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from client import ExpenseClient  # noqa: E402


def docker(*arguments):
    subprocess.run(["docker", *arguments], check=True, stdout=subprocess.DEVNULL)


def main():
    image = "determa-expense-service:e2e"
    identity = f"determa-expense-e2e-{os.getpid()}"
    port = os.environ.get("EXPENSE_E2E_PORT", "18087")
    endpoint = f"http://127.0.0.1:{port}/v1/operations"
    if os.environ.get("EXAMPLE_PREBUILT_IMAGE") != "1":
        subprocess.run(["docker", "build", "-t", image, str(ROOT)], check=True)
    docker("volume", "create", identity)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            app = ExpenseClient(
                Path(temporary) / "client.sqlite3", endpoint, "local-container-token"
            )

            def start():
                docker(
                    "run",
                    "-d",
                    "--name",
                    identity,
                    "-p",
                    f"{port}:8088",
                    "-v",
                    f"{identity}:/data",
                    "-e",
                    "EXPENSE_TOKEN=local-container-token",
                    image,
                )
                for _ in range(30):
                    try:
                        assert app.client.discover("expenses")["status"] == "committed"
                        return
                    except (URLError, ConnectionResetError, TimeoutError):
                        time.sleep(1)
                raise RuntimeError("public Docker host did not become ready")

            start()
            app.create("container-expense")
            app.event("container-expense", "submit", "container-submit", {"amount_cents": 500})
            approved = app.event("container-expense", "approve", "container-approve")
            assert app.event("container-expense", "approve", "container-approve") == approved
            receipt = app.client.receipt("container-approve:process")
            assert receipt["value"]["result"]["retention"] == "retained"
            docker("stop", identity)
            docker("rm", identity)
            start()
            assert app.read("container-expense") == approved
            assert (
                app.client.retry("container-approve:process")
                == receipt["value"]["result"]["saved_response"]
            )
            print("Docker public workflow, receipt replay, and native restart checks passed.")
    finally:
        subprocess.run(["docker", "rm", "-f", identity], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "volume", "rm", identity], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
