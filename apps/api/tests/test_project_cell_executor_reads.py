from pathlib import Path

import pytest

from yleum_api.services import project_cell_executor as executor

SDK = "src/lib/omnia/integration-client.ts"


def test_grep_exact_file_does_not_search_sibling_or_descendant_names():
    files = {SDK: "export function target() {}\n", SDK + ".bak": "target backup\n",
             SDK + "/nested": "target nested\n"}
    assert executor._grep(files, pattern="target", path=SDK) == (
        SDK + ":1:export function target() {}"
    )


def test_grep_directory_stays_recursive_and_root_includes_all_files():
    files = {"src/a.ts": "match\n", "src/nested/b.ts": "match\n", "src-other/c.ts": "match\n"}
    assert executor._grep(files, pattern="match", path="./src") == (
        "src/a.ts:1:match\nsrc/nested/b.ts:1:match"
    )
    assert "src-other/c.ts" in executor._grep(files, pattern="match", path=".")


@pytest.mark.parametrize("path", ["../secret", "src/../../secret", "/etc/passwd"])
def test_grep_and_read_ranges_reject_escaping_paths(path):
    with pytest.raises(ValueError, match="unsafe path"):
        executor._grep({"secret": "value"}, pattern="value", path=path)
    with pytest.raises(ValueError, match="unsafe path"):
        executor._read_file({"secret": "value"}, path, {"start_line": 1})


def test_late_managed_sdk_signature_can_be_read_in_a_small_bounded_window():
    repo = Path(__file__).resolve().parents[3]
    content = (repo / "apps/orchestrator/templates/max-miniapp-nextjs" / SDK).read_text(
        encoding="utf-8",
    )
    signature = next(i for i, line in enumerate(content.splitlines(), 1)
                     if "export async function updateMaxAction" in line)
    result = executor._read_file({SDK: content}, SDK, {
        "start_line": signature, "end_line": signature + 8,
    })
    assert result["ok"] is True
    assert "expectedRevision: string" in result["content"]
    assert '"If-Match": expectedRevision' in result["content"]
    assert len(result["content"]) < 6000
    assert result["read_range"] == {
        "start_line": signature, "end_line": signature + 8,
        "total_lines": len(content.splitlines()), "truncated": False,
    }


def test_read_range_is_inclusive_preserves_line_endings_and_clamps_end():
    content = "first\r\nsecond\nlast"
    result = executor._read_file({"a.ts": content}, "./a.ts", {
        "start_line": 2, "end_line": 99,
    })
    assert result["content"] == "second\nlast"
    assert result["read_range"] == {
        "start_line": 2, "end_line": 3, "total_lines": 3, "truncated": False,
    }
    assert executor._read_file({"a.ts": content}, "a.ts", {"end_line": 1})["content"] == "first\r\n"


@pytest.mark.parametrize("args", [
    {"start_line": 0}, {"start_line": -1}, {"start_line": True}, {"start_line": "2"},
    {"end_line": False}, {"end_line": None}, {"start_line": 2, "end_line": 1},
    {"start_line": 4},
])
def test_read_invalid_ranges_fail_without_returning_a_different_slice(args):
    with pytest.raises(ValueError, match="read_file range"):
        executor._read_file({"a.ts": "one\ntwo\nthree\n"}, "a.ts", args)


def test_default_and_selected_reads_keep_existing_char_cap_and_mark_truncation():
    content = "x" * (executor._MAX_READ_CHARS + 10) + "\nlast\n"
    for args in ({}, {"start_line": 1, "end_line": 1}):
        result = executor._read_file({"a.ts": content}, "a.ts", args)
        assert result["content"].startswith("x" * executor._MAX_READ_CHARS)
        assert "[truncated " in result["content"]
        assert result["read_range"]["truncated"] is True
        assert "last" not in result["content"]
    assert executor._read_file({"a.ts": ""}, "a.ts", {}) == {
        "ok": True, "content": "", "read_range": {
            "start_line": 1, "end_line": 0, "total_lines": 0, "truncated": False,
        },
    }
    assert executor._read_file({}, "absent.ts", {}) == {
        "ok": False, "error": "not found: absent.ts",
    }
