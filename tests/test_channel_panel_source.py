import ast
from pathlib import Path


def test_channel_panel_set_config_has_no_stale_return():
    source = Path("scope/ui/panels/channel_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    channel_panel = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ChannelPanel"
    )
    set_config = next(
        node for node in channel_panel.body
        if isinstance(node, ast.FunctionDef) and node.name == "set_config"
    )

    assert not any(isinstance(node, ast.Return) for node in ast.walk(set_config))
