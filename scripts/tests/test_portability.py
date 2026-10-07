"""Host-independent checks only; these do not claim a Windows end-to-end run."""

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dev = load_script("dev_windows")
generate = load_script("contoso_generate")


def test_reviewed_inputs_match():
    generate.verify_inputs(ROOT / "data")


def test_crlf_config_is_rejected_without_changing_recorded_hash(tmp_path):
    provenance = tmp_path / "provenance"
    provenance.mkdir()
    for name in ("generator-config.json", "input-data.xlsx", "source-manifest.json"):
        shutil.copyfile(ROOT / "data/provenance" / name, provenance / name)
    config = provenance / "generator-config.json"
    config.write_bytes(config.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(RuntimeError, match="restore reviewed bytes"):
        generate.verify_inputs(tmp_path)


def test_git_windows_checkout_keeps_reviewed_lf_bytes(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git is required to check checkout behavior")
    repo = tmp_path / "repo"
    target = tmp_path / "checkout"
    (repo / "data/provenance").mkdir(parents=True)
    target.mkdir()
    shutil.copyfile(ROOT / ".gitattributes", repo / ".gitattributes")
    source = ROOT / "data/provenance/generator-config.json"
    shutil.copyfile(source, repo / "data/provenance/generator-config.json")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git = ["git", "-C", str(repo), "-c", "core.autocrlf=true"]
    subprocess.run(git + ["add", ".gitattributes", "data/provenance/generator-config.json"], check=True)
    subprocess.run(git + ["checkout-index", "--all", f"--prefix={target.as_posix()}/"], check=True)
    assert (target / "data/provenance/generator-config.json").read_bytes() == source.read_bytes()


def test_build_stamp_patch_only_adds_os_guard_and_path_quotes(tmp_path):
    project = tmp_path / "DatabaseGenerator.csproj"
    project.write_text('<Project><Target Name="PreBuild" BeforeTargets="PreBuildEvent">'
                       '<Exec Command="echo hash &gt;$(ProjectDir)assemblygithash.cs" />'
                       '</Target></Project>', encoding="utf-8")
    generate.patch_build_target(project)
    patched = project.read_text()
    assert 'Condition="\'$(OS)\' == \'Windows_NT\'"' in patched
    assert '&gt;&quot;$(ProjectDir)assemblygithash.cs&quot;' in patched
    generate.patch_build_target(project)
    assert project.read_text() == patched


@pytest.mark.parametrize("already_running", [False, True])
def test_database_cleanup_only_stops_owned_cluster(tmp_path, monkeypatch, already_running):
    data = tmp_path / "pgdata-windows"
    data.mkdir()
    (data / "PG_VERSION").write_text("17\n")
    monkeypatch.setattr(dev, "RUNTIME", tmp_path)
    monkeypatch.setattr(dev, "require_free_port", lambda port: None)
    calls = []

    def run(command, **kwargs):
        calls.append([str(part) for part in command])
        return SimpleNamespace(returncode=0 if already_running or command[-1] != "status" else 3)

    monkeypatch.setattr(dev.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="test failure"):
        with dev.local_database(tmp_path / "PostgreSQL 17/bin", {}, None):
            raise RuntimeError("test failure")
    assert any(command[-1] == "start" for command in calls) is not already_running
    assert any(command[-1] == "stop" for command in calls) is not already_running
    assert all("127.0.0.1" in command for command in calls if "psql.exe" in command[0])


def test_service_exit_fails_health_check_immediately():
    with pytest.raises(RuntimeError, match="service exited"):
        dev.wait_services([SimpleNamespace(poll=lambda: 1)])


def test_existing_generation_is_never_overwritten(tmp_path, monkeypatch):
    (tmp_path / "raw").mkdir()
    sales = tmp_path / "raw/sales.csv"
    sales.write_text("existing snapshot")
    monkeypatch.setenv("CONTOSO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(generate.sys, "argv", ["contoso_generate.py"])
    with pytest.raises(RuntimeError, match="immutable"):
        generate.main()
    assert sales.read_text() == "existing snapshot"
