import json
from pathlib import Path

import pytest

from jev_context.policy import Config


@pytest.mark.parametrize("relative_config_argument", [False, True])
def test_paths_are_relative_to_config_file_not_working_directory(
    tmp_path, monkeypatch, relative_config_argument
):
    project = tmp_path / "relocated project"
    config_dir = project / ".local"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "project.toml"
    config_path.write_text(
        "\n".join(
            [
                'project_root = ".."',
                'data_root = "state"',
                'project_id = "project-portable"',
                "[engine]",
                'state = "shadow"',
                'profile_file = "profiles/model.json"',
                'evaluation_file = "evaluation.json"',
                'evaluation_sha256 = "unchanged"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    argument = config_path.relative_to(tmp_path) if relative_config_argument else config_path

    config = Config.load(argument)

    assert config.project_root == project
    assert config.data_root == config_dir / "state"
    assert config.engine == {
        "state": "shadow",
        "profile_file": str(config_dir / "profiles" / "model.json"),
        "evaluation_file": str(config_dir / "evaluation.json"),
        "evaluation_sha256": "unchanged",
    }


def test_absolute_paths_keep_their_targets(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    config_dir = tmp_path / "separate config"
    config_dir.mkdir()
    data_root = tmp_path / "existing state"
    profile = tmp_path / "model.json"
    evaluation = tmp_path / "evaluation.json"
    config_path = config_dir / "project.toml"
    config_path.write_text(
        "\n".join(
            [
                f"project_root = {json.dumps(str(project))}",
                f"data_root = {json.dumps(str(data_root))}",
                'project_id = "project-absolute"',
                "[engine]",
                'state = "active"',
                f"profile_file = {json.dumps(str(profile))}",
                f"evaluation_file = {json.dumps(str(evaluation))}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    config = Config.load(config_path)

    assert config.project_root == project
    assert config.data_root == data_root
    assert Path(config.engine["profile_file"]) == profile
    assert Path(config.engine["evaluation_file"]) == evaluation


def test_relative_config_without_engine_retains_disabled_default(tmp_path, monkeypatch):
    config_path = tmp_path / "project.toml"
    config_path.write_text(
        'project_root = "."\ndata_root = "state"\nproject_id = "project-default"\n',
        encoding="utf-8",
    )
    other_directory = tmp_path / "other"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)

    config = Config.load(config_path)

    assert config.project_root == tmp_path
    assert config.data_root == tmp_path / "state"
    assert config.engine == {"state": "disabled"}
