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
from client import DocumentClient  # noqa: E402
from worker import DocumentWorker  # noqa: E402


def docker(*arguments):
    subprocess.run(["docker", *arguments], check=True, stdout=subprocess.DEVNULL)


def main():
    image = "determa-document-service:e2e"
    identity = f"determa-document-e2e-{os.getpid()}"
    port = os.environ.get("DOCUMENT_E2E_PORT", "18089")
    endpoint = f"http://127.0.0.1:{port}/v1/operations"
    if os.environ.get("EXAMPLE_PREBUILT_IMAGE") != "1":
        subprocess.run(["docker", "build", "-t", image, str(ROOT)], check=True)
    docker("volume", "create", identity)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            app = DocumentClient(
                Path(temporary) / "client.sqlite3", endpoint, "local-container-token"
            )

            def start():
                docker(
                    "run",
                    "-d",
                    "--name",
                    identity,
                    "-p",
                    f"{port}:8089",
                    "-v",
                    f"{identity}:/data",
                    "-e",
                    "DOCUMENT_TOKEN=local-container-token",
                    image,
                )
                for _ in range(30):
                    try:
                        assert app.client.discover("documents")["status"] == "committed"
                        return
                    except (URLError, ConnectionResetError, TimeoutError):
                        time.sleep(1)
                raise RuntimeError("public Docker host did not become ready")

            start()
            app.create("container-document")
            app.event("container-document", "build", "container-build",
                      {"text": "Container document", "request_id": "container:document"})
            worker_path = Path(temporary) / "worker.sqlite3"
            worker = DocumentWorker(worker_path, app)
            assert worker.drain("container-document") == 1
            assert worker.drain("container-document") == 0
            final = app.read("container-document")
            docker("stop", identity)
            docker("rm", identity)
            start()
            assert app.read("container-document") == final
            reopened = DocumentWorker(worker_path, app,
                                      builder=lambda _: (_ for _ in ()).throw(
                                          AssertionError("saved artifact rebuilt")))
            assert reopened.drain("container-document") == 0
            print("Docker remote workflow, native artifact and durable restart checks passed.")
    finally:
        subprocess.run(["docker", "rm", "-f", identity], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "volume", "rm", identity], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
