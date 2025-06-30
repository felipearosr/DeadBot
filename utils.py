# utils.py
import discord
import io
# import csv # No longer needed for alts if fully on DB
import os # Still useful for some path operations if any remain, but not for core data
import json # Still needed for active_boosters/benched_players JSONB
import datetime
from typing import List, Dict, Tuple, TypedDict, Optional, Any # Any for db args
import asyncpg # New import

# import config # Not strictly needed if DATABASE_URL is passed around, but can be for consistency

# --- Type Hinting ---
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

# --- Database Helper Functions ---
async def db_execute(pool: asyncpg.Pool, query: str, *args: Any) -> None:
    """Executes a query that doesn't return rows (INSERT, UPDATE, DELETE)."""
    async with pool.acquire() as connection:
        async with connection.transaction(): # Ensure atomic operations
            await connection.execute(query, *args)

async def db_fetchrow(pool: asyncpg.Pool, query: str, *args: Any) -> Optional[asyncpg.Record]:
    """Fetches a single row."""
    async with pool.acquire() as connection:
        return await connection.fetchrow(query, *args)

async def db_fetch(pool: asyncpg.Pool, query: str, *args: Any) -> List[asyncpg.Record]:
    """Fetches all rows."""
    async with pool.acquire() as connection:
        return await connection.fetch(query, *args)

# --- Core Database Interaction Functions ---
async def setup_database_tables(pool: asyncpg.Pool) -> None:
    """Creates tables if they don't exist."""
    async with pool.acquire() as connection:
        async with connection.transaction():
            # Alts Table
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS alts (
                    discord_user_id TEXT PRIMARY KEY,
                    payment_alt_name TEXT NOT NULL,
                    faction TEXT NOT NULL
                );
            """)
            print("Table 'alts' checked/created.")
            # Run Logs Table
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS run_logs (
                    log_id SERIAL PRIMARY KEY,
                    run_date DATE,
                    wcl_link TEXT,
                    total_gold INTEGER,
                    raid_leader_cut_percentage REAL,
                    raid_leader_share_gold REAL,
                    guild_cut_percentage REAL,
                    guild_share_gold REAL,
                    gold_per_booster REAL,
                    num_boosters INTEGER,
                    active_boosters JSONB,
                    benched_players JSONB,
                    processed_by_user_id TEXT,
                    processed_by_username TEXT,
                    timestamp_utc TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'utc')
                );
            """)
            print("Table 'run_logs' checked/created.")
        print("Database tables setup complete.")

async def load_all_alt_mappings_from_db(pool: asyncpg.Pool) -> Dict[str, AltInfo]:
    records = await db_fetch(pool, "SELECT discord_user_id, payment_alt_name, faction FROM alts")
    loaded_mappings: Dict[str, AltInfo] = {}
    for record in records:
        loaded_mappings[str(record['discord_user_id'])] = { # Ensure user_id is string
            "alt": record['payment_alt_name'],
            "faction": record['faction']
        }
    return loaded_mappings

async def get_alt_from_db(pool: asyncpg.Pool, discord_user_id: str) -> Optional[AltInfo]:
    record = await db_fetchrow(pool, 
        "SELECT payment_alt_name, faction FROM alts WHERE discord_user_id = $1", 
        discord_user_id)
    if record:
        return {"alt": record['payment_alt_name'], "faction": record['faction']}
    return None

async def save_alt_to_db(pool: asyncpg.Pool, discord_user_id: str, alt_name: str, faction: str) -> None:
    query = """
        INSERT INTO alts (discord_user_id, payment_alt_name, faction)
        VALUES ($1, $2, $3)
        ON CONFLICT (discord_user_id) DO UPDATE SET
            payment_alt_name = EXCLUDED.payment_alt_name,
            faction = EXCLUDED.faction;
    """
    await db_execute(pool, query, discord_user_id, alt_name, faction)

async def load_all_run_logs_from_db(pool: asyncpg.Pool) -> List[Dict]:
    records = await db_fetch(pool, """
        SELECT log_id, run_date, wcl_link, total_gold, raid_leader_cut_percentage, 
               raid_leader_share_gold, guild_cut_percentage, guild_share_gold, 
               gold_per_booster, num_boosters, active_boosters, benched_players, 
               processed_by_user_id, processed_by_username, timestamp_utc 
        FROM run_logs ORDER BY timestamp_utc DESC
    """)
    # Convert asyncpg.Record to dict; asyncpg handles JSONB to Python dict/list automatically
    return [dict(record) for record in records]

