import discord
from discord import app_commands # Ensure this is imported if not already at top
from discord.ext import commands
import io
# import csv # Only needed by /log, can be imported there if not used elsewhere
# import os # Only needed by __main__ if checking files, not needed if DB only
import json # Potentially needed by /cut for log_entry formatting before DB
import datetime
from typing import List, Dict, Tuple, Optional 
import asyncpg # For database interactions
import asyncio  # <-- Make sure this is here
import logging  # <-- Add this for better logs

import config # Your configuration file
import utils  # Your utility functions
from utils import AltInfo # Your TypedDict

# --- Type Hinting & Bot State ---
class CurrentRunData:
    def __init__(self):
        self.run_date: str = ""
        self.wcl_link: str = ""
        self.total_gold: int = 0
        self.roster_raw: str = ""
        self.active_boosters: List[Tuple[str, str]] = [] 
        self.benched_players: List[str] = []
        self.guild_share: float = 0
        self.raid_leader_share_amount: float = 0
        self.gold_per_booster: float = 0
        self.data_loaded: bool = False
    def reset(self): self.__init__()

current_run_session = CurrentRunData()
# Module-level globals removed; they are now instance attributes of RaidManagerBot


class RaidManagerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # intents.message_content = True # Uncomment if you add prefix commands that read messages
        super().__init__(command_prefix="!", intents=intents)
        self.initial_extensions = ['admin_char_cmds'] 
        
        # Initialize as instance attributes
        self.db_pool: Optional[asyncpg.Pool] = None # For database connection pool
        self.alt_mappings: Dict[str, AltInfo] = {}
        self.run_logs: List[Dict] = []

    async def setup_hook(self):
        # --- DATABASE CONNECTION AND INITIAL DATA LOAD (WITH RETRY LOGIC) ---
        max_retries = 5
        retry_delay = 5  # Increased to 5 seconds for a safer margin

        for attempt in range(max_retries):
            try:
                # Ensure DATABASE_URL is correctly set in config and environment
                if not config.DATABASE_URL or "db_fallback_local_dev" in config.DATABASE_URL:
                    raise ConnectionError("DATABASE_URL is not properly configured.")

                logging.info(f"Attempting to connect to the database (Attempt {attempt + 1}/{max_retries})...")
                # Create the connection pool. A timeout is added to each connection attempt.
                self.db_pool = await asyncpg.create_pool(
                    dsn=config.DATABASE_URL, 
                    min_size=1, 
                    max_size=10,
                    timeout=15 # Timeout for an individual connection attempt
                )
                
                if self.db_pool:
                    logging.info("✅ Successfully connected to PostgreSQL and created connection pool.")
                    await utils.setup_database_tables(self.db_pool)  # Create tables if they don't exist
                    
                    # Load initial data from DB into memory
                    self.alt_mappings = await utils.load_all_alt_mappings_from_db(self.db_pool)
                    self.run_logs = await utils.load_all_run_logs_from_db(self.db_pool)
                    logging.info(f"Loaded {len(self.alt_mappings)} alts and {len(self.run_logs)} logs from database.")
                    
                    break  # --- IMPORTANT: Exit the loop on success ---

            except Exception as e_db_setup:
                logging.warning(f"⚠️ Database connection failed on attempt {attempt + 1}: {e_db_setup}")
                if attempt < max_retries - 1:
                    logging.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                else:
                    logging.critical("❌ Could not establish database connection after all retries.")
                    import traceback
                    traceback.print_exc()
                    self.db_pool = None # Ensure pool is None on final failure
        
        # --- END DATABASE SETUP ---

        # If the db_pool is still None, we print a final status warning
        if not self.db_pool:
            logging.error("Bot will continue without database functionality. Data persistence will fail.")

        # Load extensions (cogs) - this now runs after the database attempts are complete
        for extension in self.initial_extensions:
            try:
                await self.load_extension(extension)
                logging.info(f"Successfully loaded extension: {extension}")
            except Exception as e_load_ext:
                logging.error(f"Failed to load extension {extension}: {e_load_ext}")
                import traceback
                traceback.print_exc()

        # --- COMMAND SYNCING (Your existing code is good) ---
        logging.info("Proceeding with command syncing...")
        # (Your command syncing logic remains here, unchanged)
        # Set these flags to False after their one-time run to clear commands.
        CLEAR_GLOBAL_COMMANDS_ONCE = False 
        CLEAR_TARGET_GUILD_COMMANDS_ONCE = False 

        if CLEAR_GLOBAL_COMMANDS_ONCE:
            print("ONE-TIME: Attempting to clear ALL global application commands...")
            try:
                self.tree.clear_commands(guild=None)
                await self.tree.sync(guild=None)
                print("ONE-TIME: Successfully cleared global commands. SET CLEAR_GLOBAL_COMMANDS_ONCE TO False.")
            except Exception as e_clear_global:
                print(f"ONE-TIME: Error clearing global commands: {e_clear_global}")
        
        if config.TARGET_GUILD_IDS:
            for guild_id in config.TARGET_GUILD_IDS:
                guild_obj = discord.Object(id=guild_id)
                if CLEAR_TARGET_GUILD_COMMANDS_ONCE:
                    print(f"ONE-TIME: Attempting to clear commands from target guild: {guild_id}...")
                    try:
                        self.tree.clear_commands(guild=guild_obj)
                        await self.tree.sync(guild=guild_obj)
                        print(f"ONE-TIME: Successfully cleared commands from guild {guild_id}. SET CLEAR_TARGET_GUILD_COMMANDS_ONCE TO False if all target guilds are done.")
                    except Exception as e_clear_guild:
                        print(f"ONE-TIME: Error clearing commands from guild {guild_id}: {e_clear_guild}")

                print(f"Attempting to sync commands to guild: {guild_id}...")
                try:
                    self.tree.copy_global_to(guild=guild_obj)
                    synced_commands = await self.tree.sync(guild=guild_obj)
                    print(f"Synced {len(synced_commands)} application commands to guild {guild_id}.")
                except discord.errors.Forbidden:
                        print(f"ERROR: Bot is not in guild {guild_id} or lacks 'application.commands' scope or permissions. Cannot sync commands.")
                except Exception as e_sync_guild:
                    print(f"Error during command sync for guild {guild_id}: {e_sync_guild}")
        elif not CLEAR_GLOBAL_COMMANDS_ONCE: # No target guilds specified, sync globally (if not cleared)
            print("No TARGET_GUILD_IDS specified in config. Attempting global command sync.")
            try:
                synced_commands = await self.tree.sync()
                print(f"Synced {len(synced_commands)} global application commands.")
            except Exception as e_global_sync:
                print(f"Error during global command sync: {e_global_sync}")
        
        print("Command syncing process complete. Review logs.")
        # --- END COMMAND SYNCING ---
           
        # The loading of alt_mappings and run_logs is now done within the database setup block above.
        # The print statement for loaded alts/logs is also part of that block.

    async def close(self):
        """Gracefully closes the database connection pool when the bot shuts down."""
        if self.db_pool:
            await self.db_pool.close()
            print("Database connection pool closed.")
        await super().close() # Call the parent class's close method

    async def on_ready(self):
        print(f'Logged in as {self.user.name} ({self.user.id})')
        if not self.db_pool:
            print("WARNING: Bot is ready, but database connection pool was NOT established successfully.")
            print("Data persistence and some commands may not work correctly.")

