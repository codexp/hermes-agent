from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from gateway.slash_commands import GatewaySlashCommandsMixin


class SubjectHarness(GatewaySlashCommandsMixin):
    pass


class SubjectPreprocessHarness(GatewaySlashCommandsMixin):
    def __init__(self, output: str):
        self._subject_preprocess_override = lambda description, fallback: output


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    script_dir = home / "gateway-context" / "script"
    script_dir.mkdir(parents=True)
    src = Path.home() / ".hermes" / "gateway-context" / "script" / "context"
    dst = script_dir / "context"
    shutil.copy2(src, dst)
    dst.chmod(0o755)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_GATEWAY_CONTEXT_HOME", raising=False)
    yield home


def _event(text: str, *, thread_id: str | None = "1781871116.838719") -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.SLACK,
            chat_id="C0BB6UUEQHM",
            chat_type="group",
            user_id="U123",
            guild_id="T123",
            thread_id=thread_id,
        ),
    )


@pytest.mark.asyncio
async def test_subject_set_jira_key_creates_thread_card(hermes_home):
    result = await SubjectHarness()._handle_subject_command(_event("/subject set MODS-12345"))

    assert result == "```md\n# MODS-12345\n\nResources:\n  - jira:MODS-12345\n```"
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    assert card.read_text() == "# MODS-12345\n\nResources:\n  - jira:MODS-12345\n"


@pytest.mark.asyncio
async def test_subject_set_typed_jira_resource_uses_issue_key_title(hermes_home):
    result = await SubjectHarness()._handle_subject_command(_event("/subject set jira:FB2B-1812"))

    assert result == "```md\n# FB2B-1812\n\nResources:\n  - jira:FB2B-1812\n```"


@pytest.mark.asyncio
async def test_subject_set_extracts_inline_resources(hermes_home):
    result = await SubjectHarness()._handle_subject_command(
        _event("/subject set FALKE B2B Shop development channel home:~/devel/falke-b2b-shop/ jira:https://evenonsunday.atlassian.net/jira/software/c/projects/FB2B")
    )

    expected = (
        "```md\n"
        "# FALKE B2B Shop development channel\n\n"
        "Resources:\n"
        "  - home:~/devel/falke-b2b-shop/\n"
        "  - jira:https://evenonsunday.atlassian.net/jira/software/c/projects/FB2B\n"
        "```"
    )
    assert result == expected


@pytest.mark.asyncio
async def test_subject_set_uses_agent_preprocessed_card(hermes_home):
    harness = SubjectPreprocessHarness("# Hermes Setup\n")

    result = await harness._handle_subject_command(_event("/subject set Hemes Setup", thread_id=None))

    assert result == "```md\n# Hermes Setup\n```"
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "MAIN.md"
    assert card.read_text() == "# Hermes Setup\n"


@pytest.mark.asyncio
async def test_subject_set_rejects_unsafe_agent_preprocessed_card(hermes_home):
    harness = SubjectPreprocessHarness("<!-- bad -->\n# Changed\n")

    result = await harness._handle_subject_command(_event("/subject set Hemes Setup", thread_id=None))

    assert result == "```md\n# Hemes Setup\n```"


@pytest.mark.asyncio
async def test_subject_without_workspace_uses_default_not_channel_id(hermes_home):
    event = MessageEvent(
        text="/subject set MODS-12345",
        source=SessionSource(
            platform=Platform.SLACK,
            chat_id="C0BB6UUEQHM",
            chat_type="group",
            user_id="U123",
            thread_id="1781871116.838719",
        ),
    )

    await SubjectHarness()._handle_subject_command(event)

    card = hermes_home / "gateway-context" / "slack" / "_default" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    assert card.exists()
    duplicate_channel_workspace = hermes_home / "gateway-context" / "slack" / "C0BB6UUEQHM"
    assert not duplicate_channel_workspace.exists()


@pytest.mark.asyncio
async def test_subject_channel_scope_uses_main_md(hermes_home):
    await SubjectHarness()._handle_subject_command(_event("/subject set Channel subject", thread_id=None))

    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "MAIN.md"
    assert card.read_text() == "# Channel subject\n"