async def save_run_log_entry_to_db(pool: asyncpg.Pool, log_entry: Dict) -> None:
    """Saves a single run log entry to the database."""
    query = """
        INSERT INTO run_logs (
            run_date, wcl_link, total_gold, raid_leader_cut_percentage,
            raid_leader_share_gold, guild_cut_percentage, guild_share_gold,
            gold_per_booster, num_boosters, active_boosters, benched_players,
            processed_by_user_id, processed_by_username, timestamp_utc
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
        );
    """
    # Convert date string to datetime.date object if it's not already
    run_date_obj = log_entry.get("run_date")
    if isinstance(run_date_obj, str):
        try:
            run_date_obj = datetime.datetime.strptime(run_date_obj, '%Y-%m-%d').date()
        except ValueError:
            print(f"Warning: Could not parse run_date string '{log_entry.get('run_date')}' for DB insert. Using NULL.")
            run_date_obj = None
    
    # Convert timestamp_utc string to datetime object if it's not already
    timestamp_utc_obj = log_entry.get("timestamp_utc")
    if isinstance(timestamp_utc_obj, str): # Should already be a datetime object from /cut
        try:
            timestamp_utc_obj = datetime.datetime.fromisoformat(timestamp_utc_obj)
        except ValueError:
            print(f"Warning: Could not parse timestamp_utc string '{log_entry.get('timestamp_utc')}' for DB insert. Using NULL.")
            timestamp_utc_obj = None
    elif not isinstance(timestamp_utc_obj, datetime.datetime): # If it's something else unexpected
        print(f"Warning: timestamp_utc is not a datetime object or recognized string. Type: {type(timestamp_utc_obj)}. Using NULL.")
        timestamp_utc_obj = None


    # ---- MODIFICATION HERE ----
    # Explicitly convert lists/dicts intended for JSONB columns to JSON strings
    active_boosters_json = json.dumps(log_entry.get("active_boosters", []))
    benched_players_json = json.dumps(log_entry.get("benched_players", []))
    # ---- END MODIFICATION ----

    await db_execute(pool, query,
        run_date_obj,
        log_entry.get("wcl_link"), 
        log_entry.get("total_gold"),
        log_entry.get("raid_leader_cut_percentage"), 
        log_entry.get("raid_leader_share_gold"),
        log_entry.get("guild_cut_percentage"), 
        log_entry.get("guild_share_gold"),
        log_entry.get("gold_per_booster"), 
        log_entry.get("num_boosters"),
        active_boosters_json,         # Pass the JSON string
        benched_players_json,         # Pass the JSON string
        log_entry.get("processed_by_user_id"), 
        log_entry.get("processed_by_username"),
        timestamp_utc_obj
    )

# --- Other Utility Functions (No DB Interaction) ---

def parse_roster_data(roster_string: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """
    Parses roster data from a string (CSV format from raid-helper).

    Correctly categorizes players based on the 'Role' column (the first column):
    - If Role is 'Absence', 'Tentative', or 'Bench', the player is benched.
    - Otherwise, the player is considered an ACTIVE BOOSTER for payout.
    """
    active_boosters_with_ids: List[Tuple[str, str]] = []
    benched_players_names: List[str] = []

    # Define all roles that should NOT receive a payout.
    # Using a set is efficient and makes the code easy to read.
    EXCLUDED_ROLES = {"absence", "tentative", "bench"}

    # Use the csv module to robustly handle CSV data, even with commas in names.
    # StringIO treats the input string like a file.
    reader = csv.reader(StringIO(roster_string))

    # Safely skip the header row.
    try:
        header = next(reader)
        # Optional: you could validate the header here if you want
        # if header != ["Role", "Spec", "Name", "ID", "Timestamp", "Status"]:
        #     print("Warning: CSV header does not match expected format.")
    except StopIteration:
        # This happens if the file is empty.
        return [], []

    # Process each data row.
    for row in reader:
        # Ensure the row has enough columns to avoid errors.
        if len(row) < 4:
            continue

        # Extract data based on column index, stripping extra whitespace.
        role = row[0].strip().lower()
        player_name = row[2].strip()
        discord_id = row[3].strip()

        # Skip if essential data is missing.
        if not player_name or not discord_id:
            continue

        # --- THIS IS THE CORRECTED LOGIC ---
        if role in EXCLUDED_ROLES:
            # If the role is in our exclusion list, add them to the benched list.
            benched_players_names.append(player_name)
        else:
            # Otherwise, they are an active booster.
            # We still validate that the Discord ID is a number before adding.
            if discord_id.isdigit():
                active_boosters_with_ids.append((player_name, discord_id))

    return active_boosters_with_ids, benched_players_names

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

    if interaction.response.is_done():
        send_method = interaction.followup.send
    else:
        print("WARNING: send_long_message_or_file called before interaction was deferred/responded.")
        try:
            await interaction.response.defer(ephemeral=ephemeral) 
        except discord.InteractionResponded:
            pass 
        send_method = interaction.followup.send

    if len(full_message) > 1950:
        output_file = io.StringIO()
        output_file.write(full_message)
        output_file.seek(0)
        intro_text = f"Output too long, attached as `{filename}`."
        if primary_content and len(primary_content) < 300 :
            intro_text = f"{primary_content}\n... (additional details in attached file `{filename}`)"
        elif not primary_content and secondary_content:
             intro_text = f"Details attached as `{filename}`."
        await send_method(intro_text, file=discord.File(fp=output_file, filename=filename), ephemeral=ephemeral)
    else:
        if not full_message.strip():
            await send_method("No information to display.", ephemeral=ephemeral)
        else:
            await send_method(full_message, ephemeral=ephemeral)