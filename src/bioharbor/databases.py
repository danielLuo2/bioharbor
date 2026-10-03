"""Reference sequence databases (MMseqs2 format) under $BIOHARBOR_HOME/databases."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InputValidationError, ToolExecutionError, ToolUnavailableError

# name -> MMseqs2 `databases` source name, plus info shown to users and agents.
KNOWN_DATABASES: dict[str, dict[str, str]] = {
    "swissprot": {
        "source": "UniProtKB/Swiss-Prot",
        "description": "Reviewed UniProt proteins (~570k). Small, high-quality annotations.",
        "size": "~0.5 GB",
    },
    "pdb": {
        "source": "PDB",
        "description": "Sequences of experimentally solved structures in the PDB.",
        "size": "~0.3 GB",
    },
    "uniref50": {
        "source": "UniRef50",
        "description": "UniProt clustered at 50% identity; best for remote homologs.",
        "size": "tens of GB",
    },
}

INSTALL_MMSEQS = (
    "install MMseqs2 (`conda install -c conda-forge -c bioconda mmseqs2`, or a static "
    "binary from https://github.com/soedinglab/MMseqs2/releases) and re-run `bioharbor doctor`"
)


@dataclass(frozen=True)
class Database:
    name: str
    prefix: Path  # the MMseqs2 database path prefix
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, **self.meta}


def db_root(home: Path) -> Path:
    return home / "databases"


def _validate_name(name: str) -> None:
    if not name.replace("_", "").replace("-", "").isalnum():
        raise InputValidationError(
            f"invalid database name {name!r}", hint="use letters, digits, -_"
        )


def list_databases(home: Path) -> list[Database]:
    root = db_root(home)
    if not root.is_dir():
        return []
    dbs = []
    for meta_file in sorted(root.glob("*/meta.json")):
        name = meta_file.parent.name
        dbs.append(Database(name, meta_file.parent / name, json.loads(meta_file.read_text())))
    return dbs


def get_database(home: Path, name: str) -> Database:
    _validate_name(name)
    for db in list_databases(home):
        if db.name == name:
            return db
    installed = [d.name for d in list_databases(home)]
    known = f" (known: {', '.join(KNOWN_DATABASES)})" if name not in KNOWN_DATABASES else ""
    raise ToolUnavailableError(
        f"database {name!r} is not installed"
        + (f"; installed: {', '.join(installed)}" if installed else ""),
        hint=f"ask the user to run `bioharbor setup-db {name}`{known}",
    )


def setup_database(
    home: Path,
    name: str,
    mmseqs: str,
    *,
    from_fasta: Path | None = None,
    threads: int = 4,
    run: Any = None,
) -> Database:
    """Download (or build from FASTA) a database. `run(cmd)` executes a command list."""
    _validate_name(name)
    if from_fasta is None and name not in KNOWN_DATABASES:
        raise InputValidationError(
            f"unknown database {name!r}",
            hint=f"choose one of {', '.join(KNOWN_DATABASES)} or pass --from-fasta",
        )
    run = run or (lambda cmd: subprocess.run(cmd, check=True))
    target = db_root(home) / name
    staging = db_root(home) / f".{name}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    prefix = staging / name
    tmp = staging / "tmp"
    try:
        if from_fasta is not None:
            if not from_fasta.is_file():
                raise InputValidationError(f"FASTA file not found: {from_fasta}")
            run([mmseqs, "createdb", str(from_fasta), str(prefix)])
            source = f"fasta:{from_fasta.name}"
        else:
            source = KNOWN_DATABASES[name]["source"]
            run([mmseqs, "databases", source, str(prefix), str(tmp), "--threads", str(threads)])
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ToolExecutionError(
            f"building database {name!r} failed (mmseqs exit code {exc.returncode})",
            hint="check network access to ftp.uniprot.org / ftp.expasy.org, or download "
            "the FASTA yourself and use --from-fasta",
        ) from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(tmp, ignore_errors=True)

    index = prefix.with_name(prefix.name + ".index")
    n_seqs = sum(1 for _ in index.open()) if index.exists() else None
    version_file = prefix.with_name(prefix.name + ".version")
    meta = {
        "source": source,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_sequences": n_seqs,
        "source_version": version_file.read_text().strip() if version_file.exists() else None,
    }
    (staging / "meta.json").write_text(json.dumps(meta, indent=2))
    # Swap in atomically so a failed download never leaves a half-built database in use.
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    return Database(name, target / name, meta)
