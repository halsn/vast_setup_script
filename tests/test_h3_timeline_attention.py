"""Exercise the managed Director model override before any GPU kernel runs."""

import ast
import copy
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def sampler_class(tmp_path):
    relative = "minimax_h3_finite_segments.py"
    target = tmp_path / relative
    target.write_text("\n" * 893 + (ROOT / "tests/fixtures/timeline_finite_attention.py.txt").read_text(), encoding="utf-8")
    section = (ROOT / "patches/timeline-director/two-phase-checkpoints.patch").read_text().split(
        f"diff --git a/{relative} b/{relative}\n", 1
    )[1].split("diff --git ", 1)[0]
    selected = []
    for hunk in re.split(r"(?=^@@ )", section, flags=re.MULTILINE)[1:]:
        start, count = map(int, re.match(r"@@ -(\d+),(\d+)", hunk).groups())
        if 894 <= start and start + count <= 944:
            selected.append(hunk)
    patch = f"--- a/{relative}\n+++ b/{relative}\n" + "".join(selected)
    result = subprocess.run(["git", "apply", "--recount", "--whitespace=nowarn", "-"], cwd=tmp_path, input=patch, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    source = target.read_text()
    tree = ast.parse(source[source.index("class MiniMaxH3FiniteSegmentSampler"):])
    cls = tree.body[0]
    execute = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "execute")
    # Stop at graph expansion: only the model selection boundary is under test.
    boundary = next(index for index, node in enumerate(execute.body) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "graph" for t in node.targets))
    execute.body = execute.body[:boundary] + [ast.Return(value=ast.Name(id="model", ctx=ast.Load()))]
    namespace = {
        "io": SimpleNamespace(ComfyNode=object),
        "_require_finite_plan": lambda plan: plan,
        "segment_stages": lambda *args: [],
        "checkpoint_status": lambda *args, **kwargs: None,
        "folder_paths": SimpleNamespace(get_output_directory=lambda: tmp_path),
    }
    exec(compile(ast.fix_missing_locations(tree), "managed_director_attention", "exec"), namespace)
    return namespace[cls.name]


@pytest.mark.parametrize("stage", ["full", "preview", "finalize"])
def test_director_clones_model_and_selects_sdpa_for_every_stage(tmp_path, stage):
    cls = sampler_class(tmp_path)
    original = SimpleNamespace(model_options={"transformer_options": {"other_option": 17}})
    original.clone = lambda: SimpleNamespace(model_options=copy.deepcopy(original.model_options))
    result = cls.execute(original, None, None, None, {"segment_count": 3, "second_pass": True}, None, None, 1, True,
                         execution_stage=stage, checkpoint_id="checkpoint", sampling_fingerprint="fingerprint")
    assert result is not original
    options = result.model_options["transformer_options"]
    assert options["other_option"] == 17
    assert options["optimized_attention_override"] is cls._attention_override
    assert original.model_options == {"transformer_options": {"other_option": 17}}


def test_director_override_bypasses_sage_and_preserves_attention_arguments(tmp_path, monkeypatch):
    cls = sampler_class(tmp_path)
    calls = []
    attention = SimpleNamespace(__wrapped__=lambda *args, **kwargs: calls.append((args, kwargs)) or "sdpa-result")
    monkeypatch.setitem(sys.modules, "comfy.ldm.modules.attention", SimpleNamespace(attention_pytorch=attention))
    def sage(*args, **kwargs):
        raise AssertionError("The failing Sage kernel must not be entered")
    result = cls._attention_override(sage, "q", "k", "v", 32, mask=None, skip_reshape=True, transformer_options={"kept": 1})
    assert result == "sdpa-result"
    assert calls == [(("q", "k", "v", 32), {"mask": None, "skip_reshape": True, "transformer_options": {"kept": 1}})]
