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


class SetupApplyHarness(GatewaySlashCommandsMixin):
    def __init__(self):
        self.evicted: list[str] = []

    def _session_key_for_source(self, source: SessionSource) -> str:
        return "session-key"

    def _evict_cached_agent(self, session_key: str) -> None:
        self.evicted.append(session_key)


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


def _event(
    text: str,
    *,
    thread_id: str | None = "1781871116.838719",
    chat_id: str = "C0BB6UUEQHM",
    chat_name: str | None = None,
) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.SLACK,
            chat_id=chat_id,
            chat_name=chat_name,
            chat_type="group",
            user_id="U123",
            guild_id="T123",
            thread_id=thread_id,
        ),
    )


@pytest.mark.asyncio
async def test_setup_adds_free_response_channel_and_creates_channel_card(hermes_home):
    (hermes_home / "config.yaml").write_text(
        "slack:\n  free_response_channels: COLD\n",
        encoding="utf-8",
    )

    result = await SubjectHarness()._handle_setup_command(
        _event("/setup hermes-setup", thread_id="1781871116.838719")
    )

    assert "Slack channel setup complete" in result
    assert "`C0BB6UUEQHM` is added" in result
    assert "Restart required" in result
    config_text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert "free_response_channels:" in config_text
    assert "  - COLD" in config_text
    assert "  - C0BB6UUEQHM" in config_text
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "MAIN.md"
    assert card.read_text(encoding="utf-8") == "# hermes-setup\n\nThis channel is for hermes-setup.\n"


@pytest.mark.asyncio
async def test_setup_applies_subject_get_by_evicting_cached_agent(hermes_home):
    harness = SetupApplyHarness()

    result = await harness._handle_setup_command(
        _event("/setup hermes-setup", thread_id="1781871116.838719")
    )

    assert "Applied updated channel context from `/subject get`" in result
    assert harness.evicted == ["session-key"]


@pytest.mark.asyncio
async def test_setup_requires_channel_name_argument(hermes_home):
    (hermes_home / "config.yaml").write_text(
        "slack:\n  free_response_channels: []\n",
        encoding="utf-8",
    )

    result = await SubjectHarness()._handle_setup_command(
        _event("/setup", thread_id=None, chat_id="C0BD7ABQWTE")
    )

    assert result == "Usage: /setup <channel name> [type:<scope-type>] [gh:<urn>|github:<urn>] [path:<path>|dir:<path>]"
    config_text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert "C0BD7ABQWTE" not in config_text
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BD7ABQWTE" / "MAIN.md"
    assert not card.exists()


@pytest.mark.asyncio
async def test_setup_errors_on_unknown_argument(hermes_home):
    result = await SubjectHarness()._handle_setup_command(
        _event("/setup hermes-setup nope:value", thread_id=None, chat_id="C123")
    )

    assert result == "Unknown /setup argument `nope:value`. Supported arguments: type:<scope-type>, gh:<urn>, github:<urn>, path:<path>, dir:<path>."
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C123" / "MAIN.md"
    assert not card.exists()


@pytest.mark.asyncio
async def test_setup_arguments_are_written_to_frontmatter(hermes_home):
    result = await SubjectHarness()._handle_setup_command(
        _event(
            "/setup ticket-context type:jira-ticket github:codexp/hermes-agent dir:/home/ewe/.hermes/hermes-agent",
            thread_id=None,
            chat_id="C123",
        )
    )

    assert "scope_type: `jira-ticket`" in result
    assert "gh:codexp/hermes-agent, path:/home/ewe/.hermes/hermes-agent" in result
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C123" / "MAIN.md"
    assert card.read_text(encoding="utf-8") == (
        "---\n"
        "scope_type: jira-ticket\n"
        "resources:\n"
        "  - gh:codexp/hermes-agent\n"
        "  - path:/home/ewe/.hermes/hermes-agent\n"
        "---\n\n"
        "# ticket-context\n\n"
        "This channel is for ticket-context.\n"
    )


@pytest.mark.asyncio
async def test_setup_infers_shop_scope_and_preserves_existing_resources(hermes_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    shop = tmp_path / "devel" / "falke-b2b-shop"
    shop.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=shop, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:even-on-sunday/falke-b2b-shop.git"],
        cwd=shop,
        check=True,
    )
    script = hermes_home / "gateway-context" / "script" / "context"
    existing = "---\nresources:\n  - jira:FB2B\n---\n\n# Old title\n"
    subprocess.run(
        [str(script), "set", "slack", "T123", "C123SHOP", "--", existing],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"HERMES_HOME": str(hermes_home)},
    )

    result = await SubjectHarness()._handle_setup_command(
        _event("/setup falke-b2b-shop", thread_id=None, chat_id="C123SHOP")
    )

    assert "scope_type: `eos-shop`" in result
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C123SHOP" / "MAIN.md"
    text = card.read_text(encoding="utf-8")
    assert "scope_type: eos-shop" in text
    assert "  - jira:FB2B" in text
    assert f"  - path:{shop}" in text
    assert "  - gh:even-on-sunday/falke-b2b-shop" in text
    assert "# falke-b2b-shop development" in text


