from pathlib import Path

import pytest

import fitbatch
from fitbatch.cli import build_parser, main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"fitbatch {fitbatch.__version__}"


def test_gui_is_default_command() -> None:
    assert build_parser().parse_args([]).command is None
    assert build_parser().parse_args(["gui"]).command == "gui"


def test_info_shows_data_folder(
    capsys: pytest.CaptureFixture[str], isolated_data_dir: Path
) -> None:
    assert main(["info"]) == 0
    assert str(isolated_data_dir) in capsys.readouterr().out


def test_gui_module_imports() -> None:
    from fitbatch.ui import app

    assert callable(app.main)
    assert hasattr(app.FitBatchApp, "render")
