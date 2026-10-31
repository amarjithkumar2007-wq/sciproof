"""``sciproof inspect``: the readable summary covers every recorded manifest shape."""

from __future__ import annotations

import json

from sciproof.inspect_manifest import describe


def _captured(project):
    """A real captured manifest as a dict, ready for targeted edits."""
    project.capture()
    return json.loads(project.manifest.read_text())


def test_recorded_environment_variables_are_listed(project):
    manifest = _captured(project)
    manifest["environment"]["env_vars"] = {"CUDA_VISIBLE_DEVICES": "0", "OMP_NUM_THREADS": "4"}
    text = describe(manifest)
    assert "variables:   CUDA_VISIBLE_DEVICES=0, OMP_NUM_THREADS=4" in text


def test_a_manifest_without_git_metadata_says_not_recorded(project):
    manifest = _captured(project)
    manifest["git"] = None  # captured outside a git repository
    assert "git:         not recorded" in describe(manifest)


def test_a_manifest_without_outputs_says_none(project):
    manifest = _captured(project)
    manifest["outputs"] = []
    assert "Outputs (0):\n  (none)" in describe(manifest)