def test_context_path_accepts_main_md_as_channel_thread_id(hermes_home):
    script = hermes_home / "gateway-context" / "script" / "context"

    proc = subprocess.run(
        [str(script), "path", "slack", "T123", "C0BB6UUEQHM", "MAIN.md"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("/slack/T123/channel/C0BB6UUEQHM/MAIN.md")
    assert not proc.stdout.strip().endswith("MAIN.md.md")


@pytest.mark.asyncio
async def test_subject_get_defaults_to_resolved_bundle_and_scope_is_local(hermes_home):
    base = hermes_home / "gateway-context"
    (base / "slack" / "T123" / "channel" / "C0BB6UUEQHM").mkdir(parents=True)
    (base / "global.md").write_text("# Global\n", encoding="utf-8")
    (base / "slack" / "gateway.md").write_text("# Slack gateway\n", encoding="utf-8")
    (base / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "MAIN.md").write_text("# Channel\n", encoding="utf-8")
    (base / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md").write_text("# Thread\n", encoding="utf-8")

    full = await SubjectHarness()._handle_subject_command(_event("/subject get"))
    scoped = await SubjectHarness()._handle_subject_command(_event("/subject get --scope"))

    assert "# Thread" in full
    assert "<!--" not in full
    assert "MAIN.md" not in full
    assert full.index("# Thread") < full.index("# Channel") < full.index("# Global") < full.index("# Slack gateway")
    assert scoped == "```md\n# Thread\n```"


@pytest.mark.asyncio
async def test_subject_add_places_and_deduplicates_resources(hermes_home):
    harness = SubjectHarness()
    await harness._handle_subject_command(_event("/subject set MODS-12345"))

    await harness._handle_subject_command(_event("/subject add path:~/devel/oui-b2c-shop"))
    result = await harness._handle_subject_command(_event("/subject add path:~/devel/oui-b2c-shop"))

    assert result == "```md\n# MODS-12345\n\nResources:\n  - jira:MODS-12345\n  - path:~/devel/oui-b2c-shop\n```"


@pytest.mark.asyncio
async def test_subject_set_preserves_existing_resources(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        "# Old subject\n\nSkills:\n  - eos-shop-platform\nTools:\n  - mcp:phpstorm\n\nResources:\n  - jira:MODS-12345\n",
        encoding="utf-8",
    )

    result = await harness._handle_subject_command(_event("/subject set Better subject"))

    assert result == (
        "```md\n"
        "# Better subject\n\n"
        "Skills:\n"
        "  - eos-shop-platform\n\n"
        "Tools:\n"
        "  - mcp:phpstorm\n\n"
        "Resources:\n"
        "  - jira:MODS-12345\n"
        "```"
    )


@pytest.mark.asyncio
async def test_subject_add_preserves_provided_resource_text_and_keeps_resources_at_end(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    card.parent.mkdir(parents=True)
    card.write_text("# Existing\n\nResources:\n  - jira:MODS-1\n\nBody line after old resources\n", encoding="utf-8")

    await harness._handle_subject_command(_event("/subject add gh:NousResearch/hermes-agent"))
    await harness._handle_subject_command(_event("/subject add home:~/devel/hermes-agent"))
    await harness._handle_subject_command(_event("/subject add obsidian:Hermes/Hermes Gateway Context Cards"))
    result = await harness._handle_subject_command(_event("/subject add url:https://hermes-agent.nousresearch.com/docs"))

    assert result == (
        "```md\n"
        "# Existing\n"
        "Body line after old resources\n\n"
        "Resources:\n"
        "  - jira:MODS-1\n"
        "  - gh:NousResearch/hermes-agent\n"
        "  - home:~/devel/hermes-agent\n"
        "  - obsidian:Hermes/Hermes Gateway Context Cards\n"
        "  - url:https://hermes-agent.nousresearch.com/docs\n"
        "```"
    )


@pytest.mark.asyncio
async def test_subject_get_migrates_legacy_anchor_label(hermes_home):
    base = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM"
    base.mkdir(parents=True)
    (base / "1781871116.838719.md").write_text("# Legacy\n\nAnchor:\n  - jira:MODS-1\n", encoding="utf-8")

    result = await SubjectHarness()._handle_subject_command(_event("/subject get --scope"))

    assert result == "```md\n# Legacy\n\nResources:\n  - jira:MODS-1\n```"


@pytest.mark.asyncio
async def test_subject_update_uses_preprocessed_card_without_restoring_resources(hermes_home):
    harness = SubjectHarness()
    await harness._handle_subject_command(_event("/subject set MODS-12345"))
    harness._subject_preprocess_override = lambda description, fallback: "# Updated subject\n"

    result = await harness._handle_subject_command(_event("/subject update rename to updated subject"))

    assert result == "```md\n# Updated subject\n```"


@pytest.mark.asyncio
async def test_subject_update_can_remove_and_deduplicate_resources(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        "# Existing\n\nResources:\n  - path:/home/ewe/devel/shop\n  - jira:https://jira.example/browse/SB2C\n  - path:/home/ewe/devel/shop\n",
        encoding="utf-8",
    )
    harness._subject_preprocess_override = lambda description, fallback: (
        "# Existing\n\nResources:\n  - path:/home/ewe/devel/shop\n"
    )

    result = await harness._handle_subject_command(
        _event("/subject update remove the jira resource and deduplicate resources")
    )

    assert result == "```md\n# Existing\n\nResources:\n  - path:/home/ewe/devel/shop\n```"
    assert card.read_text() == "# Existing\n\nResources:\n  - path:/home/ewe/devel/shop\n"