@pytest.mark.asyncio
async def test_subject_set_jira_key_creates_thread_card(hermes_home):
    result = await SubjectHarness()._handle_subject_command(_event("/subject set MODS-12345"))

    expected = "```md\n---\nresources:\n  - jira:MODS-12345\n---\n\n# MODS-12345\n```"
    assert result == expected
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    assert card.read_text() == "---\nresources:\n  - jira:MODS-12345\n---\n\n# MODS-12345\n"


@pytest.mark.asyncio
async def test_subject_set_typed_jira_resource_uses_issue_key_title(hermes_home):
    result = await SubjectHarness()._handle_subject_command(_event("/subject set jira:FB2B-1812"))

    assert result == "```md\n---\nresources:\n  - jira:FB2B-1812\n---\n\n# FB2B-1812\n```"


@pytest.mark.asyncio
async def test_subject_set_extracts_inline_resources(hermes_home):
    result = await SubjectHarness()._handle_subject_command(
        _event("/subject set FALKE B2B Shop development channel home:~/devel/falke-b2b-shop/ jira:https://evenonsunday.atlassian.net/jira/software/c/projects/FB2B")
    )

    expected = (
        "```md\n"
        "---\n"
        "resources:\n"
        "  - home:~/devel/falke-b2b-shop/\n"
        "  - jira:https://evenonsunday.atlassian.net/jira/software/c/projects/FB2B\n"
        "---\n\n"
        "# FALKE B2B Shop development channel\n"
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
async def test_subject_get_missing_context_returns_message_not_empty_fence(hermes_home):
    result = await SubjectHarness()._handle_subject_command(_event("/subject get"))
    scoped = await SubjectHarness()._handle_subject_command(_event("/subject get --scope"))

    assert result == "Context file not found"
    assert scoped == "Context file not found"


@pytest.mark.asyncio
async def test_subject_add_places_and_deduplicates_resources(hermes_home):
    harness = SubjectHarness()
    await harness._handle_subject_command(_event("/subject set MODS-12345"))

    await harness._handle_subject_command(_event("/subject add path:~/devel/oui-b2c-shop"))
    result = await harness._handle_subject_command(_event("/subject add path:~/devel/oui-b2c-shop"))

    assert result == (
        "```md\n"
        "---\n"
        "resources:\n"
        "  - jira:MODS-12345\n"
        "  - path:~/devel/oui-b2c-shop\n"
        "---\n\n"
        "# MODS-12345\n"
        "```"
    )


@pytest.mark.asyncio
async def test_subject_add_skill_formats_skills_block_before_resources(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        "# Seidensticker B2C Shop Development\n\n"
        "Resources:\n"
        "  - jira:<http://evenonsunday.atlassian.net/browse/SEID|evenonsunday.atlassian.net/browse/SEID>\n"
        "  - gh:even-on-sunday/seidensticker-b2c-shop\n"
        "  - path:/home/ewe/devel/seidensticker-b2c-shop\n",
        encoding="utf-8",
    )

    result = await harness._handle_subject_command(_event("/subject add skill:`eos-shop-platform`"))

    assert result == (
        "```md\n"
        "---\n"
        "skills:\n"
        "  - eos-shop-platform\n"
        "resources:\n"
        "  - jira:<http://evenonsunday.atlassian.net/browse/SEID|evenonsunday.atlassian.net/browse/SEID>\n"
        "  - gh:even-on-sunday/seidensticker-b2c-shop\n"
        "  - path:/home/ewe/devel/seidensticker-b2c-shop\n"
        "---\n\n"
        "# Seidensticker B2C Shop Development\n"
        "```"
    )
    assert "`" not in card.read_text()


@pytest.mark.asyncio
async def test_subject_add_skill_colon_migrates_legacy_inline_skill(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        "# Seidensticker B2C Shop Development\n"
        "skill:`eos-shop-platform`\n\n"
        "Resources:\n"
        "  - jira:evenonsunday.atlassian.net/browse/SEID\n"
        "  - gh:even-on-sunday/seidensticker-b2c-shop\n"
        "  - path:/home/ewe/devel/seidensticker-b2c-shop\n",
        encoding="utf-8",
    )

    result = await harness._handle_subject_command(_event("/subject add skill:eos-shop-platform"))

    assert result == (
        "```md\n"
        "---\n"
        "skills:\n"
        "  - eos-shop-platform\n"
        "resources:\n"
        "  - jira:evenonsunday.atlassian.net/browse/SEID\n"
        "  - gh:even-on-sunday/seidensticker-b2c-shop\n"
        "  - path:/home/ewe/devel/seidensticker-b2c-shop\n"
        "---\n\n"
        "# Seidensticker B2C Shop Development\n"
        "```"
    )


@pytest.mark.asyncio
async def test_subject_unknown_subcommand_usage_mentions_remove_and_unset(hermes_home):
    result = await SubjectHarness()._handle_subject_command(_event("/subject nope"))

    assert result == (
        "Usage: /subject [get [--scope] | set <description> | update <instruction> | "
        "add {type}:{value} | remove {type}:{value} | unset {type}:{value} | clear]"
    )


@pytest.mark.asyncio
async def test_subject_clear_deletes_current_thread_card(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    card.parent.mkdir(parents=True)
    card.write_text("# Thread card\n", encoding="utf-8")
    channel_card = card.parent / "MAIN.md"
    channel_card.write_text("# Channel card\n", encoding="utf-8")

    result = await harness._handle_subject_command(_event("/subject clear"))

    assert result == "Subject card cleared."
    assert not card.exists()
    assert channel_card.exists()


@pytest.mark.asyncio
async def test_subject_clear_deletes_channel_card_from_channel_scope(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "MAIN.md"
    card.parent.mkdir(parents=True)
    card.write_text("# Channel card\n", encoding="utf-8")

    result = await harness._handle_subject_command(_event("/subject clear", thread_id=None))

    assert result == "Subject card cleared."
    assert not card.exists()


@pytest.mark.asyncio
async def test_subject_clear_missing_card_is_noop(hermes_home):
    result = await SubjectHarness()._handle_subject_command(_event("/subject clear"))

    assert result == "No subject card exists for this scope."


@pytest.mark.asyncio
async def test_subject_remove_skill_drops_empty_skills_block(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        "# Hermes Setup\n\nSkills:\n  - hermes-agent\n\nResources:\n  - path:/home/ewe/.hermes/hermes-agent\n",
        encoding="utf-8",
    )

    result = await harness._handle_subject_command(_event("/subject remove skill:hermes-agent"))

    assert result == (
        "```md\n"
        "---\n"
        "resources:\n"
        "  - path:/home/ewe/.hermes/hermes-agent\n"
        "---\n\n"
        "# Hermes Setup\n"
        "```"
    )
    assert card.read_text() == "---\nresources:\n  - path:/home/ewe/.hermes/hermes-agent\n---\n\n# Hermes Setup\n"


@pytest.mark.asyncio
async def test_subject_unset_resource_drops_empty_resources_block(hermes_home):
    harness = SubjectHarness()
    card = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM" / "1781871116.838719.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        "# Hermes Setup\n\nResources:\n  - path:/home/ewe/.hermes/hermes-agent\n",
        encoding="utf-8",
    )

    result = await harness._handle_subject_command(_event("/subject unset path:/home/ewe/.hermes/hermes-agent"))

    assert result == "```md\n# Hermes Setup\n```"
    assert card.read_text() == "# Hermes Setup\n"


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
        "---\n"
        "skills:\n"
        "  - eos-shop-platform\n"
        "tools:\n"
        "  - mcp:phpstorm\n"
        "resources:\n"
        "  - jira:MODS-12345\n"
        "---\n\n"
        "# Better subject\n"
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
        "---\n"
        "resources:\n"
        "  - jira:MODS-1\n"
        "  - gh:NousResearch/hermes-agent\n"
        "  - home:~/devel/hermes-agent\n"
        "  - obsidian:Hermes/Hermes Gateway Context Cards\n"
        "  - url:https://hermes-agent.nousresearch.com/docs\n"
        "---\n\n"
        "# Existing\n"
        "Body line after old resources\n"
        "```"
    )


@pytest.mark.asyncio
async def test_subject_get_migrates_legacy_anchor_label(hermes_home):
    base = hermes_home / "gateway-context" / "slack" / "T123" / "channel" / "C0BB6UUEQHM"
    base.mkdir(parents=True)
    (base / "1781871116.838719.md").write_text("# Legacy\n\nAnchor:\n  - jira:MODS-1\n", encoding="utf-8")

    result = await SubjectHarness()._handle_subject_command(_event("/subject get --scope"))

    assert result == "```md\n---\nresources:\n  - jira:MODS-1\n---\n\n# Legacy\n```"


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

    assert result == "```md\n---\nresources:\n  - path:/home/ewe/devel/shop\n---\n\n# Existing\n```"
    assert card.read_text() == "---\nresources:\n  - path:/home/ewe/devel/shop\n---\n\n# Existing\n"
