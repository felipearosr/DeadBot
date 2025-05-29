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
        
        if not self.bot.db_pool: # Check if the database pool is available
            await interaction.followup.send("Database connection is not available at the moment. Please try again later.", ephemeral=True)
            print("ERROR: admin_set_payment_char - db_pool not available on self.bot.")
            return

        cleaned_alt_name = payment_alt_name.strip()
        chosen_faction = faction.value

        print(f"DEBUG: alt_mappings BEFORE admin update (in cog): {self.bot.alt_mappings}")

        try:
            # ---- THIS IS THE CORRECTED PART ----
            # Call the new database save function from utils.py
            await utils.save_alt_to_db(self.bot.db_pool, target_user_id, cleaned_alt_name, chosen_faction)
            
            # Also update the in-memory cache on the bot instance
            self.bot.alt_mappings[target_user_id] = {
                "alt": cleaned_alt_name,
                "faction": chosen_faction
            }
            # ---- END OF CORRECTION ----
            print(f"DEBUG: alt_mappings AFTER admin update (in cog): {self.bot.alt_mappings}")

            await interaction.followup.send(
                f"Payment character for **{user.display_name}** (`{user.id}`) has been set/updated to: **{cleaned_alt_name}** ({chosen_faction}). Saved to DB.",
                ephemeral=True
            )
            print(f"Admin {interaction.user.name} set payment char for {user.name} ({target_user_id}) to {cleaned_alt_name} ({chosen_faction}).")
        except Exception as e:
            print(f"ERROR in admin_set_payment_char while saving to DB: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send("An error occurred while trying to save the payment character for the user. Please try again.", ephemeral=True)

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