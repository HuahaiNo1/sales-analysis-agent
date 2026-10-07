#!/usr/bin/env python3
"""Build the pinned official Contoso generator with an already installed .NET SDK.

Portable alternative to contoso_generate.sh; never installs software or changes
generation parameters. Existing raw sales data is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = "8.0.425"
SOURCE_COMMIT = "eaeb57a9eaa6ad0cdab4fb527552102685434ee0"
SOURCE_URL = "https://github.com/sql-bi/Contoso-Data-Generator-V2.git"


def verify_inputs(data: Path, *, include_assets: bool = False) -> None:
    manifest = json.loads((data / "provenance/source-manifest.json").read_text(encoding="utf-8"))
    files = [(data / "provenance" / name, manifest[name]["sha256"])
             for name in ("generator-config.json", "input-data.xlsx")]
    if include_assets:
        files += [(data / "cache" / item["name"], item["sha256"]) for item in manifest["asset_files"]]
    for path, expected in files:
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError(
                f"Reviewed input changed: {path.name}. Keep generator-config.json as UTF-8/LF; "
                "restore reviewed bytes rather than replacing the recorded checksum."
            )


def patch_build_target(path: Path) -> None:
    source = path.read_text(encoding="utf-8-sig")
    # Build metadata only: preserve the Windows stamp; do not execute cmd syntax on Unix.
    source = source.replace('<Target Name="PreBuild" BeforeTargets="PreBuildEvent">',
                            '<Target Name="PreBuild" BeforeTargets="PreBuildEvent" '
                            'Condition="\'$(OS)\' == \'Windows_NT\'">')
    # Upstream's redirect needs quotes for a Windows checkout whose path contains spaces.
    source = source.replace('&gt;$(ProjectDir)assemblygithash.cs',
                            '&gt;&quot;$(ProjectDir)assemblygithash.cs&quot;')
    path.write_text(source, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-inputs", action="store_true",
                        help="Check reviewed config/workbook bytes only; no network, build, or generation")
    args = parser.parse_args()
    data = Path(os.environ.get("CONTOSO_DATA_DIR", str(ROOT / "data"))).resolve()
    if args.verify_inputs:
        verify_inputs(data)
        print("Reviewed config and workbook hashes match.")
        return
    if (data / "raw/sales.csv").exists():
        raise RuntimeError("Existing data is immutable. Use a fresh CONTOSO_DATA_DIR to regenerate.")
    dotnet = os.environ.get("DOTNET_BIN") or shutil.which("dotnet")
    if not dotnet or not shutil.which("git"):
        raise RuntimeError("Install Git and the official .NET SDK 8.0.425 first; see docs/windows.md.")
    sdks = subprocess.check_output([dotnet, "--list-sdks"], text=True)
    if not any(line.startswith(SDK_VERSION + " ") for line in sdks.splitlines()):
        raise RuntimeError(f"The reviewed .NET SDK {SDK_VERSION} is not installed; see docs/windows.md.")
    work = Path(os.environ.get("CONTOSO_WORK_DIR", str(ROOT / "runtime/contoso-generator-portable"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "cache", "processed", "provenance"):
        (data / name).mkdir(parents=True, exist_ok=True)
    if data != ROOT / "data":
        for name in ("generator-config.json", "input-data.xlsx", "source-manifest.json",
                     "LICENSE-generator.txt", "LICENSE-data.txt", "linux-build.patch"):
            shutil.copyfile(ROOT / "data/provenance" / name, data / "provenance" / name)
    verify_inputs(data)  # Fail on CRLF/input changes before downloading or running the generator.
    env = os.environ.copy()
    env.update(DOTNET_CLI_HOME=str(work / "dotnet-home"), NUGET_PACKAGES=str(work / "nuget"),
               DOTNET_CLI_TELEMETRY_OPTOUT="1")
    (work / "global.json").write_text(
        json.dumps({"sdk": {"version": SDK_VERSION, "rollForward": "disable"}}) + "\n",
        encoding="utf-8", newline="\n",
    )
    source = work / "source"
    if not (source / ".git").exists():
        subprocess.run(["git", "clone", SOURCE_URL, source], check=True, env=env)
    subprocess.run(["git", "-C", source, "checkout", "--detach", SOURCE_COMMIT], check=True, env=env)
    project = source / "DatabaseGenerator/DatabaseGenerator.csproj"
    patch_build_target(project)
    subprocess.run([dotnet, "build", project, "-c", "Release"], cwd=work, env=env, check=True)
    with (data / "provenance/dotnet-info.txt").open("w", encoding="utf-8") as log:
        subprocess.run([dotnet, "--info"], cwd=work, env=env, stdout=log, check=True)
    print("Running official generator; output is saved to data provenance/generation-console.log.")
    with (data / "provenance/generation-console.log").open("w", encoding="utf-8") as log:
        subprocess.run([dotnet, source / "DatabaseGenerator/bin/Release/net8.0/DatabaseGenerator.dll",
                        data / "provenance/generator-config.json", data / "provenance/input-data.xlsx",
                        data / "raw", data / "cache"], cwd=work, env=env, stdout=log,
                       stderr=subprocess.STDOUT, check=True)
    # Upstream can log generation failures and still exit 0; hashes + normalization are mandatory.
    verify_inputs(data, include_assets=True)
    subprocess.run([sys.executable, ROOT / "scripts/contoso_normalize.py", "--data-dir", data], check=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Generation stopped: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