bot = RaidManagerBot()

@bot.tree.command(name="set_payment_char", description="Register or update your payment character and faction.")
@app_commands.describe(
    payment_alt_name="Your in-game character name for receiving gold (e.g., Altname).",
    faction="Your character's faction (Horde or Alliance)."
)
@app_commands.choices(faction=[
    app_commands.Choice(name="Horde", value="Horde"),
    app_commands.Choice(name="Alliance", value="Alliance"),
])
async def set_payment_char_command(interaction: discord.Interaction, payment_alt_name: str, faction: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    
    bot_instance: RaidManagerBot = interaction.client # type: ignore 

    if not bot_instance.db_pool: # Check if the database pool is available
        await interaction.followup.send("Database connection is not available at the moment. Please try again later.", ephemeral=True)
        print("ERROR: set_payment_char_command - db_pool not available.")
        return

    cleaned_alt_name = payment_alt_name.strip()
    chosen_faction = faction.value

    try:
        # Call the new database save function from utils.py
        await utils.save_alt_to_db(bot_instance.db_pool, user_id, cleaned_alt_name, chosen_faction)
        
        # Also update the in-memory cache
        bot_instance.alt_mappings[user_id] = {"alt": cleaned_alt_name, "faction": chosen_faction}

        await interaction.followup.send(
            f"Your payment character has been set/updated to: **{cleaned_alt_name}** ({chosen_faction}).",
            ephemeral=True
        )
        print(f"User {interaction.user.name} ({user_id}) set payment char to {cleaned_alt_name} ({chosen_faction}). Saved to DB.")
    except Exception as e:
        print(f"ERROR in set_payment_char_command while saving to DB: {e}")
        import traceback
        traceback.print_exc()
        await interaction.followup.send("An error occurred while trying to save your payment character. Please try again.", ephemeral=True)

@bot.tree.command(name="check_payment_alt", description="Check your currently registered payment character.")
async def check_payment_alt_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    bot_instance: RaidManagerBot = interaction.client # type: ignore
    alt_info = bot_instance.alt_mappings.get(user_id)

    if alt_info:
        await interaction.followup.send(
            f"Your registered payment character is: **{alt_info['alt']}** ({alt_info['faction']}).",
            ephemeral=True
        )
    else:
        await interaction.followup.send(
            "You do not have a payment character registered yet. Use `/set_payment_char` to set one.",
            ephemeral=True
        )

@bot.tree.command(name="log", description="Export all run logs to a CSV file. (Admin Only)")
@app_commands.checks.has_any_role(*config.ALLOWED_ROLES_FOR_ADMIN_CMDS)
async def log_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bot_instance: RaidManagerBot = interaction.client # type: ignore
    if not bot_instance.run_logs:
        await interaction.followup.send("No logs available to export.", ephemeral=True)
        return

    output = io.StringIO()
    fieldnames = [
        "run_date", "wcl_link", "total_gold_run", "guild_share_run",
        "raid_leader_share_run", "gold_per_booster_run", "booster_name",
        "booster_discord_id", "is_benched_player_on_this_row", "run_processed_by",
        "run_timestamp_utc"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore', restval="")
    writer.writeheader()

    for idx, log_entry in enumerate(bot_instance.run_logs):
        active_list = log_entry.get("active_boosters", [])
        benched_list = log_entry.get("benched_players", [])
        max_r = max(1, len(active_list), len(benched_list)) 

        for i in range(max_r):
            row_data = {}
            if i == 0:
                row_data["run_date"] = log_entry.get("run_date", "")
                row_data["wcl_link"] = log_entry.get("wcl_link", "")
                row_data["total_gold_run"] = log_entry.get("total_gold", 0)
                row_data["guild_share_run"] = log_entry.get("guild_share_gold", 0.0)
                row_data["raid_leader_share_run"] = log_entry.get("raid_leader_share_gold", 0.0)
                row_data["gold_per_booster_run"] = log_entry.get("gold_per_booster", 0.0)
                row_data["run_processed_by"] = log_entry.get("processed_by_username", "")
                row_data["run_timestamp_utc"] = log_entry.get("timestamp_utc", "")

            if i < len(active_list):
                booster = active_list[i]
                row_data["booster_name"] = booster.get("name", "")
                row_data["booster_discord_id"] = booster.get("discord_id", "")
            if i < len(benched_list):
                row_data["is_benched_player_on_this_row"] = benched_list[i]
            if row_data:
                writer.writerow(row_data)
        
        if max_r > 0 and len(bot_instance.run_logs) > 1 and idx < len(bot_instance.run_logs) -1 :
            writer.writerow({key: "" for key in fieldnames})

    output.seek(0)
    await interaction.followup.send("Run logs exported:", file=discord.File(fp=output, filename="all_run_logs.csv"), ephemeral=True)

@log_command.error
async def log_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    send_method = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    if not interaction.response.is_done(): await interaction.response.defer(ephemeral=True) # Ensure deferred if error before
    
    if isinstance(error, app_commands.MissingAnyRole):
        await send_method("You do not have permission to use this command.", ephemeral=True)    
    else:
        print(f"Error in /log command: {error}")
        await send_method("An error occurred while generating the log.", ephemeral=True)

# --- /cut command ---
@bot.tree.command(name="cut", description="Calc gold, log, & generate payment strings. (Admin Only)") # Removed "optionally"
@app_commands.checks.has_any_role(*config.ALLOWED_ROLES_FOR_ADMIN_CMDS)
@app_commands.describe(
    run_date="Date of the run (YYYY-MM-DD).",
    warcraft_logs_link="Link to the Warcraft Logs report.",
    total_gold="Total gold amount for the run.",
    roster_file="Upload roster .txt or .csv (Discord IDs in 4th column).",
    payment_subject="Subject for payment mail (e.g., GDKP Payout).", # No longer says OPTIONAL
    payment_body="Body for payment mail (e.g., Thanks for boosting!)."  # No longer says OPTIONAL
)
async def cut_command(
    interaction: discord.Interaction,
    run_date: str,
    warcraft_logs_link: str,
    total_gold: int,
    roster_file: discord.Attachment,
    payment_subject: str, # Required
    payment_body: str     # Required
):
    await interaction.response.defer(ephemeral=True)
    bot_instance: RaidManagerBot = interaction.client # type: ignore

    # --- DB Pool Check ---
    if not bot_instance.db_pool:
        await interaction.followup.send("Database connection is not available at the moment. Please try again later.", ephemeral=True)
        print("ERROR: cut_command - db_pool not available.")
        return

    # --- Initial Validations ---
    if not utils.is_valid_date(run_date):
        await interaction.followup.send("Invalid date format. Please use `YYYY-MM-DD`.", ephemeral=True)
        return
    if total_gold <= 0:
        await interaction.followup.send("Total gold must be a positive number.", ephemeral=True)
        return
    if not roster_file.filename.lower().endswith(('.txt', '.csv')):
        await interaction.followup.send("Roster file must be a `.txt` or `.csv` file.", ephemeral=True)
        return

    try:
        roster_bytes = await roster_file.read()
        roster_data_string = roster_bytes.decode('utf-8')
    except Exception as e:
        await interaction.followup.send(f"Error reading roster file: {e}", ephemeral=True)
        return

    # current_run_session is a module-level global, used for this specific command's flow
    current_run_session.reset()
    current_run_session.run_date = run_date
    current_run_session.wcl_link = warcraft_logs_link
    current_run_session.total_gold = total_gold
    current_run_session.roster_raw = roster_data_string

    active_boosters_with_ids, benched_players_names = utils.parse_roster_data(roster_data_string)
    if not active_boosters_with_ids:
        await interaction.followup.send("No active boosters found in the roster. Please check the file format and content.", ephemeral=True)
        return
    current_run_session.active_boosters = active_boosters_with_ids
    current_run_session.benched_players = benched_players_names

    # --- Calculations ---
    calculated_raid_leader_share_amount = 0.0
    actual_rl_cut_percentage = 0.0
    rl_config_percentage = getattr(config, 'RAID_LEADER_CUT_PERCENTAGE', 0.0)

    if rl_config_percentage > 0:
        calculated_raid_leader_share_amount = current_run_session.total_gold * rl_config_percentage
        actual_rl_cut_percentage = rl_config_percentage
    current_run_session.raid_leader_share_amount = calculated_raid_leader_share_amount
    
    gold_after_rl_cut = current_run_session.total_gold - calculated_raid_leader_share_amount
    guild_config_percentage = getattr(config, 'GUILD_CUT_PERCENTAGE', 0.0)
    guild_share_amount = gold_after_rl_cut * guild_config_percentage
    current_run_session.guild_share = guild_share_amount
    
    remaining_gold_for_boosters = gold_after_rl_cut - guild_share_amount
    gold_per_booster_precise = remaining_gold_for_boosters / len(active_boosters_with_ids) if active_boosters_with_ids else 0
    gold_per_booster_for_payment = int(gold_per_booster_precise) # Gold for mail should be integer
    current_run_session.gold_per_booster = gold_per_booster_precise
    current_run_session.data_loaded = True

    # --- Log Entry Preparation ---
    log_entry_data = {
        "run_date": run_date, 
        "wcl_link": warcraft_logs_link, 
        "total_gold": total_gold,
        "raid_leader_cut_percentage": actual_rl_cut_percentage,
        "raid_leader_share_gold": round(calculated_raid_leader_share_amount, 2),
        "guild_cut_percentage": guild_config_percentage,
        "guild_share_gold": round(guild_share_amount, 2),
        "gold_per_booster": round(gold_per_booster_precise, 2),
        "num_boosters": len(active_boosters_with_ids),
        # For DB JSONB, we can store the list of tuples/strings directly.
        # asyncpg will handle JSON serialization for these.
        "active_boosters": active_boosters_with_ids, 
        "benched_players": benched_players_names,    
        "processed_by_user_id": str(interaction.user.id),
        "processed_by_username": interaction.user.name,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc) # Pass datetime object
    }
    
    try:
        await utils.save_run_log_entry_to_db(bot_instance.db_pool, log_entry_data)
        
        # Update in-memory cache (self.bot.run_logs)
        # Create a copy to modify for the cache if its structure needs to differ (e.g., for older CSV export logic)
        cached_log_entry = log_entry_data.copy()
        # If other parts (like /log CSV export) expect active_boosters as list of dicts:
        cached_log_entry["active_boosters"] = [{"name": n, "discord_id": d} for n, d in active_boosters_with_ids]
        # If other parts expect timestamp_utc as ISO string:
        if isinstance(cached_log_entry["timestamp_utc"], datetime.datetime):
            cached_log_entry["timestamp_utc"] = cached_log_entry["timestamp_utc"].isoformat()
        
        bot_instance.run_logs.append(cached_log_entry) 
        print(f"Saved run log to DB. Total in-memory logs: {len(bot_instance.run_logs)}")
    except Exception as e:
        print(f"ERROR in cut_command while saving run log to DB: {e}")
        import traceback
        traceback.print_exc()
        await interaction.followup.send("An error occurred while saving the run log. The cut was processed, but logging failed.", ephemeral=True)
        # Consider if processing should halt if logging fails
        # return 

    # --- Create Public Embed ---
    public_embed = discord.Embed(title=f"Raid Cut Summary: {run_date}", color=discord.Color.green())
    public_embed.add_field(name="Logs", value=f"[{warcraft_logs_link.split('/')[-1] if '/' in warcraft_logs_link else 'Report'}]({warcraft_logs_link})", inline=False)
    public_embed.add_field(name="Booster Cut", value=f"{gold_per_booster_precise:,.2f}g", inline=True)
    public_embed.add_field(name="Num. of Boosters", value=str(len(active_boosters_with_ids)), inline=True)
    public_booster_names = [n for n, d in active_boosters_with_ids]
    public_booster_list_str = "\n".join(public_booster_names)
    if len(public_booster_list_str) > 1020: public_booster_list_str = public_booster_list_str[:1020] + "..."
    public_embed.add_field(name=f"Active Boosters ({len(public_booster_names)})", value=public_booster_list_str or "None", inline=False)
    public_benched_list_str = "\n".join(benched_players_names)
    if len(public_benched_list_str) > 1020: public_benched_list_str = public_benched_list_str[:1020] + "..."
    public_embed.add_field(name=f"Benched ({len(benched_players_names)})", value=public_benched_list_str or "None", inline=False)
    public_embed.set_footer(text="Run processed. Admins have detailed view & payment options.")

    # --- Admin Embed ---
    admin_embed = discord.Embed(title=f"ADMIN VIEW - Cut Details: {run_date}", color=discord.Color.gold())
    admin_embed.add_field(name="Logs", value=f"[{warcraft_logs_link.split('/')[-1] if '/' in warcraft_logs_link else 'Report'}]({warcraft_logs_link})", inline=False)
    admin_embed.add_field(name="Total Gold Pot", value=f"{total_gold:,d}g", inline=True)
    if actual_rl_cut_percentage > 0:
        admin_embed.add_field(name=f"Raid Leader Cut ({actual_rl_cut_percentage*100:.1f}%)", value=f"{calculated_raid_leader_share_amount:,.2f}g (Manual Payout)", inline=True)
    else:
        admin_embed.add_field(name="Raid Leader Cut", value="0g (Not configured or 0%)", inline=True)
    admin_embed.add_field(name=f"Guild Cut ({guild_config_percentage*100:.1f}%)", value=f"{guild_share_amount:,.2f}g", inline=True)
    admin_embed.add_field(name="Gold for Boosters (Total)", value=f"{remaining_gold_for_boosters:,.2f}g", inline=True)
    admin_embed.add_field(name="Gold/Booster (Precise)", value=f"{gold_per_booster_precise:,.2f}g", inline=True)
    admin_embed.add_field(name="Gold/Booster (Mail)", value=f"{gold_per_booster_for_payment:,d}g", inline=True)
    admin_booster_details = [f"{name} (ID: `{disc_id}`)" for name, disc_id in active_boosters_with_ids]
    admin_booster_list_str = "\n".join(admin_booster_details)
    if len(admin_booster_list_str) > 1020: admin_booster_list_str = admin_booster_list_str[:1020] + "..."
    admin_embed.add_field(name=f"Active Boosters + IDs ({len(admin_booster_details)})", value=admin_booster_list_str or "None", inline=False)
    admin_embed.add_field(name=f"Benched ({len(benched_players_names)})", value=public_benched_list_str or "None", inline=False)
    admin_embed.set_footer(text=f"Processed by: {interaction.user.display_name}")
    await interaction.followup.send(embed=admin_embed, ephemeral=True) # Send Admin details first

    # --- Generate Alt/Faction Warnings ---
    admin_alt_warnings_list: List[str] = [] 
    public_warning_ping_list: List[str] = []  
    if active_boosters_with_ids:
        for char_name, discord_id in active_boosters_with_ids:
            alt_info = bot_instance.alt_mappings.get(discord_id) # From in-memory cache (loaded from DB)
            admin_warning_text = ""
            public_ping_text = ""
            if not (alt_info and alt_info.get("alt") and alt_info.get("faction")):
                admin_warning_text = f"No Alt/Faction registered for ID `{discord_id}` ({char_name})."
                public_ping_text = f"<@{discord_id}> ({char_name}): Needs to register Alt/Faction (e.g., use `/set_payment_char`)."
            elif alt_info: 
                known_factions = ["horde", "alliance"] 
                alt_faction_lower = alt_info.get("faction", "").lower()
                if alt_faction_lower not in known_factions:
                    admin_warning_text = f"Unknown faction for ID `{discord_id}` ({char_name}). Alt: {alt_info['alt']}, Faction: '{alt_info.get('faction', 'N/A')}'"
                    public_ping_text = f"<@{discord_id}> ({char_name}): Faction '{alt_info.get('faction', 'N/A')}' for alt '{alt_info['alt']}' is not recognized. Please update (e.g., use `/set_payment_char`)."
            if admin_warning_text: admin_alt_warnings_list.append(admin_warning_text)
            if public_ping_text: public_warning_ping_list.append(public_ping_text)
    
    # --- Payment String Generation (for Admin) & Ephemeral Warning Send ---
    # Since payment_subject and payment_body are required, we always proceed with this block.
    admin_warnings_content_for_ephemeral = ""
    if admin_alt_warnings_list:
        admin_warnings_content_for_ephemeral = "\n\n**Payment/Alt Warnings (for your reference):**\n" + "\n".join(admin_alt_warnings_list)

    admin_payment_strings_content = ""
    if gold_per_booster_for_payment <= 0 and active_boosters_with_ids:
        await interaction.followup.send("Gold per booster is 0 or less. No payment strings generated." + admin_warnings_content_for_ephemeral, ephemeral=True)
    elif not active_boosters_with_ids: 
        # This case should ideally be caught earlier by the check after parse_roster_data
        await interaction.followup.send("No active boosters to generate payments for." + admin_warnings_content_for_ephemeral, ephemeral=True)
    else: # Proceed to generate payment strings
        horde_salestools_parts: List[str] = []
        alliance_salestools_parts: List[str] = []
        for char_name, discord_id in active_boosters_with_ids:
            alt_info = bot_instance.alt_mappings.get(discord_id)
            if alt_info and alt_info.get("alt") and alt_info.get("faction"):
                alt_faction_lower = alt_info['faction'].lower()
                if alt_faction_lower in ["horde", "alliance"]:
                    alt_name_for_payment = alt_info['alt']
                    # Assuming Area52 is the default/target realm for payments
                    if "-Area52" not in alt_name_for_payment.replace(" ", ""): 
                        alt_name_for_payment += "-Area52"
                    salestool_part = f"{alt_name_for_payment}:{gold_per_booster_for_payment}:{payment_subject}:{payment_body}"
                    if alt_faction_lower == "horde": horde_salestools_parts.append(salestool_part)
                    elif alt_faction_lower == "alliance": alliance_salestools_parts.append(salestool_part)
        
        payment_output_sections = []
        any_payment_generated = False
        if horde_salestools_parts: 
            any_payment_generated = True
            payment_output_sections.append(f"**Horde SalesTools ({len(horde_salestools_parts)}):**\n```\n" + "\n".join(horde_salestools_parts) + "\n```")
        else: 
            payment_output_sections.append("**Horde Payouts:** None.")
        if alliance_salestools_parts: 
            any_payment_generated = True
            payment_output_sections.append(f"**Alliance SalesTools ({len(alliance_salestools_parts)}):**\n```\n" + "\n".join(alliance_salestools_parts) + "\n```")
        else: 
            payment_output_sections.append("**Alliance Payouts:** None.")
        admin_payment_strings_content = "\n\n".join(payment_output_sections)

        # Send payment strings and/or admin warnings
        if not any_payment_generated and not admin_alt_warnings_list:
            await interaction.followup.send("Payment: No boosters processed for payment strings and no alt warnings.", ephemeral=True)
        else:
            await utils.send_long_message_or_file(interaction, 
                                            admin_payment_strings_content, 
                                            admin_warnings_content_for_ephemeral, 
                                            "payment_and_warnings_details.txt", 
                                            ephemeral=True)
    
    # --- Determine Target Public Channels ---
    target_public_channels: List[discord.TextChannel] = []
    configured_channel_ids_setting = getattr(config, 'PUBLIC_SUMMARY_CHANNEL_IDS', None)

    channel_ids_to_process: List[int] = []
    source_of_channels = "config (PUBLIC_SUMMARY_CHANNEL_IDS)"
    current_guild_id = interaction.guild_id # Get the guild ID where the command was run

    if isinstance(configured_channel_ids_setting, list):
        if not configured_channel_ids_setting:
            print("INFO: PUBLIC_SUMMARY_CHANNEL_IDS is configured as an empty list. No public messages will be sent via config for this run.")
        for item in configured_channel_ids_setting:
            try:
                channel_ids_to_process.append(int(item))
            except (ValueError, TypeError):
                print(f"WARNING: Invalid channel ID '{item}' in PUBLIC_SUMMARY_CHANNEL_IDS list. Skipping.")
    elif isinstance(configured_channel_ids_setting, (str, int)):
        try:
            channel_ids_to_process.append(int(configured_channel_ids_setting))
        except (ValueError, TypeError):
            print(f"WARNING: Invalid PUBLIC_SUMMARY_CHANNEL_IDS value '{configured_channel_ids_setting}'. Skipping.")
    elif configured_channel_ids_setting is None: # Fallback to interaction channel
        source_of_channels = "interaction.channel_id (fallback)"
        if interaction.channel_id:
            channel_ids_to_process.append(interaction.channel_id)
            print(f"DEBUG: PUBLIC_SUMMARY_CHANNEL_IDS not in config. Using interaction channel ({interaction.channel_id}) for server {current_guild_id}.")
        elif current_guild_id: # Has guild context, but no specific channel_id from interaction (should be rare for slash commands)
            print(f"ERROR: PUBLIC_SUMMARY_CHANNEL_IDS not in config, and interaction.channel_id is None, but guild context ({current_guild_id}) exists. Cannot determine fallback channel.")
        else: # No guild context and no channel_id from interaction (e.g. DM context, though less likely for this command)
            print("ERROR: PUBLIC_SUMMARY_CHANNEL_IDS not in config and no interaction channel/guild context. Cannot determine public channel.")
    else:
        print(f"WARNING: PUBLIC_SUMMARY_CHANNEL_IDS is an unexpected type ({type(configured_channel_ids_setting)}). No public messages sent via config.")

    if not current_guild_id and channel_ids_to_process and configured_channel_ids_setting is not None:
        print(f"WARNING: Command used outside of a server (guild_id is None), but PUBLIC_SUMMARY_CHANNEL_IDS is configured. Cannot filter by server, so no public messages will be sent from config to avoid cross-server leakage.")
        channel_ids_to_process = [] # Clear it to prevent sending to all configured channels if not in a guild

    for channel_id_val in channel_ids_to_process:
        _channel = bot_instance.get_channel(channel_id_val)
        if _channel and isinstance(_channel, discord.TextChannel):
            if _channel.guild and _channel.guild.id == current_guild_id:
                target_public_channels.append(_channel)
                print(f"DEBUG: Resolved public channel for current server ({current_guild_id}): '{_channel.name}' ({_channel.id}) from ID {channel_id_val} (source: {source_of_channels}).")
            elif not _channel.guild:
                 print(f"DEBUG: Configured channel '{_channel.name}' ({_channel.id}) is a DM or group channel, not in a server. Skipping for guild-specific announcements.")
            elif current_guild_id: # Channel is in a guild, but a different one
                print(f"DEBUG: Configured channel '{_channel.name}' ({_channel.id}) in server {_channel.guild.id} does not match current server {current_guild_id}. Skipping.")
            # If current_guild_id was None but we somehow still processed a channel_id (e.g. from fallback interaction.channel_id which was a DM), this logic is okay.
        elif _channel:
            print(f"ERROR: Configured channel ID {channel_id_val} (source: {source_of_channels}) is not a TextChannel (type: {type(_channel)}). Skipping.")
        else:
            print(f"ERROR: Configured channel ID {channel_id_val} (source: {source_of_channels}) not found or bot lacks access. Skipping.")

    if not target_public_channels:
        # This message covers cases where config was provided but no channels matched the current server, or config was empty/invalid.
        # It also covers if fallback to interaction channel failed (e.g. interaction.channel_id was None).
        if configured_channel_ids_setting is not None and configured_channel_ids_setting != [] : # Config was set and non-empty
            if current_guild_id: # And we are in a server context
                try: 
                    await interaction.followup.send(
                        f"Warning: PUBLIC_SUMMARY_CHANNEL_IDS is configured, but no specified channels were found or accessible in the current server (ID: {current_guild_id}). Public messages not sent to configured channels for this server.", 
                        ephemeral=True
                    )
                except Exception as e_followup: print(f"Error sending followup for channel resolution failure: {e_followup}")
            # If not in a guild context but config was set, previous log about clearing channel_ids_to_process covers it.
        elif configured_channel_ids_setting is None and not interaction.channel_id:
             # This means fallback to interaction channel also failed because interaction.channel_id was None.
             try: 
                await interaction.followup.send("Critical: Could not determine a channel to send public messages. Summary/warnings not sent publicly.", ephemeral=True)
             except Exception as e_followup: print(f"Error sending followup for fallback channel failure: {e_followup}")
        else:
             print("INFO: No target channels resolved for public messages after processing config and fallbacks for the current server.")
            
    # --- Send Public Messages to Resolved Channels ---
    if not target_public_channels:
        print("INFO: No public channels to send messages to.")
    else:
        sent_to_at_least_one_channel = False
        print(f"INFO: Attempting to send public messages to {len(target_public_channels)} channel(s).")

        for P_channel_obj in target_public_channels: # Renamed to avoid conflict if any
            channel_name_for_log = f"{P_channel_obj.name} ({P_channel_obj.id})"
            # 1. Send Public Raid Cut Summary
            try:
                await P_channel_obj.send(embed=public_embed)
                print(f"INFO: Public summary sent successfully to channel {channel_name_for_log}.")
                sent_to_at_least_one_channel = True
            except discord.Forbidden:
                print(f"ERROR: Bot lacks permission for Public Summary in {channel_name_for_log}.")
                if configured_channel_ids_setting is not None: # Inform admin if this was a *configured* channel
                    try: await interaction.followup.send(f"Warning: Failed to send public raid summary to {P_channel_obj.mention} due to permissions.", ephemeral=True)
                    except: pass 
            except Exception as e_ps:
                print(f"ERROR: Unexpected error sending Public Summary to {channel_name_for_log}: {e_ps}")
                if configured_channel_ids_setting is not None:
                    try: await interaction.followup.send(f"Warning: An unexpected error occurred sending public raid summary to {P_channel_obj.mention}: {e_ps}", ephemeral=True)
                    except: pass
            
            # 2. Send Public Payment Warnings (if any)
            if public_warning_ping_list:
                public_warnings_title = "**⚠️ Public Alt/Faction Warnings**"
                public_warnings_content_str = "\n".join(public_warning_ping_list) 
                full_public_warning_message = f"{public_warnings_title}\n{public_warnings_content_str}"

                if len(full_public_warning_message) > 1950: # Discord message limit is 2000
                    warning_output_file = io.StringIO()
                    warning_output_file.write(full_public_warning_message.replace("\\n", "\n")) # Correct newlines for file
                    warning_output_file.seek(0)
                    try:
                        await P_channel_obj.send(
                            f"{public_warnings_title}\n(List too long, warnings with pings attached as `payment_warnings.txt`)",
                            file=discord.File(fp=warning_output_file, filename="payment_warnings.txt"),
                            allowed_mentions=discord.AllowedMentions(users=True) 
                        )
                        print(f"INFO: Public payment warnings (with pings) sent as a file to {channel_name_for_log}.")
                        sent_to_at_least_one_channel = True 
                    except discord.Forbidden: print(f"ERROR: Bot lacks permission for Public Warnings (file) in {channel_name_for_log}.")
                    except Exception as e_pwf: print(f"ERROR: Unexpected error sending Public Warnings (file) to {channel_name_for_log}: {e_pwf}")
                    finally: warning_output_file.close()
                else:
                    try:
                        await P_channel_obj.send(
                            full_public_warning_message,
                            allowed_mentions=discord.AllowedMentions(users=True)
                        )
                        print(f"INFO: Public payment warnings (with pings) sent as a message to {channel_name_for_log}.")
                        sent_to_at_least_one_channel = True
                    except discord.Forbidden: print(f"ERROR: Bot lacks permission for Public Warnings (message) in {channel_name_for_log}.")
                    except Exception as e_pwm: print(f"ERROR: Unexpected error sending Public Warnings (message) to {channel_name_for_log}: {e_pwm}")
            elif public_warning_ping_list and not sent_to_at_least_one_channel: # Warnings existed, but no channel was suitable for *any* message yet
                # This specific log might be redundant if the initial "No public channels to send messages to" or resolution errors covered it.
                # However, if summary failed but warnings could theoretically go, this might be relevant.
                # For simplicity, this specific log can be removed if other logs are clear.
                pass # Covered by general channel failure logs

        if not sent_to_at_least_one_channel and target_public_channels:
             print(f"INFO: Although {len(target_public_channels)} channel(s) were resolved, no public messages (summary or warnings) were successfully sent due to errors/permissions.")
        elif not sent_to_at_least_one_channel and not target_public_channels and public_warning_ping_list:
            # This case means: No channels resolved AND there were warnings to send.
            # The "No target channels resolved for public messages..." log and potentially an ephemeral already covered the channel part.
            print("INFO: Public payment warnings were generated but could not be sent publicly as no channels were resolved/accessible.")


@cut_command.error
async def cut_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # Ensure deferral if not already done, especially for early errors
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.InteractionResponded: # Already responded or deferred
            pass
        except Exception as e_defer: # Other error during defer
            print(f"CRITICAL: Failed to defer interaction in cut_error: {e_defer}")
            # At this point, sending a followup might also fail.
            return 

    send_method = interaction.followup.send # Now always use followup

    if isinstance(error, app_commands.MissingAnyRole):
        await send_method("You do not have the required role to use this command.", ephemeral=True)
    else:
        print(f"Error in /cut command: {error}")
        import traceback
        traceback.print_exc()
        await send_method("An unexpected error occurred while processing the cut. Please check the bot logs.", ephemeral=True)

# --- Main Execution Block ---
if __name__ == "__main__":
    # The os.path.exists checks and creation for ALTS_FILE and RUN_LOGS_FILE
    # have been REMOVED because data is now in the PostgreSQL database.
    # The database tables are created in RaidManagerBot.setup_hook() via utils.setup_database_tables()

    bot_token_to_check = getattr(config, 'BOT_TOKEN', "YOUR_BOT_TOKEN_FALLBACK_IF_NOT_SET_LOCALLY")
    db_url_to_check = getattr(config, 'DATABASE_URL', "postgresql://user:pass@host:port/db_fallback_local_dev")

    # Perform checks on configuration
    critical_config_missing = False
    if "YOUR_BOT_TOKEN_FALLBACK_IF_NOT_SET_LOCALLY" in bot_token_to_check or not bot_token_to_check:
        print("ERROR: BOT_TOKEN is not correctly set. Please set it as an environment variable on Railway or in your local .env file.")
        critical_config_missing = True
    
    if "db_fallback_local_dev" in db_url_to_check or not db_url_to_check:
        print("ERROR: DATABASE_URL is not correctly set. Please set it as an environment variable on Railway (from your PostgreSQL service) or in your local .env file.")
        critical_config_missing = True

    if critical_config_missing:
        print("Halting bot due to missing critical configuration.")
        # Consider exiting more gracefully or raising an exception if preferred
        exit(1) 
    else:
        # Non-critical config logging (optional)
        rl_cut_percentage_config = getattr(config, 'RAID_LEADER_CUT_PERCENTAGE', 0.0)
        if rl_cut_percentage_config > 0.0:
             print(f"INFO: `RAID_LEADER_CUT_PERCENTAGE` is set to {rl_cut_percentage_config*100:.1f}%.")
        else:
             print("INFO: `RAID_LEADER_CUT_PERCENTAGE` is 0.0 or not defined. No Raid Leader cut applied by default.")
        
        print(f"INFO: ALLOWED_ROLES_FOR_ADMIN_CMDS set to: {config.ALLOWED_ROLES_FOR_ADMIN_CMDS}")
        print(f"INFO: TARGET_GUILD_IDS set to: {config.TARGET_GUILD_IDS}")

        try:
            bot.run(bot_token_to_check)
        except discord.LoginFailure:
            print("ERROR: Failed to log in. Check if BOT_TOKEN is correct and valid.")
        except Exception as e:
            print(f"ERROR: An unexpected error occurred while trying to run the bot: {e}")
            import traceback
            traceback.print_exc()