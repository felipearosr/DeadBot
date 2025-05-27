# utils.py
import discord
import io
import csv
import os
import json
import datetime
from typing import List, Dict, Tuple, TypedDict, Optional

import config

# --- Type Hinting (moved from main bot script) ---
class AltInfo(TypedDict):
    alt: str
    faction: str

# --- Helper Functions ---
def is_valid_date(date_string: str) -> bool:
    try:
        datetime.datetime.strptime(date_string, '%Y-%m-%d')
        return True
    except ValueError:
        return False

def load_alt_mappings(file_path: str) -> Dict[str, AltInfo]:
    loaded_alt_mappings: Dict[str, AltInfo] = {}
    if not os.path.exists(file_path):
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["DiscordUserID", "PaymentAltName", "Faction"])
        print(f"INFO: {file_path} created.")
        return loaded_alt_mappings

    try:
        with open(file_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("DiscordUserID") and row.get("PaymentAltName") and row.get("Faction"):
                    loaded_alt_mappings[str(row["DiscordUserID"])] = {
                        "alt": row["PaymentAltName"],
                        "faction": row["Faction"].capitalize()
                    }
    except Exception as e:
        print(f"ERROR: Error loading alt mappings from {file_path}: {e}")
        if 'row' in locals() and row: print(f"Potentially problematic row data: {row}")
        else: print("Could not identify specific problematic row, or error occurred before reading rows.")
    return loaded_alt_mappings

def save_alt_mappings(mappings_data: Dict[str, AltInfo], file_path: str) -> None:
    try:
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["DiscordUserID", "PaymentAltName", "Faction"])
            for discord_id, info in mappings_data.items():
                writer.writerow([discord_id, info["alt"], info["faction"]])
    except Exception as e:
        print(f"ERROR: Error saving alt mappings to {file_path}: {e}")

def parse_roster_data(roster_string: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    active_boosters_with_ids: List[Tuple[str, str]] = []
    benched_players_names: List[str] = []
    lines = roster_string.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')
    player_data_start_index = -1
    expected_header = "role,spec,name,id,timestamp,status"

    for i, line_content in enumerate(lines):
        if line_content.strip().lower() == expected_header:
            player_data_start_index = i + 1
            break

    if player_data_start_index == -1:
        print("DEBUG: Roster header not found.")
        return [], []

    for line_content in lines[player_data_start_index:]:
        stripped_line = line_content.strip()
        if not stripped_line:
            continue
        parts = [p.strip() for p in stripped_line.split(',')]
        if len(parts) >= 4:
            role_or_class = parts[0]
            player_name = parts[2]
            discord_id_str = parts[3]
            if role_or_class.lower() not in ["absence", "bench"] and player_name and discord_id_str.isdigit():
                active_boosters_with_ids.append((player_name, discord_id_str))
            elif role_or_class.lower() == "bench" and player_name:
                benched_players_names.append(player_name)
    return active_boosters_with_ids, benched_players_names

def load_run_logs(file_path: str) -> List[Dict]:
    loaded_run_logs: List[Dict] = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                loaded_run_logs = json.load(f)
        except Exception as e:
            print(f"ERROR: Error loading logs from {file_path}: {e}")
            loaded_run_logs = []  # Ensure it returns an empty list on error
    else:
        print(f"INFO: {file_path} not found. Will be created on first save if applicable.")
    return loaded_run_logs

def save_run_logs(logs_data: List[Dict], file_path: str) -> None:
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(logs_data, f, indent=4)
    except Exception as e:
        print(f"ERROR: Error saving logs to {file_path}: {e}")

async def send_long_message_or_file(interaction: discord.Interaction,
                                    primary_content: str,
                                    secondary_content: str,
                                    filename: str,
                                    ephemeral: bool = False):
    full_message = primary_content
    if secondary_content:
        if primary_content and not primary_content.endswith("\n\n"):
            if not primary_content.endswith("\n"):
                full_message += "\n"
            full_message += "\n"
        full_message += secondary_content

    send_method = interaction.followup.send

    if len(full_message) > 1950:
        output_file = io.StringIO()
        output_file.write(full_message)
        output_file.seek(0)

        intro_text = f"Output too long, attached as `{filename}`."
        if primary_content and len(primary_content) < 300:
            intro_text = f"{primary_content}\n... (additional details in attached file `{filename}`)"
        elif not primary_content and secondary_content:
             intro_text = f"Details attached as `{filename}`."

        await send_method(intro_text, file=discord.File(fp=output_file, filename=filename), ephemeral=ephemeral)
    else:
        if not full_message.strip():
            await send_method("No payment information or warnings to display.", ephemeral=ephemeral)
        else:
            await send_method(full_message, ephemeral=ephemeral)