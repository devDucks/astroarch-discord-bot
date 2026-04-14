import os
from typing import Optional

import discord
import httpx
from discord import ui
from discord.ext import commands

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_PAT = os.getenv("GITHUB_PAT")

# Package source (where to list packages from)
PACKAGE_OWNER = "devDucks"
PACKAGE_REPO = "astroarch-pkgs"
PACKAGE_DIR = "packages"

# Workflow target (where to trigger the workflow)
WORKFLOW_OWNER = os.getenv("WORKFLOW_OWNER", "devDucks")  # Your repo
WORKFLOW_REPO = os.getenv("WORKFLOW_REPO", "astroarch-pkgs")  # Your repo

# Discord
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
OWNER_ID = os.getenv("DISCORD_OWNER_ID")

# Architectures
ARCHITECTURES = ["aarch64", "x64"]


class PackageSelect(ui.Select):
    def __init__(self, packages):
        options = [
            discord.SelectOption(label=pkg, value=pkg)
            for pkg in packages[:25]  # Discord limit
        ]
        super().__init__(
            placeholder="Select package to recompile...", options=options, max_values=1
        )
        self.packages = packages

    async def callback(self, interaction: discord.Interaction):
        selected_package = self.values[0]

        # Show architecture selection
        arch_view = ui.View()
        arch_view.add_item(ArchitectureSelect(selected_package, interaction.user))
        await interaction.response.send_message(
            f"✅ Selected: `{selected_package}`\n\nNow select architecture:",
            view=arch_view,
            ephemeral=True,
        )


class ArchitectureSelect(ui.Select):
    def __init__(self, package: str, requester: discord.User):
        self.package = package
        self.requester = requester

        options = [
            discord.SelectOption(label=arch.upper(), value=arch)
            for arch in ARCHITECTURES
        ]
        super().__init__(
            placeholder="Select architecture...", options=options, max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        selected_arch = self.values[0]

        # Create approval embed
        embed = discord.Embed(title="🔨 Recompile Request", color=discord.Color.blue())
        embed.add_field(name="Package", value=f"`{self.package}`", inline=False)
        embed.add_field(name="Architecture", value=f"`{selected_arch}`", inline=False)
        embed.add_field(name="Requested by", value=self.requester.mention, inline=False)

        # Send to channel with approval button (only Matt can approve)
        channel = interaction.client.get_channel(CHANNEL_ID)
        approval_msg = await channel.send(
            f"<@{OWNER_ID}> — approve this recompile?",
            embed=embed,
            view=ApprovalButtons(self.package, selected_arch, OWNER_ID),
        )

        await interaction.response.send_message(
            f"✅ Request submitted for approval!\nPackage: `{self.package}` | Arch: `{selected_arch}`",
            ephemeral=True,
        )


class ApprovalButtons(ui.View):
    def __init__(self, package: str, arch: str, owner_id: int):
        super().__init__(timeout=86400)  # 24 hours
        self.package = package
        self.arch = arch
        self.owner_id = owner_id

    @ui.button(label="✅ Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()

        if interaction.user.id != int(self.owner_id):
            await interaction.followup.send(
                "Only the channel owner can approve.", ephemeral=True
            )
            return

        # Trigger GitHub workflow
        success = await trigger_workflow(
            package=self.package, arch=self.arch, requester=interaction.user.name
        )

        if success:
            await interaction.followup.send(
                f"✅ Workflow triggered! Building `{self.package}` for `{self.arch}`",
            )
        else:
            await interaction.followup.send(
                "❌ Failed to trigger workflow. Check logs.",
            )

    @ui.button(label="❌ Reject", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()

        if interaction.user.id != int(self.owner_id):
            await interaction.followup.send(
                "Only the channel owner can reject.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"❌ Request rejected by {interaction.user.mention}",
        )


class PackageView(ui.View):
    def __init__(self, packages):
        super().__init__(timeout=300)
        self.add_item(PackageSelect(packages))


async def fetch_packages() -> list:
    """Fetch package list from GitHub repo directory."""
    try:
        headers = {"Authorization": f"token {GITHUB_PAT}"}
        url = f"https://api.github.com/repos/{PACKAGE_OWNER}/{PACKAGE_REPO}/contents/{PACKAGE_DIR}"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        packages = []
        for item in response.json():
            if item["type"] == "dir":
                packages.append(item["name"])

        return sorted(packages)
    except Exception as e:
        print(f"Error fetching packages: {e}")
        return []


async def trigger_workflow(package: str, arch: str, requester: str) -> bool:
    """Trigger GitHub Actions workflow dispatch."""
    workflow = "build-packages-aarch64" if arch == "aarch64" else "build-packages"
    try:
        headers = {
            "Authorization": f"token {GITHUB_PAT}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {
            "ref": "main",
            "inputs": {
                "package": package,
            },
        }

        url = f"https://api.github.com/repos/{WORKFLOW_OWNER}/{WORKFLOW_REPO}/actions/workflows/{workflow}.yml/dispatches"

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

        return True
    except Exception as e:
        print(f"Error triggering workflow: {e}")
        return False


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"{bot.user} is ready!")


@bot.command(name="recompile")
async def recompile_command(ctx):
    """Start recompile request flow."""
    if ctx.channel.id != CHANNEL_ID:
        await ctx.send(f"This command only works in <#{CHANNEL_ID}>", delete_after=5)
        return

    packages = await fetch_packages()

    if not packages:
        await ctx.send("❌ No packages found. Check repo access.", delete_after=10)
        return

    embed = discord.Embed(
        title="📦 Select Package to Recompile",
        description=f"Found {len(packages)} packages",
        color=discord.Color.blurple(),
    )

    view = PackageView(packages)
    await ctx.send(embed=embed, view=view, delete_after=300)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
