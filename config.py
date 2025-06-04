import os

TARGET_GUILD_IDS = [
    1010986192981995560,
    517622768741777408
] # Replace with your server's ID (integer)

PUBLIC_SUMMARY_CHANNEL_IDS = [
    1379685368529420288
]
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_FALLBACK_IF_NOT_SET_LOCALLY")
GUILD_CUT_PERCENTAGE = 0.035
RAID_LEADER_CUT_PERCENTAGE = 0.015
#ALTS_FILE = "alts_faction.csv"
#RUN_LOGS_FILE = "run_logs.json"
ALLOWED_ROLES_FOR_ADMIN_CMDS = ["Sales Leader"] # Example
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://user:pass@host:port/db_fallback")