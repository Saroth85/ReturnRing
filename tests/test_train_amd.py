"""Training orchestration must request GPU work and never run hardware comparisons."""

from pathlib import Path

import pytest

from scripts.train_amd import TASKS, training_command


@pytest.mark.parametrize("task", TASKS)
def test_commands_use_gpu_and_separate_output(task: str) -> None:
    command = training_command(task, Path("data"), Path("session-output"), 42)
    assert command[0] == "-m"
    assert "--device" in command
    assert command[command.index("--device") + 1] in ("gpu", "cuda")
    assert "session-output" in command
    assert "bench" not in command
    if task.startswith("graphsage"):
        assert command[command.index("--backend") + 1] == "torch"


def test_unknown_training_task_is_refused() -> None:
    with pytest.raises(ValueError):
        training_command("fake", Path("data"), Path("out"), 42)
