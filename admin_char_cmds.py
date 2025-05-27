# admin_char_cmds.py
import discord
from discord import app_commands
from discord.ext import commands

# No direct import of 'bot' module needed anymore for globals
import utils
import config

# Forward declaration for type hinting if bot.py imports this cog
# and you want to type hint the bot instance in __init__ more specifically.
# from typing import TYPE_CHECKING
# if TYPE_CHECKING:
#     from bot import RaidManagerBot # Assuming your bot class is RaidManagerBot in bot.py

class AdminCharCommands(commands.Cog):
    def __init__(self, bot: commands.Bot): # bot: RaidManagerBot if using TYPE_CHECKING
        self.bot = bot # Store the bot instance passed from main file

    @app_commands.command(name="admin_set_payment_char", description="[Admin] Set or update a payment character for a user.")
    @app_commands.checks.has_any_role(*config.ALLOWED_ROLES_FOR_ADMIN_CMDS)
    @app_commands.describe(
        user="The Discord user to set the payment character for.",
        payment_alt_name="The user's in-game character name (e.g., Altname).",
        faction="The user's character faction."
    )
    @app_commands.choices(faction=[
        app_commands.Choice(name="Horde", value="Horde"),
        app_commands.Choice(name="Alliance", value="Alliance"),
    ])
    async def admin_set_payment_char(self, interaction: discord.Interaction, user: discord.User, payment_alt_name: str, faction: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        target_user_id = str(user.id)

        # Access alt_mappings via the stored bot instance
        print(f"DEBUG: alt_mappings BEFORE admin update (in cog): {self.bot.alt_mappings}")

        self.bot.alt_mappings[target_user_id] = {
            "alt": payment_alt_name.strip(),
            "faction": faction.value
        }
        print(f"DEBUG: alt_mappings AFTER admin update (in cog): {self.bot.alt_mappings}")

        utils.save_alt_mappings(self.bot.alt_mappings, config.ALTS_FILE)

        await interaction.followup.send(
            f"Payment character for **{user.display_name}** (`{user.id}`) has been set/updated to: **{payment_alt_name}** ({faction.name}).",
            ephemeral=True
        )
        print(f"Admin {interaction.user.name} set payment char for {user.name} ({target_user_id}) to {payment_alt_name} ({faction.name}).")

    @admin_set_payment_char.error
    async def admin_set_payment_char_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if interaction.response.is_done():
            send_method = interaction.followup.send
        else:
            # Should not happen if defer is used, but as a fallback
            await interaction.response.defer(ephemeral=True)
            send_method = interaction.followup.send

        if isinstance(error, app_commands.MissingAnyRole):
            await send_method("You do not have permission to use this command.", ephemeral=True)
        else:
            print(f"Error in /admin_set_payment_char: {error}")
            await send_method("An unexpected error occurred.", ephemeral=True)


    @app_commands.command(name="admin_check_payment_alt", description="[Admin] Check a user's registered payment character.")
    @app_commands.checks.has_any_role(*config.ALLOWED_ROLES_FOR_ADMIN_CMDS)
    @app_commands.describe(user="The Discord user to check.")
    async def admin_check_payment_alt(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer(ephemeral=True)
        target_user_id = str(user.id)

        alt_info = self.bot.alt_mappings.get(target_user_id) # Access via self.bot

        if alt_info:
            await interaction.followup.send(
                f"Registered payment character for **{user.display_name}** (`{user.id}`) is: **{alt_info['alt']}** ({alt_info['faction']}).",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"**{user.display_name}** (`{user.id}`) does not have a payment character registered.",
                ephemeral=True
            )

    @admin_check_payment_alt.error
    async def admin_check_payment_alt_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if interaction.response.is_done():
            send_method = interaction.followup.send
        else:
            await interaction.response.defer(ephemeral=True)
            send_method = interaction.followup.send
            
        if isinstance(error, app_commands.MissingAnyRole):
            await send_method("You do not have permission to use this command.", ephemeral=True)
        else:
            print(f"Error in /admin_check_payment_alt: {error}")
            await send_method("An unexpected error occurred.", ephemeral=True)

async def setup(bot: commands.Bot): # bot: RaidManagerBot if using TYPE_CHECKING
    await bot.add_cog(AdminCharCommands(bot))
    print("AdminCharCommands cog has been loaded and added.")