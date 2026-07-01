"""Tests for `karakum build` — the tiered image build and its build-arg plumbing.

Docker is never invoked: `subprocess.run` is captured and every command inspected.
These guard the wiring that has silently regressed before — most notably
`RUST_COMPONENTS` reaching the rust image empty, which dropped `cargo clippy` from
the built image without any error — and the proto toolchain's version plumbing.
"""
from types import SimpleNamespace

from click.testing import CliRunner

from karakum import cli, manifest


# A toolchains.yaml shaped like the shipped seed, exercised through `build`.
_FIXTURE_TC = {
    "node": {"version": "22.11.0", "tools": ["typescript", "pnpm"]},
    "python": {"version": "3.13", "uv_version": "0.5.11"},
    "rust": {
        "version": "1.96.0",
        "components": ["clippy", "rustfmt", "rustc-dev", "llvm-tools"],
        "tools": ["cargo-nextest", "sqlx-cli"],
    },
    "proto": {"protoc": {"version": "28.3"}, "buf": {"version": "1.47.2"}},
}


def _capture_build(monkeypatch, tc):
    """Run `karakum build` with docker + preflight stubbed; return captured argvs."""
    calls = []
    monkeypatch.setattr(cli.preflight, "check_tools", lambda: None)
    monkeypatch.setattr(cli.manifest, "load", lambda p: tc)
    monkeypatch.setattr(cli.os, "chdir", lambda p: None)
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = CliRunner().invoke(cli.main, ["build"])
    assert result.exit_code == 0, result.output
    return calls


def _build_arg(cmd, name):
    """Value of `--build-arg NAME=...` in a docker build argv, or None if absent."""
    for i, tok in enumerate(cmd):
        if tok == "--build-arg" and cmd[i + 1].startswith(f"{name}="):
            return cmd[i + 1].split("=", 1)[1]
    return None


def _build_for_tag(calls, tag):
    """The single `docker build` argv that tags `tag`, or None."""
    return next((cmd for cmd in calls if tag in cmd), None)


# --- rust build-args (the clippy regression) -------------------------------

def test_rust_components_and_tools_reach_build_args(monkeypatch):
    calls = _capture_build(monkeypatch, _FIXTURE_TC)
    rust = _build_for_tag(calls, "karakum-toolchain-rust:latest")
    assert rust is not None
    assert _build_arg(rust, "RUST_VERSION") == "1.96.0"
    # The exact bug that started this: components must arrive space-joined, not empty.
    assert _build_arg(rust, "RUST_COMPONENTS") == "clippy rustfmt rustc-dev llvm-tools"
    assert _build_arg(rust, "RUST_TOOLS") == "cargo-nextest sqlx-cli"


def test_rust_components_empty_string_when_unset(monkeypatch):
    # A config with no components must pass RUST_COMPONENTS="" (the Dockerfile then
    # skips the `rustup component add`) — not the literal "None", and not crash.
    calls = _capture_build(monkeypatch, {"rust": {"version": "1.96.0"}})
    rust = _build_for_tag(calls, "karakum-toolchain-rust:latest")
    assert _build_arg(rust, "RUST_COMPONENTS") == ""
    assert _build_arg(rust, "RUST_TOOLS") == ""


# --- proto build-args (the new nested version shape) -----------------------

def test_proto_versions_reach_build_args(monkeypatch):
    calls = _capture_build(monkeypatch, _FIXTURE_TC)
    proto = _build_for_tag(calls, "karakum-toolchain-proto:latest")
    assert proto is not None
    assert _build_arg(proto, "PROTOC_VERSION") == "28.3"
    assert _build_arg(proto, "BUF_VERSION") == "1.47.2"


# --- tier ordering ---------------------------------------------------------

def test_compose_build_runs_last(monkeypatch):
    # Toolchain images must be built (and tagged) before the compose step that
    # COPYs --from them, or the agent image build can't resolve the stages.
    calls = _capture_build(monkeypatch, _FIXTURE_TC)
    assert calls[-1] == ["docker", "compose", "build"]
    tags = [t for cmd in calls for t in cmd if str(t).startswith("karakum-toolchain-")]
    assert "karakum-toolchain-proto:latest" in tags


# --- structural guard: every toolchain image is declared (and pinned) ------

def test_every_toolchain_dir_is_declared_in_seed():
    """A `containers/toolchain-<name>` dir with no `toolchains.yaml` entry would
    build unpinned/unparameterized — this keeps the two in lockstep."""
    root = manifest.karakum_root()
    tc = manifest.load(root / "examples" / "toolchains.yaml")
    dirs = {
        p.name[len("toolchain-"):]
        for p in (root / "containers").iterdir()
        if p.is_dir() and p.name.startswith("toolchain-")
    }
    assert dirs, "no toolchain-* container dirs found"
    for name in dirs:
        assert name in tc, f"containers/toolchain-{name} has no entry in toolchains.yaml"


def test_shipped_seed_pins_rust_lint_and_proto_tools():
    """Guards the two regressions in this PR's history: clippy/rustfmt present in
    the rust components, and both proto tools carrying a version string."""
    tc = manifest.load(manifest.karakum_root() / "examples" / "toolchains.yaml")
    components = tc["rust"].get("components") or []
    assert "clippy" in components and "rustfmt" in components
    assert isinstance(manifest.get(tc, "proto.protoc.version"), str)
    assert isinstance(manifest.get(tc, "proto.buf.version"), str)
