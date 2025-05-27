import discord
from discord import app_commands
from discord.ext import commands
import io
import csv
import os
import json
import datetime
from typing import List, Dict, Tuple, Literal, TypedDict, Union, Optional

import config

# --- Type Hinting & Bot State ---
class AltInfo(TypedDict): alt: str; faction: str
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
alt_mappings: Dict[str, AltInfo] = {}
run_logs: List[Dict] = []

# --- Helper Functions ---
def is_valid_date(date_string: str) -> bool:
    try: datetime.datetime.strptime(date_string, '%Y-%m-%d'); return True
    except ValueError: return False

def load_alt_mappings():
    global alt_mappings
    alt_mappings = {}
    if not os.path.exists(config.ALTS_FILE):
        with open(config.ALTS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["DiscordUserID", "PaymentAltName", "Faction"])
        print(f"{config.ALTS_FILE} created.")
        return
    try:
        with open(config.ALTS_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("DiscordUserID") and row.get("PaymentAltName") and row.get("Faction"):
                    alt_mappings[str(row["DiscordUserID"])] = {
                        "alt": row["PaymentAltName"],
                        "faction": row["Faction"].capitalize()
                    }
    except Exception as e:
        print(f"Error loading alt mappings from {config.ALTS_FILE}: {e}")
        if 'row' in locals() and row: print(f"Potentially problematic row data: {row}")
        else: print("Could not identify specific problematic row, or error occurred before reading rows.")

def save_alt_mappings():
    global alt_mappings;
    try:
        with open(config.ALTS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["DiscordUserID", "PaymentAltName", "Faction"])
            for discord_id, info in alt_mappings.items():
                writer.writerow([discord_id, info["alt"], info["faction"]])
    except Exception as e: print(f"Error saving alt mappings to {config.ALTS_FILE}: {e}")

def parse_roster_data(roster_string: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    active_boosters_with_ids: List[Tuple[str, str]] = []; benched_players_names: List[str] = []
    lines = roster_string.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n'); player_data_start_index = -1
    expected_header = "role,spec,name,id,timestamp,status"
    for i, line_content in enumerate(lines):
        if line_content.strip().lower() == expected_header: player_data_start_index = i + 1; break
    if player_data_start_index == -1: print("DEBUG: Roster header not found."); return [], []
    for line_content in lines[player_data_start_index:]:
        stripped_line = line_content.strip();
        if not stripped_line: continue
        parts = [p.strip() for p in stripped_line.split(',')]
        if len(parts) >= 4:
            role_or_class = parts[0]; player_name = parts[2]; discord_id_str = parts[3]
            if role_or_class.lower() not in ["absence", "bench"] and player_name and discord_id_str.isdigit():
                active_boosters_with_ids.append((player_name, discord_id_str))
            elif role_or_class.lower() == "bench" and player_name:
                benched_players_names.append(player_name)
    return active_boosters_with_ids, benched_players_names

def load_run_logs():
    global run_logs; run_logs = []
    if os.path.exists(config.RUN_LOGS_FILE):
        try:
            with open(config.RUN_LOGS_FILE, 'r', encoding='utf-8') as f: run_logs = json.load(f)
            print(f"Loaded {len(run_logs)} logs.")
        except Exception as e: print(f"Error loading logs: {e}"); run_logs = []
    else: print(f"{config.RUN_LOGS_FILE} not found.")

def save_run_log_entry(log_entry: Dict):
    global run_logs; run_logs.append(log_entry)
    try:
        with open(config.RUN_LOGS_FILE, 'w', encoding='utf-8') as f: json.dump(run_logs, f, indent=4)
        print(f"Saved log. Total: {len(run_logs)}")
    except Exception as e: print(f"Error saving logs: {e}")

# MODIFIED send_long_message_or_file
async def send_long_message_or_file(interaction: discord.Interaction, 
                                    primary_content: str, 
                                    secondary_content: str, 
                                    filename: str, 
                                    ephemeral: bool = False):
    
    full_message = primary_content
    if secondary_content: # Only append if there's secondary content
        # Ensure there's a clear separation if both primary and secondary exist
        if primary_content and not primary_content.endswith("\n\n"):
            if not primary_content.endswith("\n"):
                full_message += "\n" # Add one newline if none
            full_message += "\n" # Add another for a blank line
        full_message += secondary_content

    # In /cut, after defer, it's always a followup
    send_method = interaction.followup.send 

    if len(full_message) > 1950: # Discord's message limit
        output_file = io.StringIO()
        output_file.write(full_message)
        output_file.seek(0)
        
        intro_text = f"Output too long, attached as `{filename}`."
        # Try to provide a more context-aware intro if possible
        if primary_content and len(primary_content) < 300 : # If primary_content is short enough to show
            intro_text = f"{primary_content}\n... (additional details in attached file `{filename}`)"
        elif not primary_content and secondary_content: # Only secondary content was provided, and it's long
             intro_text = f"Details attached as `{filename}`." # Generic if only secondary exists and is long

        await send_method(intro_text, file=discord.File(fp=output_file, filename=filename), ephemeral=ephemeral)
    else:
        if not full_message.strip(): # Check if the message is effectively empty
            await send_method("No payment information or warnings to display.", ephemeral=ephemeral)
        else:
            await send_method(full_message, ephemeral=ephemeral)


# --- Bot Class ---
class RaidManagerBot(commands.Bot):
    def __init__(self): intents = discord.Intents.default(); super().__init__(command_prefix="!", intents=intents)
    async def setup_hook(self):
        guild_obj = discord.Object(id=config.GUILD_ID) if config.GUILD_ID and config.GUILD_ID != 0 and str(config.GUILD_ID).lower() != "your_discord_server_id" else None
        if guild_obj: self.tree.copy_global_to(guild=guild_obj); await self.tree.sync(guild=guild_obj)
        else: await self.tree.sync()
        print(f"Synced commands."); load_alt_mappings(); load_run_logs()
        print(f"Loaded {len(alt_mappings)} alts and {len(run_logs)} logs.")
    async def on_ready(self): print(f'Logged in as {self.user.name}')
bot = RaidManagerBot()

# --- Slash Commands ---
@bot.tree.command(name="log", description="Export all run logs to a CSV file. (Admin Only)")
@app_commands.checks.has_any_role(*config.ALLOWED_ROLES_FOR_ADMIN_CMDS)
async def log_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not run_logs: await interaction.followup.send("No logs.", ephemeral=True); return
    output = io.StringIO()
    fieldnames = [
        "run_date", "wcl_link", "total_gold_run", "guild_share_run",
        "raid_leader_share_run", "gold_per_booster_run", "booster_name",
        "booster_discord_id", "is_benched_player_on_this_row", "run_processed_by",
        "run_timestamp_utc"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for idx, log_entry in enumerate(run_logs):
        active_list = log_entry.get("active_boosters", [])
        benched_list = log_entry.get("benched_players", [])
        max_r = max(1, len(active_list), len(benched_list))
        for i in range(max_r):
            row_data = {}
            if i == 0:
                row_data["run_date"] = log_entry.get("run_date")
                row_data["wcl_link"] = log_entry.get("wcl_link")
                row_data["total_gold_run"] = log_entry.get("total_gold")
                row_data["guild_share_run"] = log_entry.get("guild_share_gold")
                row_data["raid_leader_share_run"] = log_entry.get("raid_leader_share_gold")
                row_data["gold_per_booster_run"] = log_entry.get("gold_per_booster")
                row_data["run_processed_by"] = log_entry.get("processed_by_username")
                row_data["run_timestamp_utc"] = log_entry.get("timestamp_utc")
            if i < len(active_list):
                booster = active_list[i]
                row_data["booster_name"] = booster.get("name")
                row_data["booster_discord_id"] = booster.get("discord_id")
            else:
                row_data["booster_name"] = ""
                row_data["booster_discord_id"] = ""
            if i < len(benched_list):
                row_data["is_benched_player_on_this_row"] = benched_list[i]
            else:
                row_data["is_benched_player_on_this_row"] = ""
            if row_data: writer.writerow(row_data)
        if max_r > 0 and len(run_logs) > 1 and idx < len(run_logs) - 1 :
            writer.writerow({key: "" for key in fieldnames})
    output.seek(0)
    await interaction.followup.send("Run logs:", file=discord.File(fp=output, filename="all_run_logs.csv"), ephemeral=True)

@log_command.error
async def log_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    send_method = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    if isinstance(error, app_commands.MissingAnyRole): await send_method("No permission.", ephemeral=True)
    else: print(f"Err log: {error}"); await send_method("Error generating log.", ephemeral=True)

@bot.tree.command(name="export", description="Export CURRENT run's booster/benched lists to CSV.")
async def export_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    if not current_run_session.data_loaded:
        await interaction.followup.send("No current run data loaded. Use `/cut` first.", ephemeral=True)
        return
    output = io.StringIO(); writer = csv.writer(output); writer.writerow(["Booster Name", "Booster Discord ID", "Benched"])
    max_len = max(len(current_run_session.active_boosters), len(current_run_session.benched_players))
    for i in range(max_len):
        b_name = current_run_session.active_boosters[i][0] if i < len(current_run_session.active_boosters) else ""
        b_id = current_run_session.active_boosters[i][1] if i < len(current_run_session.active_boosters) else ""
        bench_p = current_run_session.benched_players[i] if i < len(current_run_session.benched_players) else ""
        writer.writerow([b_name, b_id, bench_p])
    output.seek(0)
    await interaction.followup.send("Current run export:", file=discord.File(fp=output, filename="current_run_export.csv"))

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
    # Initial defer is ephemeral. If we succeed, the public embed will be a new, non-ephemeral followup.
    await interaction.response.defer(ephemeral=True)

    async def send_error_and_return(message: str): # Helper to send error and ensure return
        await interaction.followup.send(message, ephemeral=True)
        # No explicit return here, the caller of send_error_and_return should return

    # --- Initial Validations ---
    if not is_valid_date(run_date):
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

    current_run_session.reset()
    current_run_session.run_date = run_date
    current_run_session.wcl_link = warcraft_logs_link
    current_run_session.total_gold = total_gold
    current_run_session.roster_raw = roster_data_string

    active_boosters_with_ids, benched_players_names = parse_roster_data(roster_data_string)
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
        print(f"INFO: Applying configured RL cut of {actual_rl_cut_percentage*100:.1f}% ({calculated_raid_leader_share_amount:,.2f}g) for run on {run_date}.")

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
    save_run_log_entry(log_entry)

    # --- Send Main Success Embeds (Public and Admin) ---
    # These are sent first if all core calculations are successful.

    # Public Embed
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
    await interaction.followup.send(embed=public_embed, ephemeral=False) # Send as a new, public message

    # Admin Embed
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
    await interaction.followup.send(embed=admin_embed, ephemeral=True) # Send as a new, ephemeral message

    # --- Payment String Generation (Now happens AFTER main embeds) ---
    if payment_subject and payment_body:
        print("DEBUG: Entered payment string generation block (subject and body provided).")
        if gold_per_booster_for_payment <= 0 and active_boosters_with_ids:
            print(f"DEBUG: Condition met - Gold per booster <= 0 ({gold_per_booster_for_payment}) AND active boosters exist.")
            await interaction.followup.send("Gold per booster is 0 or less. No payment strings generated.", ephemeral=True)
            return # Return after this specific payment-related message
        if not active_boosters_with_ids: # This check is technically redundant due to earlier check, but safe
            print("DEBUG: Condition met - No active boosters (within payment block).")
            await interaction.followup.send("No active boosters to generate payments for.", ephemeral=True)
            return # Return after this specific payment-related message

        print("DEBUG: Proceeding with payment string/warning collection.")
        horde_salestools_parts: List[str] = []
        alliance_salestools_parts: List[str] = []
        missing_alt_warnings_list: List[str] = []

        if not alt_mappings and os.path.exists(config.ALTS_FILE):
            print("DEBUG: Alt mappings were empty, attempting to load.")
            load_alt_mappings()
            if not alt_mappings:
                 print("DEBUG: Alt mappings still empty after load attempt.")

        for char_name, discord_id in active_boosters_with_ids:
            # ... (alt lookup logic as before) ...
            alt_info = alt_mappings.get(discord_id)
            if alt_info and alt_info.get("alt") and alt_info.get("faction"):
                alt_name_for_payment = alt_info['alt']
                if "-Area52" not in alt_name_for_payment.replace(" ", ""):
                    alt_name_for_payment += "-Area52"
                salestool_part = f"{alt_name_for_payment}:{payment_subject}:{gold_per_booster_for_payment}:{payment_body}"
                if alt_info['faction'].lower() == "horde":
                    horde_salestools_parts.append(salestool_part)
                elif alt_info['faction'].lower() == "alliance":
                    alliance_salestools_parts.append(salestool_part)
                else:
                    missing_alt_warnings_list.append(f"Unknown faction for Discord ID `{discord_id}` ({char_name}). Alt: {alt_info['alt']}, Faction: {alt_info['faction']}")
            else:
                missing_alt_warnings_list.append(f"No Alt/Faction registered for ID `{discord_id}` ({char_name}).")
        
        payment_output_sections = []
        any_payment_generated = False # Tracks if any SalesTools strings were made

        # Horde Section - MODIFIED FORMATTING
        if horde_salestools_parts: # This list contains "Char-Realm:Subj:Gold:Body" strings
            any_payment_generated = True
            # Format: **Horde SalesTools (Count):** \n ``` \n part1 \n part2 \n part3 \n ```
            horde_block = f"**Horde SalesTools ({len(horde_salestools_parts)}):**\n"
            horde_block += "```\n"
            horde_block += "\n".join(horde_salestools_parts) # Join each part with a newline
            horde_block += "\n```"
            payment_output_sections.append(horde_block)
        else:
            payment_output_sections.append("**Horde Payouts:** None.")

        # Alliance Section - MODIFIED FORMATTING
        if alliance_salestools_parts: # This list contains "Char-Realm:Subj:Gold:Body" strings
            any_payment_generated = True
            alliance_block = f"**Alliance SalesTools ({len(alliance_salestools_parts)}):**\n"
            alliance_block += "```\n"
            alliance_block += "\n".join(alliance_salestools_parts) # Join each part with a newline
            alliance_block += "\n```"
            payment_output_sections.append(alliance_block)
        else:
            payment_output_sections.append("**Alliance Payouts:** None.")
        
        payment_strings_content = "\n\n".join(payment_output_sections)
        
        warnings_content = ""
        if missing_alt_warnings_list:
            warnings_content = "\n\n**Payment Warnings:**\n" + "\n".join(missing_alt_warnings_list)

        if not any_payment_generated and not missing_alt_warnings_list:
            await interaction.followup.send("Payment: No boosters processed or warnings to display.", ephemeral=True)
        else:
            await send_long_message_or_file(interaction, 
                                            payment_strings_content, 
                                            warnings_content, 
                                            "payment_details.txt", 
                                            ephemeral=True)
        return 

    elif payment_subject or payment_body:
        await interaction.followup.send("To generate payment strings, please provide both a `payment_subject` AND a `payment_body`.", ephemeral=True)
        return # Return after this message
    else: 
        print("DEBUG: Condition met - Neither payment_subject nor payment_body was provided. Skipping payment string generation.")
        # No payment strings requested, so the command is complete after sending public/admin embeds.
        return # Explicitly return


@cut_command.error
async def cut_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    send_method = interaction.followup.send
    if not interaction.response.is_done():
        try:
            if isinstance(error, app_commands.MissingAnyRole):
                await interaction.response.send_message("You do not have the required role to use this command.", ephemeral=True)
            else:
                print(f"Error in /cut command (before defer or during initial response): {error}")
                await interaction.response.send_message("An unexpected error occurred. Please check bot logs.", ephemeral=True)
            return
        except discord.InteractionResponded: pass
        except Exception as e: print(f"Critical error sending initial error response for /cut: {e}"); return

    if isinstance(error, app_commands.MissingAnyRole):
        await send_method("You do not have the required role to use this command.", ephemeral=True)
    else:
        print(f"Error in /cut command: {error}")
        await send_method("An unexpected error occurred while processing the cut. Please check the bot logs.", ephemeral=True)

# --- Main Execution Block ---
if __name__ == "__main__":
    guild_id_to_check = getattr(config, 'GUILD_ID', 0)
    bot_token_to_check = getattr(config, 'BOT_TOKEN', "")
    allowed_roles_to_check = getattr(config, 'ALLOWED_ROLES_FOR_ADMIN_CMDS', [])
    rl_cut_percentage_config = getattr(config, 'RAID_LEADER_CUT_PERCENTAGE', 0.0)

    if str(guild_id_to_check).lower() == "your_discord_server_id" or guild_id_to_check == 0:
        print(f"ERROR: Please set your actual GUILD_ID in config.py. Current: {guild_id_to_check}")
    elif bot_token_to_check == "YOUR_BOT_TOKEN" or not bot_token_to_check:
        print("ERROR: Please set your BOT_TOKEN in config.py.")
    elif not allowed_roles_to_check or \
         (isinstance(allowed_roles_to_check, list) and len(allowed_roles_to_check) == 4 and
          "Admin" in allowed_roles_to_check and "Officer" in allowed_roles_to_check and
          "Sales Leader" in allowed_roles_to_check and 123456789012345678 in allowed_roles_to_check):
        print("WARNING: config.ALLOWED_ROLES_FOR_ADMIN_CMDS in config.py might be using default placeholder values. Please configure with your specific role names or IDs.")
        bot.run(bot_token_to_check)
    else:
        if rl_cut_percentage_config > 0.0:
             print(f"INFO: `RAID_LEADER_CUT_PERCENTAGE` is set to {rl_cut_percentage_config*100:.1f}%. "
                   "This amount will be deducted for the Raid Leader (manual payout).")
        else:
             print("INFO: `RAID_LEADER_CUT_PERCENTAGE` is 0.0 or not defined in config.py. No Raid Leader cut will be applied by default.")
        bot.run(bot_token_to_check)