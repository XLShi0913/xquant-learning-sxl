"""Execute q4/q5 locally and preserve the pre-run artifacts in scoped ZIPs."""
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import nbformat
from nbclient import NotebookClient


ROOT = Path(__file__).resolve().parents[1]


def verify(chapter, name):
    path = ROOT / chapter / name
    output = ROOT / chapter / "results"
    archive = output / f"before-cost-model-{datetime.now():%Y%m%d-%H%M%S}.zip"
    if output.is_dir():
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            bundle.write(path, arcname=name)
            for artifact in output.iterdir():
                if artifact.is_file() and artifact != archive and artifact.suffix != ".zip":
                    bundle.write(artifact, arcname=f"results/{artifact.name}")
    notebook = nbformat.read(path, as_version=4)
    client = NotebookClient(notebook, timeout=600, kernel_name="python3",
                            resources={"metadata": {"path": str(ROOT / chapter)}})
    client.execute()
    nbformat.write(notebook, path)  # Execution counts/outputs: generated verification artifact.
    print(f"{chapter}: all {len(notebook.cells)} cells executed; previous artifacts: {archive}", flush=True)


if __name__ == "__main__":
    for chapter, name in [("q4", "when-to-trade.ipynb"), ("q5", "how-to-validate.ipynb")]:
        verify(chapter, name)
