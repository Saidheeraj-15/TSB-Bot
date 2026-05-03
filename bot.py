import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("TOKEN") or ""
ALARM_FILE    = "alarms.json"
SCHEDULE_FILE = "schedules.json"
IST           = timezone(timedelta(hours=5, minutes=30))
DAY_NAMES     = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

print(f"🔍  Token loaded: {'YES ✅' if BOT_TOKEN else 'NO ❌'}")
print(f"🔍  Token length: {len(BOT_TOKEN)} chars")
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ══════════════════════════════════════════════════════════════════════════════
# DATA HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_alarms() -> dict:
    if os.path.exists(ALARM_FILE):
        with open(ALARM_FILE) as f:
            return json.load(f)
    return {}

def save_alarms(data: dict) -> None:
    with open(ALARM_FILE, "w") as f:
        json.dump(data, f, indent=2)

def next_alarm_id(items: list) -> int:
    return max((a["id"] for a in items), default=0) + 1

def load_schedules() -> dict:
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE) as f:
            return json.load(f)
    return {}

def save_schedules(data: dict) -> None:
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def time_until(target: datetime) -> str:
    diff = target - datetime.now(IST)
    if diff.total_seconds() <= 0:
        return "very soon"
    days = diff.days
    hrs  = diff.seconds // 3600
    mins = (diff.seconds % 3600) // 60
    parts = []
    if days:  parts.append(f"{days}d")
    if hrs:   parts.append(f"{hrs}h")
    if mins:  parts.append(f"{mins}m")
    return " ".join(parts) or "< 1m"


# ══════════════════════════════════════════════════════════════════════════════
# REPEATING ALARM — MODALS & VIEWS
# ══════════════════════════════════════════════════════════════════════════════

class SetAlarmModal(discord.ui.Modal, title="Set Your Alarm"):
    time_input = discord.ui.TextInput(
        label="Time (IST) — 24hr format e.g. 08:00",
        placeholder="HH:MM",
        max_length=5,
        required=True,
    )
    message_input = discord.ui.TextInput(
        label="Reminder Message",
        placeholder="e.g. Solve today's LeetCode POTD!",
        max_length=200,
        required=True,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, days: list):
        super().__init__()
        self.days = days

    async def on_submit(self, interaction: discord.Interaction):
        try:
            h, m = map(int, self.time_input.value.strip().split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Invalid time. Use HH:MM e.g. `08:00`", ephemeral=True)
            return

        alarms = load_alarms()
        uid = str(interaction.user.id)
        if uid not in alarms:
            alarms[uid] = []

        alarm_id = next_alarm_id(alarms[uid])
        days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"] if "daily" in self.days else self.days
        alarms[uid].append({"id": alarm_id, "hour": h, "minute": m, "days": days,
                             "message": self.message_input.value.strip(), "active": True})
        save_alarms(alarms)

        days_label = "Every Day" if len(days) == 7 else ", ".join(days)
        embed = discord.Embed(title="✅ Alarm Created!", color=0x57F287)
        embed.add_field(name="ID",      value=f"`#{alarm_id}`",         inline=True)
        embed.add_field(name="Time",    value=f"`{h:02d}:{m:02d} IST`", inline=True)
        embed.add_field(name="Days",    value=f"`{days_label}`",         inline=True)
        embed.add_field(name="Message", value=self.message_input.value.strip(), inline=False)
        embed.set_footer(text="I'll DM you at the set time on selected day(s).")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class DayPickerSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Every Day",  value="daily"),
            discord.SelectOption(label="Monday",     value="Mon"),
            discord.SelectOption(label="Tuesday",    value="Tue"),
            discord.SelectOption(label="Wednesday",  value="Wed"),
            discord.SelectOption(label="Thursday",   value="Thu"),
            discord.SelectOption(label="Friday",     value="Fri"),
            discord.SelectOption(label="Saturday",   value="Sat"),
            discord.SelectOption(label="Sunday",     value="Sun"),
        ]
        super().__init__(placeholder="Select day(s) for your alarm...",
                         min_values=1, max_values=8, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SetAlarmModal(self.values))


class DayPickerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(DayPickerSelect())


