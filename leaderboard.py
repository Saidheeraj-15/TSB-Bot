import discord
from discord import app_commands
from discord.ext import tasks
import httpx
import csv
import io
from datetime import datetime, timezone, timedelta

# ── CONFIG ─────────────────────────────────────────────────────────────────────
LEADERBOARD_CHANNEL_ID  = 1424749944882991114
GUILD_ID                = 1419384274376982540
STUDY_CATEGORY_ID       = 1424091569446850682

IST = timezone(timedelta(hours=5, minutes=30))

import os
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


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def db_get(table: str, params: dict) -> list:
    with httpx.Client() as c:
        r = c.get(_url(table), headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json() or []

def db_insert(table: str, data: dict):
    with httpx.Client() as c:
        r = c.post(_url(table), headers=HEADERS, json=data)
        r.raise_for_status()

def db_upsert(table: str, data: dict, on_conflict: str):
    h = {**HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"}
    with httpx.Client() as c:
        r = c.post(_url(table), headers=h, json=data, params={"on_conflict": on_conflict})
        r.raise_for_status()

def db_patch(table: str, params: dict, data: dict):
    with httpx.Client() as c:
        r = c.patch(_url(table), headers=HEADERS, params=params, json=data)
        r.raise_for_status()


# ══════════════════════════════════════════════════════════════════════════════
# STUDY HOURS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_weekly_leaderboard(week: int, year: int) -> list:
    rows = db_get("study_hours", {
        "week": f"eq.{week}",
        "year": f"eq.{year}",
        "select": "user_id,seconds"
    })
    totals = {}
    for r in rows:
        uid = r["user_id"]
        totals[uid] = totals.get(uid, 0) + r["seconds"]

    print(f"📊 study_hours rows: {len(rows)}, unique users: {len(totals)}")
    for uid, secs in list(totals.items())[:3]:
        print(f"  study_hours user {uid}: {secs}s")

    now = datetime.now(IST)
    if week == now.isocalendar()[1] and year == now.year:
        lion_rows = db_get("lion_import", {"select": "user_id,weekly_seconds"})
        print(f"📊 lion_import rows: {len(lion_rows)}")
        for r in lion_rows:
            if r.get("weekly_seconds"):
                uid = r["user_id"]
                before = totals.get(uid, 0)
                totals[uid] = before + r["weekly_seconds"]
                print(f"  lion user {uid}: +{r['weekly_seconds']}s (was {before}s, now {totals[uid]}s)")

    print(f"📊 Final top 3:")
    sorted_all = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    for uid, secs in sorted_all[:3]:
        print(f"  {uid}: {secs}s = {secs//3600}h {(secs%3600)//60}m")

    return sorted_all[:10]


def get_monthly_leaderboard(month: int, year: int) -> list:
    rows = db_get("study_hours", {
        "month": f"eq.{month}",
        "year":  f"eq.{year}",
        "select": "user_id,seconds"
    })
    totals = {}
    for r in rows:
        uid = r["user_id"]
        totals[uid] = totals.get(uid, 0) + r["seconds"]

    now = datetime.now(IST)
    if month == now.month and year == now.year:
        lion_rows = db_get("lion_import", {"select": "user_id,monthly_seconds"})
        for r in lion_rows:
            if r.get("monthly_seconds"):
                uid = r["user_id"]
                totals[uid] = totals.get(uid, 0) + r["monthly_seconds"]

    return sorted(totals.items(), key=lambda x: x[1], reverse=True)[:10]


def upsert_study_seconds(user_id: str, seconds: int):
    """Insert a new row each session — query sums them all."""
    now = datetime.now(IST)
    iso = now.isocalendar()
    db_insert("study_hours", {
        "user_id":  user_id,
        "guild_id": str(GUILD_ID),
        "date":     now.date().isoformat(),
        "seconds":  seconds,
        "week":     iso[1],
        "month":    now.month,
        "year":     now.year,
    })


# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE SESSION TRACKING (in-memory)
# ══════════════════════════════════════════════════════════════════════════════

active_sessions: dict[int, datetime] = {}

def session_join(member_id: int):
    active_sessions[member_id] = datetime.now(IST)

def session_leave(member_id: int):
    if member_id not in active_sessions:
        return
    joined_at = active_sessions.pop(member_id)
    seconds = int((datetime.now(IST) - joined_at).total_seconds())
    if seconds > 0:
        upsert_study_seconds(str(member_id), seconds)


# ══════════════════════════════════════════════════════════════════════════════
# LEADERBOARD EMBED BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def seconds_to_hm(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h >= 10: return f"{h}h"
    elif h:     return f"{h}h {m}m"
    else:       return f"{m}m"


async def build_leaderboard_embed(bot, entries: list, title: str, period: str, kind: str = "weekly") -> discord.Embed:
    e = discord.Embed(title=title, color=0x5865F2, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=f"Period: {period}")

    if not entries:
        e.description = "No study data yet for this period!"
        return e

    if kind == "weekly":
        congrats = "Amazing work this week! Congrats to our top studiers 🎉"
    else:
        congrats = "A grand month of studying! Bow down to our champions 👑"

    users = []
    for uid, secs in entries:
        try:
            user = await bot.fetch_user(int(uid))
            mention = user.mention
            name = user.display_name
        except:
            mention = f"<@{uid}>"
            name = f"User"
        users.append((mention, name, secs))

    lines = [f"*{congrats}*\n"]

    # Podium top 3
    podium_labels = ["👑 **1st Place**", "🥈 **2nd Place**", "🥉 **3rd Place**"]
    for i in range(min(3, len(users))):
        mention, name, secs = users[i]
        lines.append(f"{podium_labels[i]} — {mention} `{seconds_to_hm(secs)}`")

    # Rest 4-10
    if len(users) > 3:
        lines.append("\n**─── Rest of the Leaderboard ───**")
        for i in range(3, len(users)):
            mention, name, secs = users[i]
            lines.append(f"`{i+1}.` {mention} — `{seconds_to_hm(secs)}`")

    e.description = "\n".join(lines)
    return e


# ══════════════════════════════════════════════════════════════════════════════
# AUTO POST TASKS
# ══════════════════════════════════════════════════════════════════════════════

bot_ref = None

@tasks.loop(minutes=1)
async def leaderboard_scheduler():
    now = datetime.now(IST)

    # Weekly — every Sunday at 23:59
    if now.weekday() == 6 and now.hour == 23 and now.minute == 59:
        iso = now.isocalendar()
        entries = get_weekly_leaderboard(iso[1], iso[0])
        period = f"Week {iso[1]}, {iso[0]}"
        embed = await build_leaderboard_embed(
            bot_ref, entries, "🏆 Weekly Study Leaderboard", period, kind="weekly")
        ch = bot_ref.get_channel(LEADERBOARD_CHANNEL_ID)
        if ch:
            await ch.send(embed=embed)
            print(f"✅ Weekly leaderboard posted for week {iso[1]}")

    # Monthly — last day of month at 23:59
    tomorrow = now + timedelta(days=1)
    if tomorrow.month != now.month and now.hour == 23 and now.minute == 59:
        entries = get_monthly_leaderboard(now.month, now.year)
        month_name = now.strftime("%B %Y")
        embed = await build_leaderboard_embed(
            bot_ref, entries, "📅 Monthly Study Leaderboard", month_name, kind="monthly")
        ch = bot_ref.get_channel(LEADERBOARD_CHANNEL_ID)
        if ch:
            await ch.send(embed=embed)
            print(f"✅ Monthly leaderboard posted for {month_name}")

@leaderboard_scheduler.before_loop
async def before_scheduler():
    await bot_ref.wait_until_ready()
    import asyncio
    now = datetime.now(IST)
    await asyncio.sleep(60 - now.second)


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

def setup_commands(bot):

    @bot.tree.command(name="importcsv", description="Import Lion bot CSV data (admin only). Attach the CSV file.")
    @app_commands.checks.has_permissions(administrator=True)
    async def import_csv(interaction: discord.Interaction, type: str, file: discord.Attachment):
        if type not in ("monthly", "weekly"):
            return await interaction.response.send_message("❌ Type must be `monthly` or `weekly`", ephemeral=True)
        if not file.filename.endswith(".csv"):
            return await interaction.response.send_message("❌ Please attach a `.csv` file.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            content = await file.read()
            text = content.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            count = 0
            skipped = 0
            for row in reader:
                # Accept both original Lion bot columns and renamed columns
                uid = (row.get("userid") or row.get("user_id") or "").strip().strip("'")
                raw = (row.get("total_time") or row.get("weekly_seconds") or row.get("monthly_seconds") or "").strip()
                if not uid or raw in ("None", "none", "") or not raw:
                    skipped += 1
                    continue
                try:
                    seconds = int(float(raw))
                except ValueError:
                    skipped += 1
                    continue
                if seconds <= 0:
                    skipped += 1
                    continue
                field = "weekly_seconds" if type == "weekly" else "monthly_seconds"
                h = {**HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"}
                with httpx.Client() as c:
                    c.post(_url("lion_import"), headers=h, json={
                        "user_id":    uid,
                        "guild_id":   str(GUILD_ID),
                        field:        seconds,
                        "total_time": seconds,
                    }, params={"on_conflict": "user_id"})
                    c.patch(_url("lion_import"), headers=HEADERS,
                            params={"user_id": f"eq.{uid}"},
                            json={field: seconds})
                count += 1
            e = discord.Embed(title="✅ CSV Imported!", color=0x57F287)
            e.add_field(name="Type",     value=f"`{type}`",   inline=True)
            e.add_field(name="Imported", value=f"`{count}`",  inline=True)
            e.add_field(name="Skipped",  value=f"`{skipped}`", inline=True)
            e.set_footer(text="Data saved to Supabase • Leaderboard will reflect this data")
            await interaction.followup.send(embed=e, ephemeral=True)
            print(f"✅ CSV import: {count} records, {skipped} skipped")
        except Exception as ex:
            print(f"❌ CSV import error: {ex}")
            await interaction.followup.send(f"❌ Import failed: `{ex}`", ephemeral=True)

    @import_csv.error
    async def import_csv_error(interaction, error):
        await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


    @bot.tree.command(name="leaderboard", description="Post the leaderboard publicly (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def post_leaderboard(interaction: discord.Interaction, type: str):
        if type not in ("weekly", "monthly"):
            return await interaction.response.send_message("❌ Type must be `weekly` or `monthly`", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(IST)
        try:
            if type == "weekly":
                iso = now.isocalendar()
                entries = get_weekly_leaderboard(iso[1], iso[0])
                period = f"Week {iso[1]}, {iso[0]}"
                embed = await build_leaderboard_embed(bot_ref, entries, "🏆 Weekly Study Leaderboard", period, kind="weekly")
            else:
                entries = get_monthly_leaderboard(now.month, now.year)
                period = now.strftime("%B %Y")
                embed = await build_leaderboard_embed(bot_ref, entries, "📅 Monthly Study Leaderboard", period, kind="monthly")
            ch = bot_ref.get_channel(LEADERBOARD_CHANNEL_ID)
            if ch:
                await ch.send(embed=embed)
            await interaction.followup.send("✅ Leaderboard posted!", ephemeral=True)
        except Exception as ex:
            print(f"❌ leaderboard error: {ex}")
            await interaction.followup.send(f"❌ Failed: `{ex}`", ephemeral=True)

    @post_leaderboard.error
    async def leaderboard_error(interaction, error):
        await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


    @bot.tree.command(name="adminleaderboard", description="View full weekly + monthly leaderboard — all members (admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_leaderboard(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(IST)
        iso = now.isocalendar()
        try:
            weekly_rows = db_get("study_hours", {"week": f"eq.{iso[1]}", "year": f"eq.{iso[0]}", "select": "user_id,seconds"})
            weekly_totals = {}
            for r in weekly_rows:
                uid = r["user_id"]
                weekly_totals[uid] = weekly_totals.get(uid, 0) + r["seconds"]

            monthly_rows = db_get("study_hours", {"month": f"eq.{now.month}", "year": f"eq.{now.year}", "select": "user_id,seconds"})
            monthly_totals = {}
            for r in monthly_rows:
                uid = r["user_id"]
                monthly_totals[uid] = monthly_totals.get(uid, 0) + r["seconds"]

            lion_rows = db_get("lion_import", {"select": "user_id,weekly_seconds,monthly_seconds"})
            for r in lion_rows:
                uid = r["user_id"]
                if r.get("weekly_seconds"):
                    weekly_totals[uid] = weekly_totals.get(uid, 0) + r["weekly_seconds"]
                if r.get("monthly_seconds"):
                    monthly_totals[uid] = monthly_totals.get(uid, 0) + r["monthly_seconds"]

            weekly_sorted  = sorted(weekly_totals.items(),  key=lambda x: x[1], reverse=True)
            monthly_sorted = sorted(monthly_totals.items(), key=lambda x: x[1], reverse=True)

            e1 = discord.Embed(title=f"📊 Admin Weekly — Week {iso[1]}, {iso[0]}", color=0xFEE75C, timestamp=datetime.now(timezone.utc))
            w_lines = []
            for i, (uid, secs) in enumerate(weekly_sorted):
                try:
                    user = await bot.fetch_user(int(uid))
                    name = user.display_name
                except:
                    name = f"<@{uid}>"
                w_lines.append(f"`{i+1}.` **{name}** — `{seconds_to_hm(secs)}`")
            e1.description = "\n".join(w_lines) if w_lines else "No data yet."
            e1.set_footer(text=f"{len(weekly_sorted)} members tracked")

            e2 = discord.Embed(title=f"📊 Admin Monthly — {now.strftime('%B %Y')}", color=0xEB459E, timestamp=datetime.now(timezone.utc))
            m_lines = []
            for i, (uid, secs) in enumerate(monthly_sorted):
                try:
                    user = await bot.fetch_user(int(uid))
                    name = user.display_name
                except:
                    name = f"<@{uid}>"
                m_lines.append(f"`{i+1}.` **{name}** — `{seconds_to_hm(secs)}`")
            e2.description = "\n".join(m_lines) if m_lines else "No data yet."
            e2.set_footer(text=f"{len(monthly_sorted)} members tracked")

            await interaction.followup.send(embeds=[e1, e2], ephemeral=True)
        except Exception as ex:
            print(f"❌ adminleaderboard error: {ex}")
            await interaction.followup.send(f"❌ Failed: `{ex}`", ephemeral=True)

    @admin_leaderboard.error
    async def admin_leaderboard_error(interaction, error):
        await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# SETUP — called from bot.py
# ══════════════════════════════════════════════════════════════════════════════

async def setup_leaderboard(bot):
    global bot_ref
    bot_ref = bot
    setup_commands(bot)
    leaderboard_scheduler.start()
    print("✅ Leaderboard module loaded")
