import discord
from discord import app_commands
from discord.ext import commands
import io
import csv
import os
import json
import datetime
from typing import List, Dict, Tuple, Optional 

import config
import utils 
from utils import AltInfo

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
        self.alt_mappings: Dict[str, AltInfo] = {}
        self.run_logs: List[Dict] = []

    async def setup_hook(self):
        for extension in self.initial_extensions:
            try:
                await self.load_extension(extension)
                print(f"Successfully loaded extension: {extension}")
            except Exception as e:
                print(f"Failed to load extension {extension}:")
                import traceback
                traceback.print_exc()

        # --- COMMAND SYNCING ---
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
        else: # No target guilds specified, sync globally (if not cleared)
            if not CLEAR_GLOBAL_COMMANDS_ONCE: # Only sync globally if we are not in the process of clearing them
                print("No TARGET_GUILD_IDS specified in config. Attempting global command sync.")
                try:
                    synced_commands = await self.tree.sync()
                    print(f"Synced {len(synced_commands)} global application commands.")
                except Exception as e_global_sync:
                    print(f"Error during global command sync: {e_global_sync}")
        
        print("Command syncing process complete. Review logs.")
        # --- END COMMAND SYNCING ---
           
        self.alt_mappings = utils.load_alt_mappings(config.ALTS_FILE)
        self.run_logs = utils.load_run_logs(config.RUN_LOGS_FILE)
        print(f"Loaded {len(self.alt_mappings)} alts and {len(self.run_logs)} logs.")

    async def on_ready(self):
        print(f'Logged in as {self.user.name} ({self.user.id})')

bot = RaidManagerBot()