class AlarmDeleteSelect(discord.ui.Select):
    def __init__(self, user_alarms: list):
        options = []
        for a in user_alarms:
            days_label = "Daily" if len(a["days"]) == 7 else "/".join(a["days"])
            options.append(discord.SelectOption(
                label=f"#{a['id']} — {a['hour']:02d}:{a['minute']:02d} IST ({days_label})",
                description=a["message"][:50], value=str(a["id"]), emoji="🗑️",
            ))
        super().__init__(placeholder="Select alarm(s) to delete...",
                         min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        alarms = load_alarms()
        uid = str(interaction.user.id)
        ids_to_delete = {int(v) for v in self.values}
        before = len(alarms.get(uid, []))
        alarms[uid] = [a for a in alarms.get(uid, []) if a["id"] not in ids_to_delete]
        save_alarms(alarms)
        await interaction.response.send_message(
            f"🗑️ Deleted **{before - len(alarms[uid])}** alarm(s).", ephemeral=True)
        self.view.stop()


class AlarmDeleteView(discord.ui.View):
    def __init__(self, user_alarms: list):
        super().__init__(timeout=60)
        self.add_item(AlarmDeleteSelect(user_alarms))


class AlarmPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Set Alarm", style=discord.ButtonStyle.primary,
                       emoji="⏰", custom_id="panel:set_alarm")
    async def set_alarm(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="⏰ Set an Alarm",
                              description="Pick day(s) then fill in the time and message.",
                              color=0x5865F2)
        await interaction.response.send_message(embed=embed, view=DayPickerView(), ephemeral=True)

    @discord.ui.button(label="My Alarms", style=discord.ButtonStyle.secondary,
                       emoji="📋", custom_id="panel:my_alarms")
    async def my_alarms(self, interaction: discord.Interaction, button: discord.ui.Button):
        alarms = load_alarms()
        uid = str(interaction.user.id)
        user_alarms = alarms.get(uid, [])
        if not user_alarms:
            await interaction.response.send_message(
                "📭 You have no active alarms. Click **Set Alarm** to create one!", ephemeral=True)
            return
        embed = discord.Embed(title="📋 Your Alarms", color=0x5865F2)
        for a in user_alarms:
            days_label = "Every Day" if len(a["days"]) == 7 else ", ".join(a["days"])
            embed.add_field(name=f"#{a['id']}  •  {a['hour']:02d}:{a['minute']:02d} IST  •  {days_label}",
                            value=f"💬 {a['message']}", inline=False)
        embed.set_footer(text=f"{len(user_alarms)} alarm(s) active")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Delete Alarms", style=discord.ButtonStyle.danger,
                       emoji="🗑️", custom_id="panel:delete_alarms")
    async def delete_alarms(self, interaction: discord.Interaction, button: discord.ui.Button):
        alarms = load_alarms()
        uid = str(interaction.user.id)
        user_alarms = alarms.get(uid, [])
        if not user_alarms:
            await interaction.response.send_message("📭 You have no alarms to delete.", ephemeral=True)
            return
        embed = discord.Embed(title="🗑️ Delete Alarms",
                              description="Select alarm(s) to remove:", color=0xED4245)
        await interaction.response.send_message(embed=embed, view=AlarmDeleteView(user_alarms), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# ONE-TIME SCHEDULE — MODALS & VIEWS
# ══════════════════════════════════════════════════════════════════════════════

class ScheduleModal(discord.ui.Modal, title="Schedule a One-Time Reminder"):
    date_input = discord.ui.TextInput(
        label="Date (IST) — DD/MM/YYYY",
        placeholder="e.g. 25/12/2025",
        max_length=10,
        required=True,
    )
    time_input = discord.ui.TextInput(
        label="Time (IST) — 24hr HH:MM",
        placeholder="e.g. 08:00",
        max_length=5,
        required=True,
    )
    message_input = discord.ui.TextInput(
        label="Reminder Message",
        placeholder="e.g. Submit the project before midnight!",
        max_length=200,
        required=True,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            day, month, year = map(int, self.date_input.value.strip().split("/"))
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date. Use DD/MM/YYYY e.g. `25/12/2025`", ephemeral=True)
            return
        try:
            h, m = map(int, self.time_input.value.strip().split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid time. Use HH:MM e.g. `08:00`", ephemeral=True)
            return
        try:
            target = datetime(year, month, day, h, m, tzinfo=IST)
        except ValueError:
            await interaction.response.send_message("❌ That date doesn't exist.", ephemeral=True)
            return
        if target <= datetime.now(IST):
            await interaction.response.send_message(
                "❌ That date/time is already in the past. Pick a future time.", ephemeral=True)
            return

        schedules = load_schedules()
        uid = str(interaction.user.id)
        if uid not in schedules:
            schedules[uid] = []
        sid = next_alarm_id(schedules[uid])
        schedules[uid].append({"id": sid, "timestamp": target.timestamp(),
                                "message": self.message_input.value.strip(), "fired": False})
        save_schedules(schedules)

        embed = discord.Embed(title="✅ Reminder Scheduled!", color=0x57F287)
        embed.add_field(name="🆔 ID",       value=f"`#{sid}`",                                         inline=True)
        embed.add_field(name="📅 Date",     value=f"`{day:02d}/{month:02d}/{year} {h:02d}:{m:02d} IST`", inline=True)
        embed.add_field(name="⏳ Fires In", value=f"`{time_until(target)}`",                            inline=True)
        embed.add_field(name="💬 Message",  value=self.message_input.value.strip(),                     inline=False)
        embed.set_footer(text="I'll DM you once at that exact time, then auto-remove this reminder.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ScheduleDeleteSelect(discord.ui.Select):
    def __init__(self, uid: str, user_schedules: list):
        self.uid = uid
        options = []
        for s in user_schedules:
            dt = datetime.fromtimestamp(s["timestamp"], tz=IST)
            options.append(discord.SelectOption(
                label=f"#{s['id']} — {dt.strftime('%d/%m/%Y %H:%M IST')}",
                description=s["message"][:50], value=str(s["id"]), emoji="🗑️",
            ))
        super().__init__(placeholder="Select reminder(s) to delete...",
                         min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        schedules = load_schedules()
        ids_to_delete = {int(v) for v in self.values}
        before = len(schedules.get(self.uid, []))
        schedules[self.uid] = [s for s in schedules.get(self.uid, []) if s["id"] not in ids_to_delete]
        save_schedules(schedules)
        await interaction.response.send_message(
            f"🗑️ Deleted **{before - len(schedules[self.uid])}** reminder(s).", ephemeral=True)
        self.view.stop()


class ScheduleDeleteView(discord.ui.View):
    def __init__(self, uid: str, user_schedules: list):
        super().__init__(timeout=60)
        self.add_item(ScheduleDeleteSelect(uid, user_schedules))


class SchedulePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Schedule Reminder", style=discord.ButtonStyle.primary,
                       emoji="📅", custom_id="schedule:set")
    async def set_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScheduleModal())

    @discord.ui.button(label="My Reminders", style=discord.ButtonStyle.secondary,
                       emoji="📋", custom_id="schedule:list")
    async def list_schedules(self, interaction: discord.Interaction, button: discord.ui.Button):
        schedules = load_schedules()
        uid = str(interaction.user.id)
        user_schedules = [s for s in schedules.get(uid, []) if not s.get("fired")]
        if not user_schedules:
            await interaction.response.send_message(
                "📭 No upcoming reminders. Click **Schedule Reminder** to add one!", ephemeral=True)
            return
        embed = discord.Embed(title="📋 Your Scheduled Reminders", color=0x5865F2)
        for s in user_schedules:
            dt = datetime.fromtimestamp(s["timestamp"], tz=IST)
            embed.add_field(
                name=f"#{s['id']}  •  {dt.strftime('%d/%m/%Y %H:%M IST')}  •  ⏳ {time_until(dt)}",
                value=f"💬 {s['message']}", inline=False)
        embed.set_footer(text=f"{len(user_schedules)} reminder(s) pending")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Delete Reminder", style=discord.ButtonStyle.danger,
                       emoji="🗑️", custom_id="schedule:delete")
    async def delete_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        schedules = load_schedules()
        uid = str(interaction.user.id)
        user_schedules = [s for s in schedules.get(uid, []) if not s.get("fired")]
        if not user_schedules:
            await interaction.response.send_message("📭 No reminders to delete.", ephemeral=True)
            return
        embed = discord.Embed(title="🗑️ Delete Reminders",
                              description="Select reminder(s) to remove:", color=0xED4245)
        await interaction.response.send_message(
            embed=embed, view=ScheduleDeleteView(uid, user_schedules), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL
# ══════════════════════════════════════════════════════════════════════════════

class AdminDeleteSelect(discord.ui.Select):
    def __init__(self, entries: list):
        self.entries = {f"{uid}:{a['id']}": (uid, a) for uid, a in entries}
        options = []
        for key, (uid, a) in self.entries.items():
            days_label = "Daily" if len(a["days"]) == 7 else "/".join(a["days"])
            options.append(discord.SelectOption(
                label=f"[{a['_username']}] #{a['id']} — {a['hour']:02d}:{a['minute']:02d} ({days_label})",
                description=a["message"][:50], value=key, emoji="🗑️",
            ))
        super().__init__(placeholder="Select alarm(s) to delete...",
                         min_values=1, max_values=min(len(options), 25), options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        alarms = load_alarms()
        deleted = 0
        for key in self.values:
            uid, alarm = self.entries[key]
            before = len(alarms.get(uid, []))
            alarms[uid] = [a for a in alarms.get(uid, []) if a["id"] != alarm["id"]]
            deleted += before - len(alarms[uid])
        save_alarms(alarms)
        await interaction.response.send_message(f"🗑️ Deleted **{deleted}** alarm(s).", ephemeral=True)
        self.view.stop()


class AdminDeleteView(discord.ui.View):
    def __init__(self, entries: list):
        super().__init__(timeout=60)
        self.add_item(AdminDeleteSelect(entries))


# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="panel", description="Post the repeating alarm panel (admin only)")
@app_commands.checks.has_permissions(manage_channels=True)
async def spawn_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⏰ Alarm Panel",
        description=(
            "Set personalized DM reminders delivered straight to your inbox.\n\n"
            "🔵 **Set Alarm** — choose days, time & message\n"
            "⚪ **My Alarms** — view all your active alarms\n"
            "🔴 **Delete Alarms** — remove alarms you no longer need"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="All responses are private • Times in IST (UTC+5:30)")
    await interaction.channel.send(embed=embed, view=AlarmPanelView())
    await interaction.response.send_message("✅ Panel posted!", ephemeral=True)

@spawn_panel.error
async def panel_error(interaction: discord.Interaction, error):
    await interaction.response.send_message("❌ You need **Manage Channels** permission.", ephemeral=True)


@bot.tree.command(name="schedule", description="Post the one-time reminder panel (admin only)")
@app_commands.checks.has_permissions(manage_channels=True)
async def spawn_schedule_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📅 One-Time Reminder Panel",
        description=(
            "Schedule a reminder for a specific date and time.\n"
            "You'll receive a DM once at that exact moment — then it auto-removes.\n\n"
            "📅 **Schedule Reminder** — pick a date, time & message\n"
            "📋 **My Reminders** — view all upcoming reminders\n"
            "🔴 **Delete Reminder** — cancel a scheduled reminder"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="All responses are private • Times in IST (UTC+5:30) • Fires once then removed")
    await interaction.channel.send(embed=embed, view=SchedulePanelView())
    await interaction.response.send_message("✅ Schedule panel posted!", ephemeral=True)

@spawn_schedule_panel.error
async def schedule_panel_error(interaction: discord.Interaction, error):
    await interaction.response.send_message("❌ You need **Manage Channels** permission.", ephemeral=True)


@bot.tree.command(name="adminpanel", description="View and manage all users' alarms (admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def admin_panel(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    alarms = load_alarms()
    if not alarms:
        await interaction.followup.send("📭 No alarms set by any user yet.", ephemeral=True)
        return

    embed = discord.Embed(title="🛡️ Admin — All Alarms", color=0xED4245,
                          timestamp=datetime.now(timezone.utc))
    all_entries = []
    total = 0

    for uid, user_alarms in alarms.items():
        if not user_alarms:
            continue
        try:
            user = await bot.fetch_user(int(uid))
            username = f"{user.display_name} (@{user.name})"
            uname = user.name
        except Exception:
            username = f"Unknown ({uid})"
            uname = uid

        lines = []
        for a in user_alarms:
            days_label = "Every Day" if len(a["days"]) == 7 else ", ".join(a["days"])
            lines.append(f"`#{a['id']}` — **{a['hour']:02d}:{a['minute']:02d} IST** — {days_label}\n💬 {a['message']}")
            a["_username"] = uname
            all_entries.append((uid, a))
            total += 1

        embed.add_field(name=f"👤 {username}", value="\n\n".join(lines), inline=False)

    embed.set_footer(text=f"Total: {total} alarm(s) across {len(alarms)} user(s)")
    view = AdminDeleteView(all_entries) if all_entries else None
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@admin_panel.error
async def admin_panel_error(interaction: discord.Interaction, error):
    await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND CHECKER
# ══════════════════════════════════════════════════════════════════════════════

@tasks.loop(minutes=1)
async def check_alarms():
    now_ist = datetime.now(IST)
    now_day = DAY_NAMES[now_ist.weekday()]
    now_h   = now_ist.hour
    now_m   = now_ist.minute

    # ── Repeating alarms ──────────────────────────────────────────────────────
    alarms = load_alarms()
    for uid, user_alarms in alarms.items():
        for alarm in user_alarms:
            if not alarm.get("active", True):
                continue
            if alarm["hour"] == now_h and alarm["minute"] == now_m and now_day in alarm["days"]:
                try:
                    user = await bot.fetch_user(int(uid))
                    embed = discord.Embed(title="⏰ Alarm!", description=alarm["message"],
                                         color=0xFEE75C, timestamp=datetime.now(timezone.utc))
                    embed.set_footer(text=f"Alarm #{alarm['id']}  •  {now_h:02d}:{now_m:02d} IST")
                    await user.send(embed=embed)
                    print(f"✅ Alarm #{alarm['id']} sent to {uid}")
                except discord.Forbidden:
                    print(f"⚠️ Cannot DM {uid} — DMs closed")
                except Exception as e:
                    print(f"❌ Alarm error for {uid}: {e}")

    # ── One-time schedules ────────────────────────────────────────────────────
    schedules = load_schedules()
    changed = False
    for uid, user_schedules in schedules.items():
        for s in user_schedules:
            if s.get("fired"):
                continue
            target = datetime.fromtimestamp(s["timestamp"], tz=IST)
            # Fire if within the current minute window
            if target.date() == now_ist.date() and target.hour == now_h and target.minute == now_m:
                try:
                    user = await bot.fetch_user(int(uid))
                    embed = discord.Embed(title="📅 Reminder!", description=s["message"],
                                         color=0xEB459E, timestamp=datetime.now(timezone.utc))
                    embed.set_footer(text=f"One-time reminder #{s['id']}  •  {target.strftime('%d/%m/%Y %H:%M IST')}")
                    await user.send(embed=embed)
                    s["fired"] = True
                    changed = True
                    print(f"✅ Schedule #{s['id']} fired for {uid}")
                except discord.Forbidden:
                    print(f"⚠️ Cannot DM {uid} — DMs closed")
                    s["fired"] = True
                    changed = True
                except Exception as e:
                    print(f"❌ Schedule error for {uid}: {e}")

    if changed:
        # Clean up fired schedules
        for uid in schedules:
            schedules[uid] = [s for s in schedules[uid] if not s.get("fired")]
        save_schedules(schedules)


@check_alarms.before_loop
async def before_check():
    await bot.wait_until_ready()
    now = datetime.now(IST)
    await asyncio.sleep(60 - now.second)


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    bot.add_view(AlarmPanelView())
    bot.add_view(SchedulePanelView())
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Sync failed: {e}")
    check_alarms.start()


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN not set!")
        exit(1)
    print("🚀 Starting bot...")
    bot.run(BOT_TOKEN, log_handler=None)
