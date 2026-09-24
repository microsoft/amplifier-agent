from pathlib import Path

from amplifier_agent_grader import rubric
import pytest

GRADER_YAML = """
evaluations:
  - name: reply
    steps: Read driver/result.json.
    rubric:
      a: {points: 10, description: first}
      b: {points: 5, description: second}
"""


def test_rubric_pass_score_default(tmp_path: Path) -> None:
    (tmp_path / "grader.yaml").write_text(GRADER_YAML)
    loaded = rubric.load(tmp_path / "grader.yaml")
    assert loaded["pass_score"] == 1.0
    assert loaded["evaluations"][0]["weight"] == 1
    (tmp_path / "grader.yaml").write_text("pass_score: 0.6\n" + GRADER_YAML)
    assert rubric.load(tmp_path / "grader.yaml")["pass_score"] == 0.6
    (tmp_path / "grader.yaml").write_text(GRADER_YAML.replace("points: 5", "points: 0"))
    with pytest.raises(rubric.RubricError, match=r"b\.points must be a positive integer"):
        rubric.load(tmp_path / "grader.yaml")
