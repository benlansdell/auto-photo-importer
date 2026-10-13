"""Tests for config loading."""

from pathlib import Path

from photo_importer.config import AppConfig, load_config


def test_default_config():
    cfg = AppConfig()
    assert cfg.camera_name == "Fujifilm"
    assert cfg.verify_hashes is True
    assert "RAF" in cfg.raw_extensions


def test_load_example_yaml(tmp_path: Path):
    src = Path(__file__).resolve().parents[1] / "config.example.yaml"
    dest = tmp_path / "config.yaml"
    dest.write_text(src.read_text())
    cfg = load_config(dest)
    assert cfg.destination == Path("/photos")
    assert cfg.web.port == 8080
    assert cfg.folder_layout == "ymd"


def test_extension_normalization():
    cfg = AppConfig(raw_extensions=[".raf", "jpg"])  # type: ignore[arg-type]
    # jpeg list separate; raw gets uppercased
    assert cfg.raw_extensions == ["RAF", "JPG"]
