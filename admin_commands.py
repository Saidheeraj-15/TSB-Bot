import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
import httpx
import os
import csv
import io

# ── CONFIG ────────────────────────────────────────────────────────────────────
LEADERBOARD_CHANNEL_ID = 1424749944882991114
GUILD_ID               = 1419384274376982540
STUDY_CATEGORY_ID      = 1424091569446850682

IST = timezone(timedelta(hours=5, minutes=30))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/").removesuffix("/rest/v1")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"

def db_get(table: str, params: dict) -> list:
    with httpx.Client() as c:
        r = c.get(_url(table), headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json() or []

def seconds_to_hm(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h >= 10:   return f"{h}h"
    elif h:       return f"{h}h {m}m"
    else:         return f"{m}m"


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN LEADERBOARD — full view, both weekly + monthly, all members
# ══════════════════════════════════════════════════════════════════════════════

def setup_admin_commands(bot):

    @bot.tree.command(
        name="adminleaderboard",
        description="View full weekly + monthly leaderboard for all members (admin only)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_leaderboard(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(IST)
        iso = now.isocalendar()

        try:
            # ── Fetch weekly data ─────────────────────────────────────────────
            weekly_rows = db_get("study_hours", {
                "week": f"eq.{iso[1]}",
                "year": f"eq.{iso[0]}",
                "select": "user_id,seconds"
            })
            weekly_totals = {}
            for r in weekly_rows:
                uid = r["user_id"]
                weekly_totals[uid] = weekly_totals.get(uid, 0) + r["seconds"]

            # ── Fetch monthly data ────────────────────────────────────────────
            monthly_rows = db_get("study_hours", {
                "month": f"eq.{now.month}",
                "year":  f"eq.{now.year}",
                "select": "user_id,seconds"
            })
            monthly_totals = {}
            for r in monthly_rows:
                uid = r["user_id"]
                monthly_totals[uid] = monthly_totals.get(uid, 0) + r["seconds"]

            # ── Add Lion bot base data ────────────────────────────────────────
            lion_rows = db_get("lion_import", {"select": "user_id,weekly_seconds,monthly_seconds"})
            for r in lion_rows:
                uid = r["user_id"]
                if r.get("weekly_seconds"):
                    weekly_totals[uid] = weekly_totals.get(uid, 0) + r["weekly_seconds"]
                if r.get("monthly_seconds"):
                    monthly_totals[uid] = monthly_totals.get(uid, 0) + r["monthly_seconds"]

            # ── Sort ──────────────────────────────────────────────────────────
            weekly_sorted  = sorted(weekly_totals.items(),  key=lambda x: x[1], reverse=True)
            monthly_sorted = sorted(monthly_totals.items(), key=lambda x: x[1], reverse=True)

            # ── Weekly embed ──────────────────────────────────────────────────
            e1 = discord.Embed(
                title=f"📊 Weekly Leaderboard — Week {iso[1]}, {iso[0]}",
                color=0xFEE75C,
                timestamp=datetime.now(timezone.utc)
            )
            w_lines = []
            for i, (uid, secs) in enumerate(weekly_sorted):
                try:
                    user = await bot.fetch_user(int(uid))
                    name = user.display_name
                except:
                    name = f"Unknown ({uid[:8]}...)"
                w_lines.append(f"`{i+1}.` **{name}** — `{seconds_to_hm(secs)}`")
            e1.description = "\n".join(w_lines) if w_lines else "No data yet."
            e1.set_footer(text=f"{len(weekly_sorted)} members tracked")

            # ── Monthly embed ─────────────────────────────────────────────────
            e2 = discord.Embed(
                title=f"📊 Monthly Leaderboard — {now.strftime('%B %Y')}",
                color=0xEB459E,
                timestamp=datetime.now(timezone.utc)
            )
            m_lines = []
            for i, (uid, secs) in enumerate(monthly_sorted):
                try:
                    user = await bot.fetch_user(int(uid))
                    name = user.display_name
                except:
                    name = f"Unknown ({uid[:8]}...)"
                m_lines.append(f"`{i+1}.` **{name}** — `{seconds_to_hm(secs)}`")
            e2.description = "\n".join(m_lines) if m_lines else "No data yet."
            e2.set_footer(text=f"{len(monthly_sorted)} members tracked")

            await interaction.followup.send(embeds=[e1, e2], ephemeral=True)

        except Exception as ex:
            print(f"❌ adminleaderboard error: {ex}")
            await interaction.followup.send(f"❌ Failed: `{ex}`", ephemeral=True)

    @admin_leaderboard.error
    async def admin_leaderboard_error(interaction, error):
        await interaction.response.send_message(
            "❌ You need **Administrator** permission.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# SETUP — called from bot.py
# ══════════════════════════════════════════════════════════════════════════════

async async def setup_admin(bot):
    setup_admin_commands(bot)
    print("✅ Admin commands module loaded")
