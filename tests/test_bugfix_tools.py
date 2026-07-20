from pathlib import Path

from app.services.bugfix_tools import BugFixRepository


def test_parser_extracts_python_exception_and_repository_frames(tmp_path: Path):
    source = tmp_path / "app" / "services" / "orders.py"
    source.parent.mkdir(parents=True)
    source.write_text("def create_order():\n    return None.id\n", encoding="utf-8")
    repository = BugFixRepository(tmp_path)
    log = '''Traceback (most recent call last):
  File "app/services/orders.py", line 2, in create_order
    return None.id
AttributeError: 'NoneType' object has no attribute 'id'
'''

    parsed = repository.parse_python_traceback(log)

    assert parsed["exception_type"] == "AttributeError"
    assert parsed["exception_message"] == "'NoneType' object has no attribute 'id'"
    assert parsed["repository_frames"] == [
        {"path": "app/services/orders.py", "line": 2, "function": "create_order", "in_repository": True}
    ]


def test_context_reader_rejects_paths_outside_repository(tmp_path: Path):
    repository = BugFixRepository(tmp_path)

    context = repository.read_context("../../etc/passwd", 1)

    assert context["found"] is False
    assert "项目根目录" in context["reason"]


def test_context_reader_marks_target_line(tmp_path: Path):
    source = tmp_path / "app.py"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    repository = BugFixRepository(tmp_path)

    context = repository.read_context("app.py", 2, radius=1)

    assert context["found"] is True
    assert ">>    2 | two" in context["snippet"]