# --- User-Facing Slash Commands in bot.py ---
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
    bot_instance.alt_mappings[user_id] = {"alt": payment_alt_name.strip(), "faction": faction.value}
    utils.save_alt_mappings(bot_instance.alt_mappings, config.ALTS_FILE)

    await interaction.followup.send(
        f"Your payment character has been set/updated to: **{payment_alt_name}** ({faction.name}).",
        ephemeral=True
    )
    print(f"User {interaction.user.name} ({user_id}) set payment char to {payment_alt_name} ({faction.name}).")

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
@bot.tree.command(name="cut", description="Calc gold, log, & optionally generate payment strings. (Admin Only)")
@app_commands.checks.has_any_role(*config.ALLOWED_ROLES_FOR_ADMIN_CMDS)
@app_commands.describe(
    run_date="Date of the run (YYYY-MM-DD).",
    warcraft_logs_link="Link to the Warcraft Logs report.",
    total_gold="Total gold amount for the run.",
    roster_file="Upload roster .txt or .csv (Discord IDs in 4th column).",
    payment_subject="Subject for payment mail (triggers payment string generation).",
    payment_body="Body for payment mail."
)
async def cut_command(
    interaction: discord.Interaction,
    run_date: str,
    warcraft_logs_link: str,
    total_gold: int,
    roster_file: discord.Attachment,
    payment_subject: str,
    payment_body: str
):
    await interaction.response.defer(ephemeral=True)
    bot_instance: RaidManagerBot = interaction.client

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

    # current_run_session is still a module-level global for simplicity of this specific data object
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

    # --- Calculations (using current_run_session) ---
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
    gold_per_booster_for_payment = int(gold_per_booster_precise)
    current_run_session.gold_per_booster = gold_per_booster_precise
    current_run_session.data_loaded = True

    # --- Log Entry ---
    log_entry = {
        "run_date": run_date, "wcl_link": warcraft_logs_link, "total_gold": total_gold,
        "raid_leader_cut_percentage": actual_rl_cut_percentage,
        "raid_leader_share_gold": round(calculated_raid_leader_share_amount, 2),
        "guild_cut_percentage": guild_config_percentage,
        "guild_share_gold": round(guild_share_amount, 2),
        "gold_per_booster": round(gold_per_booster_precise, 2),
        "num_boosters": len(active_boosters_with_ids),
        "active_boosters": [{"name": n, "discord_id": d} for n, d in active_boosters_with_ids],
        "benched_players": benched_players_names,
        "processed_by_user_id": str(interaction.user.id),
        "processed_by_username": interaction.user.name,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    bot_instance.run_logs.append(log_entry) # Use bot instance attribute
    utils.save_run_logs(bot_instance.run_logs, config.RUN_LOGS_FILE)
    print(f"Saved log. Total: {len(bot_instance.run_logs)}")

    # --- Create Public Embed (for Raid Cut Summary - created early, sent later) ---
    public_embed = discord.Embed(title=f"Raid Cut Summary: {run_date}", color=discord.Color.green())
    public_embed.add_field(name="Logs", value=f"[{warcraft_logs_link.split('/')[-1] if '/' in warcraft_logs_link else 'Report'}]({warcraft_logs_link})", inline=False)
    public_embed.add_field(name="Booster Cut", value=f"{current_run_session.gold_per_booster:,.2f}g", inline=True)
    public_embed.add_field(name="Num. of Boosters", value=str(len(current_run_session.active_boosters)), inline=True)
    public_booster_names = [n for n, d in current_run_session.active_boosters]
    public_booster_list_str = "\n".join(public_booster_names)
    if len(public_booster_list_str) > 1020: public_booster_list_str = public_booster_list_str[:1020] + "..."
    public_embed.add_field(name=f"Active Boosters ({len(public_booster_names)})", value=public_booster_list_str or "None", inline=False)
    public_benched_list_str = "\n".join(current_run_session.benched_players)
    if len(public_benched_list_str) > 1020: public_benched_list_str = public_benched_list_str[:1020] + "..."
    public_embed.add_field(name=f"Benched ({len(current_run_session.benched_players)})", value=public_benched_list_str or "None", inline=False)
    public_embed.set_footer(text="Run processed. Admins have detailed view & payment options.")

    # --- Admin Embed (sent as an ephemeral followup) ---
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
    admin_booster_details = [f"{name} (ID: `{disc_id}`)" for name, disc_id in current_run_session.active_boosters]
    admin_booster_list_str = "\n".join(admin_booster_details)
    if len(admin_booster_list_str) > 1020: admin_booster_list_str = admin_booster_list_str[:1020] + "..."
    admin_embed.add_field(name=f"Active Boosters + IDs ({len(admin_booster_details)})", value=admin_booster_list_str or "None", inline=False)
    admin_embed.add_field(name=f"Benched ({len(current_run_session.benched_players)})", value=public_benched_list_str or "None", inline=False) # Re-use public_benched_list_str
    admin_embed.set_footer(text=f"Processed by: {interaction.user.display_name}")
    await interaction.followup.send(embed=admin_embed, ephemeral=True)

    # --- Generate Alt/Faction Warnings ---
    admin_alt_warnings_list: List[str] = [] 
    public_warning_ping_list: List[str] = []  

    if active_boosters_with_ids: # From roster parsing
        # No need to reload alt_mappings here if setup_hook handles it well
        # if not bot_instance.alt_mappings and os.path.exists(config.ALTS_FILE):
        #     bot_instance.alt_mappings = utils.load_alt_mappings(config.ALTS_FILE)

        for char_name, discord_id in active_boosters_with_ids:
            alt_info = bot_instance.alt_mappings.get(discord_id)
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
            
            if admin_warning_text:
                admin_alt_warnings_list.append(admin_warning_text)
            if public_ping_text:
                public_warning_ping_list.append(public_ping_text)
    
    # --- Payment String Generation (for Admin, if requested) & Ephemeral Warning Send ---
    payment_followup_sent = False
    admin_warnings_content_for_ephemeral = ""
    if admin_alt_warnings_list:
        admin_warnings_content_for_ephemeral = "\n\n**Payment/Alt Warnings (for your reference):**\n" + "\n".join(admin_alt_warnings_list)

    if payment_subject and payment_body:
        admin_payment_strings_content = ""
        if gold_per_booster_for_payment <= 0 and active_boosters_with_ids:
            await interaction.followup.send("Gold per booster is 0 or less. No payment strings generated." + admin_warnings_content_for_ephemeral, ephemeral=True)
            payment_followup_sent = True
        elif not active_boosters_with_ids: # Should have been caught earlier
            await interaction.followup.send("No active boosters to generate payments for." + admin_warnings_content_for_ephemeral, ephemeral=True)
            payment_followup_sent = True
        else:
            horde_salestools_parts: List[str] = []
            alliance_salestools_parts: List[str] = []
            for char_name, discord_id in active_boosters_with_ids: # from roster parsing
                alt_info = bot_instance.alt_mappings.get(discord_id) # Use bot_instance for alts
                if alt_info and alt_info.get("alt") and alt_info.get("faction"):
                    alt_faction_lower = alt_info['faction'].lower()
                    if alt_faction_lower in ["horde", "alliance"]:
                        alt_name_for_payment = alt_info['alt']
                        if "-Area52" not in alt_name_for_payment.replace(" ", ""): 
                            alt_name_for_payment += "-Area52"
                        salestool_part = f"{alt_name_for_payment}:{payment_subject}:{gold_per_booster_for_payment}:{payment_body}"
                        if alt_faction_lower == "horde": horde_salestools_parts.append(salestool_part)
                        elif alt_faction_lower == "alliance": alliance_salestools_parts.append(salestool_part)
            
            payment_output_sections = []
            any_payment_generated = False
            if horde_salestools_parts: any_payment_generated = True; payment_output_sections.append(f"**Horde SalesTools ({len(horde_salestools_parts)}):**\n```\n" + "\n".join(horde_salestools_parts) + "\n```")
            else: payment_output_sections.append("**Horde Payouts:** None.")
            if alliance_salestools_parts: any_payment_generated = True; payment_output_sections.append(f"**Alliance SalesTools ({len(alliance_salestools_parts)}):**\n```\n" + "\n".join(alliance_salestools_parts) + "\n```")
            else: payment_output_sections.append("**Alliance Payouts:** None.")
            admin_payment_strings_content = "\n\n".join(payment_output_sections)

            if not any_payment_generated and not admin_alt_warnings_list:
                await interaction.followup.send("Payment: No boosters processed for payment strings and no alt warnings.", ephemeral=True)
            else:
                await utils.send_long_message_or_file(interaction, admin_payment_strings_content, admin_warnings_content_for_ephemeral, "payment_and_warnings_details.txt", ephemeral=True)
            payment_followup_sent = True
    elif admin_alt_warnings_list: # No payment subject/body, but warnings exist
        await utils.send_long_message_or_file(interaction, "No payment strings requested.", admin_warnings_content_for_ephemeral, "alt_warnings.txt", ephemeral=True)
        payment_followup_sent = True
    elif payment_subject or payment_body: # Malformed request
        await interaction.followup.send("To generate payment strings, please provide both `payment_subject` AND `payment_body`." + admin_warnings_content_for_ephemeral, ephemeral=True)
        payment_followup_sent = True
    
    if not payment_followup_sent and not (payment_subject and payment_body) and not admin_alt_warnings_list:
        await interaction.followup.send("Run processing complete. Admin details sent. No payment strings requested and no alt warnings.", ephemeral=True)
        print("DEBUG: Sent final ephemeral confirmation as no payment actions or warnings.")

    # --- Send Public Messages ---
    public_channel_obj: Optional[discord.TextChannel] = None # Renamed to avoid conflict if public_channel is used elsewhere
    target_channel_id = interaction.channel_id
    if target_channel_id:
        # Use bot_instance (which is interaction.client) to get channel
        _fetched_channel = bot_instance.get_channel(target_channel_id)
        # ... (rest of channel fetching logic, same as before)
        _source = "bot_instance.get_channel"
        if not _fetched_channel and interaction.guild:
            _fetched_channel = interaction.guild.get_channel(target_channel_id)
            _source = "interaction.guild.get_channel"
        # interaction.client is bot_instance, so no need for another .client.get_channel

        if _fetched_channel and isinstance(_fetched_channel, discord.TextChannel):
            public_channel_obj = _fetched_channel
            print(f"DEBUG: Public channel resolved to '{public_channel_obj.name}' ({public_channel_obj.id}) via {_source}.")
        elif _fetched_channel:
            print(f"ERROR: Target channel ({target_channel_id}, via {_source}) is not a TextChannel (type: {type(_fetched_channel)}). Name: '{getattr(_fetched_channel, 'name', 'N/A')}'. Cannot send public messages.")
        else:
            print(f"ERROR: Channel with ID {target_channel_id} not found by any method. Cannot send public messages.")
    else:
        print(f"ERROR: interaction.channel_id was None. Cannot send public messages.")

    # 1. Send Public Raid Cut Summary
    if public_channel_obj:
        try:
            await public_channel_obj.send(embed=public_embed)
            print(f"INFO: Public summary sent successfully to channel {public_channel_obj.name}.")
        # ... (exception handling for public summary, same as before, ensure public_channel_obj = None on failure) ...
        except discord.Forbidden:
            print(f"ERROR: Bot lacks permission for Public Summary in {public_channel_obj.name}.")
            try: await interaction.followup.send(f"Critical: Failed to send public raid summary to {public_channel_obj.mention} due to permissions.", ephemeral=True)
            except: pass 
            public_channel_obj = None 
        except Exception as e:
            print(f"ERROR: Unexpected error sending Public Summary: {e}")
            try: await interaction.followup.send(f"Critical: An unexpected error occurred sending public raid summary: {e}", ephemeral=True)
            except: pass
            public_channel_obj = None
    elif not public_channel_obj and target_channel_id: # Check if resolution failed earlier
        try: await interaction.followup.send("Critical: Could not resolve channel to send public messages. Summary/warnings not sent publicly.", ephemeral=True)
        except: pass
    
    # 2. Send Public Payment Warnings
    if public_channel_obj and public_warning_ping_list:
        public_warnings_title = "**⚠️ Public Alt/Faction Warnings**"
        public_warnings_content_str = "\n".join(public_warning_ping_list) 
        full_public_warning_message = f"{public_warnings_title}\n{public_warnings_content_str}"

        if len(full_public_warning_message) > 1950:
            # ... (file sending logic for public warnings, same as before, using public_channel_obj) ...
            warning_output_file = io.StringIO()
            warning_output_file.write(full_public_warning_message)
            warning_output_file.seek(0)
            try:
                await public_channel_obj.send(
                    f"{public_warnings_title}\n(List too long, warnings with pings attached as `payment_warnings.txt`)",
                    file=discord.File(fp=warning_output_file, filename="payment_warnings.txt"),
                    allowed_mentions=discord.AllowedMentions(users=True) 
                )
                print(f"INFO: Public payment warnings (with pings) sent as a file to {public_channel_obj.name}.")
            except discord.Forbidden: print(f"ERROR: Bot lacks permission for Public Warnings (file) in {public_channel_obj.name}.")
            except Exception as e: print(f"ERROR: Unexpected error sending Public Warnings (file): {e}")
        else:
            try:
                await public_channel_obj.send(
                    full_public_warning_message,
                    allowed_mentions=discord.AllowedMentions(users=True)
                )
                print(f"INFO: Public payment warnings (with pings) sent as a message to {public_channel_obj.name}.")
            except discord.Forbidden: print(f"ERROR: Bot lacks permission for Public Warnings (message) in {public_channel_obj.name}.")
            except Exception as e: print(f"ERROR: Unexpected error sending Public Warnings (message): {e}")
    elif public_warning_ping_list: # Warnings exist but public_channel_obj became invalid
        print("INFO: Public payment warnings (with pings) were generated but could not be sent publicly due to channel issue for summary.")

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
    # Ensure essential files exist
    if not os.path.exists(config.ALTS_FILE):
        with open(config.ALTS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["DiscordUserID", "PaymentAltName", "Faction"])
        print(f"INFO: {config.ALTS_FILE} created with headers.")
    
    if not os.path.exists(config.RUN_LOGS_FILE):
        with open(config.RUN_LOGS_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f) # Initialize with an empty list
        print(f"INFO: {config.RUN_LOGS_FILE} created as empty JSON array.")

    guild_id_to_check = getattr(config, 'TARGET_GUILD_IDS', 0)
    bot_token_to_check = getattr(config, 'BOT_TOKEN', "")
    allowed_roles_to_check = getattr(config, 'ALLOWED_ROLES_FOR_ADMIN_CMDS', [])
    rl_cut_percentage_config = getattr(config, 'RAID_LEADER_CUT_PERCENTAGE', 0.0)

    if str(guild_id_to_check).lower() == "your_discord_server_id" or guild_id_to_check == 0:
        print(f"ERROR: Please set your actual TARGET_GUILD_IDS in config.py. Current: {guild_id_to_check}")
    elif bot_token_to_check == "YOUR_BOT_TOKEN" or not bot_token_to_check:
        print("ERROR: Please set your BOT_TOKEN in config.py.")
    elif not allowed_roles_to_check or \
         (isinstance(allowed_roles_to_check, list) and len(allowed_roles_to_check) == 1 and
          allowed_roles_to_check[0] in ["YOUR_ADMIN_ROLE_NAME_OR_ID_1", 123456789012345678]): # Example placeholder
        print("WARNING: config.ALLOWED_ROLES_FOR_ADMIN_CMDS in config.py might be using default placeholder values or is empty. Please configure with your specific role names or IDs if admin commands are not working as expected.")
        bot.run(bot_token_to_check)
    else:
        if rl_cut_percentage_config > 0.0:
             print(f"INFO: `RAID_LEADER_CUT_PERCENTAGE` is set to {rl_cut_percentage_config*100:.1f}%. "
                   "This amount will be deducted for the Raid Leader (manual payout).")
        else:
             print("INFO: `RAID_LEADER_CUT_PERCENTAGE` is 0.0 or not defined in config.py. No Raid Leader cut will be applied by default.")
        bot.run(bot_token_to_check)