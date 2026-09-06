import sys
from types import SimpleNamespace

import pytest

from scripts.build_runtime import prepare_spec, prepare_sysconfig

SPEC = """
datas, binaries, hiddenimports = [], [], []
collected = collect_all('google.genai')
datas += collected[0]
hiddenimports += collected[2]
a = Analysis(['entry.py'], datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.datas)
"""


def test_runtime_spec_excludes_test_modules_and_origins_before_packaging(tmp_path):
    spec = tmp_path / "runtime.spec"
    spec.write_text(SPEC)
    prepare_spec(spec)
    runtime_modules = ["google.genai", "google.genai.types", "google.genai.testsupport"]
    runtime_data = [
        "google/genai/schema.json", "google/genai/testsupport.json",
        "example.dist-info/METADATA", "example.dist-info/entry_points.txt",
        "example.dist-info/licenses/LICENSE", "example.dist-info/NOTICE",
        "another/tests/runtime.json", "another/data/direct_url.json",
    ]

    def collect_all(name, *, exclude_datas, filter_submodules):
        assert name == "google.genai" and exclude_datas == ["tests"]
        modules = [*runtime_modules, "google.genai.tests", "google.genai.tests.test_client"]
        return [], [], [module for module in modules if filter_submodules(module)]

    def analysis(scripts, *, datas, hiddenimports):
        assert scripts == ["entry.py"]
        names = [*runtime_data, "example.dist-info/direct_url.json",
                 "vendor/other.dist-info/direct_url.json", "google/genai/tests/test_client.py"]
        return SimpleNamespace(
            pure=hiddenimports,
            datas=[(name, "/fixture/" + name, "DATA") for name in names],
        )

    def executable(modules, data):
        assert modules == runtime_modules
        assert [item[0] for item in data] == runtime_data

    exec(compile(spec.read_text(), str(spec), "exec"), {
        "collect_all": collect_all, "Analysis": analysis, "PYZ": lambda modules: modules,
        "EXE": executable,
    })


@pytest.mark.parametrize("statement", [
    "collected = collect_all('google.genai')",
    "a = Analysis(['entry.py'], datas=datas, hiddenimports=hiddenimports)",
])
def test_changed_spec_structure_refuses_an_unfiltered_build(tmp_path, statement):
    spec = tmp_path / "runtime.spec"
    source = SPEC.replace(statement, "pass")
    spec.write_text(source)
    with pytest.raises(RuntimeError, match="expected Google collection or Analysis"):
        prepare_spec(spec)
    assert spec.read_text() == source


def test_sysconfig_hook_relocates_paths_and_preserves_abi_metadata(tmp_path, monkeypatch):
    origin = "/builder/python"
    values = {
        "BINDIR": origin + "/bin",
        "DESTDIRS": origin + "/lib " + origin + "/include",
        "SOABI": "cpython-312-x86_64-linux-gnu",
        "EXT_SUFFIX": ".cpython-312-x86_64-linux-gnu.so",
        "SIZEOF_VOID_P": 8,
        "Py_GIL_DISABLED": 0,
        "ENABLED": True,
        "UNSET": None,
        "FACTOR": 1.5,
    }
    name = "_sysconfigdata__linux_x86_64-linux-gnu"
    hooks = prepare_sysconfig(tmp_path, name, values, origin)
    hook = hooks / "pre_find_module_path" / f"hook-{name}.py"
    namespace = {"__file__": str(hook)}
    exec(compile(hook.read_text(), str(hook), "exec"), namespace)
    api = SimpleNamespace(search_dirs=["/unrelated"])
    namespace["pre_find_module_path"](api)
    assert api.search_dirs == [str(tmp_path / "stdlib")]
    module = tmp_path / "stdlib" / f"{name}.py"
    source = module.read_text()
    assert origin not in source
    for prefix in ("/runtime/first", "/runtime/second"):
        monkeypatch.setattr(sys, "_MEIPASS", prefix, raising=False)
        namespace = {}
        exec(compile(source, str(module), "exec"), namespace)
        assert namespace["build_time_vars"] == {
            key: value.replace(origin, prefix) if isinstance(value, str) else value
            for key, value in values.items()
        }


@pytest.mark.parametrize("values", [{"ABI": []}, {1: "invalid key"}])
def test_sysconfig_generation_refuses_unsupported_metadata(tmp_path, values):
    with pytest.raises(RuntimeError, match="unsupported sysconfig metadata"):
        prepare_sysconfig(tmp_path, "_sysconfigdata_fixture", values, "/builder/python")
    assert not list(tmp_path.iterdir())
