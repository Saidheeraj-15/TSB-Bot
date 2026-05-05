import discord
from discord.ext import commands, tasks
from discord import app_commands
import os, asyncio, json, io
from datetime import datetime, timezone, timedelta
import httpx

# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("DISCORD_BOT_TOKEN", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
ADMIN_ID     = int(os.environ.get("ADMIN_USER_ID", "0"))

IST       = timezone(timedelta(hours=5, minutes=30))
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

print(f"🔍 Token:    {'✅' if BOT_TOKEN    else '❌'}")
print(f"🔍 Supabase: {'✅' if SUPABASE_URL else '❌'}")
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE — httpx calls to Supabase REST API (no SDK)
# ══════════════════════════════════════════════════════════════════════════════

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

def db_insert(table: str, data: dict):
    with httpx.Client() as c:
        r = c.post(_url(table), headers=HEADERS, json=data)
        r.raise_for_status()

def db_delete(table: str, params: dict):
    with httpx.Client() as c:
        r = c.delete(_url(table), headers=HEADERS, params=params)
        r.raise_for_status()

def db_patch(table: str, params: dict, data: dict):
    with httpx.Client() as c:
        r = c.patch(_url(table), headers=HEADERS, params=params, json=data)
        r.raise_for_status()

# ── Alarm helpers ─────────────────────────────────────────────────────────────
def get_alarms(uid: str) -> list:
    return db_get("alarms", {"user_id": f"eq.{uid}", "select": "*"})

def get_all_alarms() -> list:
    return db_get("alarms", {"select": "*"})

def add_alarm(uid: str, hour: int, minute: int, days: list, message: str):
    db_insert("alarms", {"user_id": uid, "hour": hour, "minute": minute,
                          "days": days, "message": message, "active": True})

def delete_alarm(alarm_id: int):
    db_delete("alarms", {"id": f"eq.{alarm_id}"})

# ── Schedule helpers ──────────────────────────────────────────────────────────
def get_schedules(uid: str) -> list:
    return db_get("schedules", {"user_id": f"eq.{uid}", "fired": "eq.false", "select": "*"})

def get_all_schedules() -> list:
    return db_get("schedules", {"fired": "eq.false", "select": "*"})

def add_schedule(uid: str, timestamp: float, message: str):
    db_insert("schedules", {"user_id": uid, "timestamp": timestamp,
                             "message": message, "fired": False})

def delete_schedule(schedule_id: int):
    db_delete("schedules", {"id": f"eq.{schedule_id}"})

def mark_fired(schedule_id: int):
    db_patch("schedules", {"id": f"eq.{schedule_id}"}, {"fired": True})


# ══════════════════════════════════════════════════════════════════════════════
# UTILS
# ══════════════════════════════════════════════════════════════════════════════

def time_until(target: datetime) -> str:
    diff = target - datetime.now(IST)
    if diff.total_seconds() <= 0: return "very soon"
    d, h, m = diff.days, diff.seconds // 3600, (diff.seconds % 3600) // 60
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    return " ".join(parts) or "< 1m"

def days_label(days: list) -> str:
    return "Every Day" if len(days) == 7 else ", ".join(days)


# ══════════════════════════════════════════════════════════════════════════════
# REPEATING ALARM — MODALS & VIEWS
# ══════════════════════════════════════════════════════════════════════════════

class SetAlarmModal(discord.ui.Modal, title="Set Your Alarm"):
    time_input = discord.ui.TextInput(
        label="Time (IST) — 24hr e.g. 08:00",
        placeholder="HH:MM", max_length=5)
    message_input = discord.ui.TextInput(
        label="Reminder Message",
        placeholder="e.g. Solve today's LeetCode POTD!",
        max_length=200, style=discord.TextStyle.paragraph)

    def __init__(self, days): super().__init__(); self.days = days

    async def on_submit(self, interaction: discord.Interaction):
        try:
            h, m = map(int, self.time_input.value.strip().split(":"))
            assert 0 <= h <= 23 and 0 <= m <= 59
        except:
            return await interaction.response.send_message(
                "❌ Invalid time. Use HH:MM e.g. `08:00`", ephemeral=True)
        try:
            uid  = str(interaction.user.id)
            days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"] if "daily" in self.days else list(self.days)
            add_alarm(uid, h, m, days, self.message_input.value.strip())
        except Exception as ex:
            print(f"❌ add_alarm error: {ex}")
            return await interaction.response.send_message(
                "❌ Failed to save alarm. Please try again.", ephemeral=True)

        e = discord.Embed(title="✅ Alarm Set", color=0x57F287)
        e.add_field(name="Time",    value=f"`{h:02d}:{m:02d} IST`", inline=True)
        e.add_field(name="Days",    value=f"`{days_label(days)}`",   inline=True)
        e.add_field(name="Message", value=self.message_input.value.strip(), inline=False)
        e.set_footer(text="You'll receive a DM at this time on selected day(s).")
        await interaction.response.send_message(embed=e, ephemeral=True)


class DayPickerSelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=l, value=v) for l, v in [
            ("Every Day","daily"),("Monday","Mon"),("Tuesday","Tue"),
            ("Wednesday","Wed"),("Thursday","Thu"),("Friday","Fri"),
            ("Saturday","Sat"),("Sunday","Sun")]]
        super().__init__(placeholder="Select day(s)...", min_values=1, max_values=8, options=opts)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SetAlarmModal(self.values))

class DayPickerView(discord.ui.View):
    def __init__(self): super().__init__(timeout=120); self.add_item(DayPickerSelect())


class AlarmDeleteSelect(discord.ui.Select):
    def __init__(self, user_alarms):
        opts = [discord.SelectOption(
            label=f"#{a['id']} — {a['hour']:02d}:{a['minute']:02d} IST ({'Daily' if len(a['days'])==7 else '/'.join(a['days'])})",
            description=a["message"][:50], value=str(a["id"])) for a in user_alarms]
        super().__init__(placeholder="Select alarm(s) to delete...",
                         min_values=1, max_values=len(opts), options=opts)

    async def callback(self, interaction: discord.Interaction):
        try:
            for aid in self.values:
                delete_alarm(int(aid))
        except Exception as ex:
            print(f"❌ delete_alarm error: {ex}")
            return await interaction.response.send_message(
                "❌ Failed to delete. Please try again.", ephemeral=True)
        await interaction.response.send_message(
            f"🗑️ Deleted **{len(self.values)}** alarm(s).", ephemeral=True)
        self.view.stop()

class AlarmDeleteView(discord.ui.View):
    def __init__(self, user_alarms):
        super().__init__(timeout=60)
        self.add_item(AlarmDeleteSelect(user_alarms))


class AlarmPanelView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Set Alarm", style=discord.ButtonStyle.primary,
                       emoji="⏰", custom_id="alarm:set")
    async def set_alarm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(title="⏰ New Alarm",
                                description="Choose the day(s) first.", color=0x5865F2),
            view=DayPickerView(), ephemeral=True)

    @discord.ui.button(label="My Alarms", style=discord.ButtonStyle.secondary,
                       emoji="📋", custom_id="alarm:list")
    async def my_alarms(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = str(interaction.user.id)
            alarms = get_alarms(uid)
        except Exception as ex:
            print(f"❌ get_alarms error: {ex}")
            return await interaction.followup.send("❌ Failed to fetch alarms.", ephemeral=True)
        if not alarms:
            return await interaction.followup.send("📭 You have no alarms yet.", ephemeral=True)
        e = discord.Embed(title="📋 Your Alarms", color=0x5865F2)
        for a in alarms:
            e.add_field(
                name=f"#{a['id']}  •  {a['hour']:02d}:{a['minute']:02d} IST  •  {days_label(a['days'])}",
                value=f"💬 {a['message']}", inline=False)
        e.set_footer(text=f"{len(alarms)} active alarm(s)")
        await interaction.followup.send(embed=e, ephemeral=True)

    @discord.ui.button(label="Delete Alarm", style=discord.ButtonStyle.danger,
                       emoji="🗑️", custom_id="alarm:delete")
    async def delete_alarms(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = str(interaction.user.id)
            alarms = get_alarms(uid)
        except Exception as ex:
            print(f"❌ get_alarms error: {ex}")
            return await interaction.followup.send("❌ Failed to fetch alarms.", ephemeral=True)
        if not alarms:
            return await interaction.followup.send("📭 No alarms to delete.", ephemeral=True)
        await interaction.followup.send(
            embed=discord.Embed(title="🗑️ Delete Alarms", color=0xED4245),
            view=AlarmDeleteView(alarms), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# ONE-TIME SCHEDULE — MODALS & VIEWS
# ══════════════════════════════════════════════════════════════════════════════

class ScheduleModal(discord.ui.Modal, title="One-Time Reminder"):
    date_input = discord.ui.TextInput(
        label="Date (IST) — DD/MM/YYYY",
        placeholder="e.g. 25/12/2025", max_length=10)
    time_input = discord.ui.TextInput(
        label="Time (IST) — HH:MM",
        placeholder="e.g. 08:00", max_length=5)
    message_input = discord.ui.TextInput(
        label="Reminder Message",
        placeholder="e.g. Submit the project!",
        max_length=200, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            day, month, year = map(int, self.date_input.value.strip().split("/"))
        except:
            return await interaction.response.send_message(
                "❌ Invalid date. Use DD/MM/YYYY e.g. `25/12/2025`", ephemeral=True)
        try:
            h, m = map(int, self.time_input.value.strip().split(":"))
            assert 0 <= h <= 23 and 0 <= m <= 59
        except:
            return await interaction.response.send_message(
                "❌ Invalid time. Use HH:MM e.g. `08:00`", ephemeral=True)
        try:
            target = datetime(year, month, day, h, m, tzinfo=IST)
        except:
            return await interaction.response.send_message(
                "❌ That date doesn't exist.", ephemeral=True)
        if target <= datetime.now(IST):
            return await interaction.response.send_message(
                "❌ That date/time is already in the past.", ephemeral=True)
        try:
            uid = str(interaction.user.id)
            add_schedule(uid, target.timestamp(), self.message_input.value.strip())
        except Exception as ex:
            print(f"❌ add_schedule error: {ex}")
            return await interaction.response.send_message(
                "❌ Failed to save reminder. Please try again.", ephemeral=True)

        e = discord.Embed(title="✅ Reminder Scheduled", color=0x57F287)
        e.add_field(name="Date",     value=f"`{day:02d}/{month:02d}/{year} {h:02d}:{m:02d} IST`", inline=True)
        e.add_field(name="Fires In", value=f"`{time_until(target)}`",                              inline=True)
        e.add_field(name="Message",  value=self.message_input.value.strip(),                       inline=False)
        e.set_footer(text="Fires once then auto-removes.")
        await interaction.response.send_message(embed=e, ephemeral=True)


class ScheduleDeleteSelect(discord.ui.Select):
    def __init__(self, user_schedules):
        opts = [discord.SelectOption(
            label=f"#{s['id']} — {datetime.fromtimestamp(s['timestamp'],tz=IST).strftime('%d/%m/%Y %H:%M IST')}",
            description=s["message"][:50], value=str(s["id"])) for s in user_schedules]
        super().__init__(placeholder="Select reminder(s) to delete...",
                         min_values=1, max_values=len(opts), options=opts)

    async def callback(self, interaction: discord.Interaction):
        try:
            for sid in self.values:
                delete_schedule(int(sid))
        except Exception as ex:
            print(f"❌ delete_schedule error: {ex}")
            return await interaction.response.send_message(
                "❌ Failed to delete. Please try again.", ephemeral=True)
        await interaction.response.send_message(
            f"🗑️ Deleted **{len(self.values)}** reminder(s).", ephemeral=True)
        self.view.stop()

class ScheduleDeleteView(discord.ui.View):
    def __init__(self, user_schedules):
        super().__init__(timeout=60)
        self.add_item(ScheduleDeleteSelect(user_schedules))


class SchedulePanelView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Schedule Reminder", style=discord.ButtonStyle.primary,
                       emoji="📅", custom_id="schedule:set")
    async def set_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScheduleModal())

    @discord.ui.button(label="My Reminders", style=discord.ButtonStyle.secondary,
                       emoji="📋", custom_id="schedule:list")
    async def list_schedules(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = str(interaction.user.id)
            schedules = get_schedules(uid)
        except Exception as ex:
            print(f"❌ get_schedules error: {ex}")
            return await interaction.followup.send("❌ Failed to fetch reminders.", ephemeral=True)
        if not schedules:
            return await interaction.followup.send(
                "📭 You have no upcoming reminders.", ephemeral=True)
        e = discord.Embed(title="📋 Your Reminders", color=0x5865F2)
        for s in schedules:
            dt = datetime.fromtimestamp(s["timestamp"], tz=IST)
            e.add_field(
                name=f"#{s['id']}  •  {dt.strftime('%d/%m/%Y %H:%M IST')}  •  ⏳ {time_until(dt)}",
                value=f"💬 {s['message']}", inline=False)
        e.set_footer(text=f"{len(schedules)} pending reminder(s)")
        await interaction.followup.send(embed=e, ephemeral=True)

    @discord.ui.button(label="Delete Reminder", style=discord.ButtonStyle.danger,
                       emoji="🗑️", custom_id="schedule:delete")
    async def delete_schedule(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            uid = str(interaction.user.id)
            schedules = get_schedules(uid)
        except Exception as ex:
            print(f"❌ get_schedules error: {ex}")
            return await interaction.followup.send("❌ Failed to fetch reminders.", ephemeral=True)
        if not schedules:
            return await interaction.followup.send("📭 No reminders to delete.", ephemeral=True)
        await interaction.followup.send(
            embed=discord.Embed(title="🗑️ Delete Reminders", color=0xED4245),
            view=ScheduleDeleteView(schedules), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL
# ══════════════════════════════════════════════════════════════════════════════

class AdminAlarmDeleteSelect(discord.ui.Select):
    def __init__(self, alarms):
        opts = [discord.SelectOption(
            label=f"#{a['id']} [{a['_username']}] — {a['hour']:02d}:{a['minute']:02d} IST",
            description=a["message"][:50], value=str(a["id"])) for a in alarms[:25]]
        super().__init__(placeholder="Delete repeating alarm(s)...",
                         min_values=1, max_values=len(opts), options=opts)
    async def callback(self, interaction: discord.Interaction):
        for aid in self.values: delete_alarm(int(aid))
        await interaction.response.send_message(
            f"🗑️ Deleted **{len(self.values)}** alarm(s).", ephemeral=True)
        self.view.stop()

class AdminScheduleDeleteSelect(discord.ui.Select):
    def __init__(self, schedules):
        opts = [discord.SelectOption(
            label=f"#{s['id']} [{s['_username']}] — {datetime.fromtimestamp(s['timestamp'],tz=IST).strftime('%d/%m/%Y %H:%M')}",
            description=s["message"][:50], value=str(s["id"])) for s in schedules[:25]]
        super().__init__(placeholder="Delete one-time reminder(s)...",
                         min_values=1, max_values=len(opts), options=opts)
    async def callback(self, interaction: discord.Interaction):
        for sid in self.values: delete_schedule(int(sid))
        await interaction.response.send_message(
            f"🗑️ Deleted **{len(self.values)}** reminder(s).", ephemeral=True)
        self.view.stop()

class AdminAlarmDeleteView(discord.ui.View):
    def __init__(self, alarms): super().__init__(timeout=60); self.add_item(AdminAlarmDeleteSelect(alarms))

class AdminScheduleDeleteView(discord.ui.View):
    def __init__(self, schedules): super().__init__(timeout=60); self.add_item(AdminScheduleDeleteSelect(schedules))


# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="panel", description="Post the repeating alarm panel (admin only)")
@app_commands.checks.has_permissions(manage_channels=True)
async def spawn_panel(interaction: discord.Interaction):
    e = discord.Embed(title="⏰ Alarm Panel", color=0x5865F2,
                      description="Get DM reminders on your chosen days at a set time.")
    e.set_footer(text="Responses are private • Times in IST")
    await interaction.channel.send(embed=e, view=AlarmPanelView())
    await interaction.response.send_message("✅ Done!", ephemeral=True)

@spawn_panel.error
async def panel_error(interaction, error):
    await interaction.response.send_message("❌ You need **Manage Channels** permission.", ephemeral=True)


@bot.tree.command(name="schedule", description="Post the one-time reminder panel (admin only)")
@app_commands.checks.has_permissions(manage_channels=True)
async def spawn_schedule(interaction: discord.Interaction):
    e = discord.Embed(title="📅 One-Time Reminder Panel", color=0x5865F2,
                      description="Get a DM at a specific date & time. Fires once, then auto-removes.")
    e.set_footer(text="Responses are private • Times in IST")
    await interaction.channel.send(embed=e, view=SchedulePanelView())
    await interaction.response.send_message("✅ Done!", ephemeral=True)

@spawn_schedule.error
async def schedule_error(interaction, error):
    await interaction.response.send_message("❌ You need **Manage Channels** permission.", ephemeral=True)


@bot.tree.command(name="adminpanel", description="View and manage all users' alarms (admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def admin_panel(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        all_alarms    = get_all_alarms()
        all_schedules = get_all_schedules()
    except Exception as ex:
        print(f"❌ adminpanel fetch error: {ex}")
        return await interaction.followup.send("❌ Failed to fetch data from database.", ephemeral=True)

    if not all_alarms and not all_schedules:
        return await interaction.followup.send(
            "📭 No data yet. Users haven't set any alarms or reminders.", ephemeral=True)

    uid_cache = {}
    async def get_username(uid: str) -> tuple:
        if uid in uid_cache: return uid_cache[uid]
        try:
            u = await bot.fetch_user(int(uid))
            uid_cache[uid] = (f"{u.display_name} (@{u.name})", u.name)
        except:
            uid_cache[uid] = (f"Unknown ({uid})", uid)
        return uid_cache[uid]

    # Repeating alarms
    if all_alarms:
        e1 = discord.Embed(title="⏰ All Repeating Alarms", color=0xED4245)
        grouped = {}
        for a in all_alarms:
            grouped.setdefault(a["user_id"], []).append(a)
        annotated = []
        for uid, alarms in grouped.items():
            full_name, uname = await get_username(uid)
            lines = []
            for a in alarms:
                lines.append(f"`#{a['id']}` — **{a['hour']:02d}:{a['minute']:02d} IST** — {days_label(a['days'])}\n💬 {a['message']}")
                a["_username"] = uname
                annotated.append(a)
            e1.add_field(name=f"👤 {full_name}", value="\n\n".join(lines), inline=False)
        e1.set_footer(text=f"{len(all_alarms)} alarm(s) total")
        await interaction.followup.send(embed=e1, view=AdminAlarmDeleteView(annotated), ephemeral=True)

    # One-time schedules
    if all_schedules:
        e2 = discord.Embed(title="📅 All One-Time Reminders", color=0xEB459E)
        grouped2 = {}
        for s in all_schedules:
            grouped2.setdefault(s["user_id"], []).append(s)
        annotated2 = []
        for uid, scheds in grouped2.items():
            full_name, uname = await get_username(uid)
            lines = []
            for s in scheds:
                dt = datetime.fromtimestamp(s["timestamp"], tz=IST)
                lines.append(f"`#{s['id']}` — **{dt.strftime('%d/%m/%Y %H:%M IST')}** — ⏳ {time_until(dt)}\n💬 {s['message']}")
                s["_username"] = uname
                annotated2.append(s)
            e2.add_field(name=f"👤 {full_name}", value="\n\n".join(lines), inline=False)
        e2.set_footer(text=f"{len(all_schedules)} reminder(s) pending")
        await interaction.followup.send(embed=e2, view=AdminScheduleDeleteView(annotated2), ephemeral=True)

@admin_panel.error
async def admin_panel_error(interaction, error):
    await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# AUTO DAILY BACKUP
# ══════════════════════════════════════════════════════════════════════════════

@tasks.loop(hours=24)
async def daily_backup():
    if not ADMIN_ID: return
    try:
        alarms    = get_all_alarms()
        schedules = get_all_schedules()
        user = await bot.fetch_user(ADMIN_ID)
        now  = datetime.now(IST).strftime("%d/%m/%Y %H:%M IST")
        e = discord.Embed(title="🗄️ Daily Backup", color=0x57F287,
                          description=f"Auto-backup at {now}")
        e.add_field(name="⏰ Repeating Alarms",   value=f"`{len(alarms)}` entries",    inline=True)
        e.add_field(name="📅 One-Time Reminders", value=f"`{len(schedules)}` entries", inline=True)
        await user.send(embed=e, files=[
            discord.File(fp=io.BytesIO(json.dumps(alarms,    indent=2).encode()), filename="alarms.json"),
            discord.File(fp=io.BytesIO(json.dumps(schedules, indent=2).encode()), filename="schedules.json"),
        ])
        print("✅ Daily backup sent.")
    except Exception as ex:
        print(f"❌ Backup failed: {ex}")

@daily_backup.before_loop
async def before_backup():
    await bot.wait_until_ready()
    now = datetime.now(IST)
    next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    await asyncio.sleep((next_midnight - now).total_seconds())


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND ALARM CHECKER
# ══════════════════════════════════════════════════════════════════════════════

@tasks.loop(minutes=1)
async def check_alarms():
    now_ist = datetime.now(IST)
    now_day = DAY_NAMES[now_ist.weekday()]
    now_h, now_m = now_ist.hour, now_ist.minute

    try:
        for a in get_all_alarms():
            if a["hour"] == now_h and a["minute"] == now_m and now_day in a["days"]:
                try:
                    user = await bot.fetch_user(int(a["user_id"]))
                    e = discord.Embed(title="⏰ Alarm!", description=a["message"],
                                      color=0xFEE75C, timestamp=datetime.now(timezone.utc))
                    e.set_footer(text=f"Alarm #{a['id']}  •  {now_h:02d}:{now_m:02d} IST")
                    await user.send(embed=e)
                    print(f"✅ Alarm #{a['id']} → {a['user_id']}")
                except discord.Forbidden: print(f"⚠️ Cannot DM {a['user_id']}")
                except Exception as ex:   print(f"❌ {ex}")
    except Exception as ex:
        print(f"❌ check_alarms error: {ex}")

    try:
        for s in get_all_schedules():
            target = datetime.fromtimestamp(s["timestamp"], tz=IST)
            if target.date() == now_ist.date() and target.hour == now_h and target.minute == now_m:
                try:
                    user = await bot.fetch_user(int(s["user_id"]))
                    e = discord.Embed(title="📅 Reminder!", description=s["message"],
                                      color=0xEB459E, timestamp=datetime.now(timezone.utc))
                    e.set_footer(text=f"Reminder #{s['id']}  •  {target.strftime('%d/%m/%Y %H:%M IST')}")
                    await user.send(embed=e)
                    print(f"✅ Schedule #{s['id']} → {s['user_id']}")
                except discord.Forbidden: print(f"⚠️ Cannot DM {s['user_id']}")
                except Exception as ex:   print(f"❌ {ex}")
                finally: mark_fired(s["id"])
    except Exception as ex:
        print(f"❌ check_schedules error: {ex}")

@check_alarms.before_loop
async def before_check():
    await bot.wait_until_ready()
    await asyncio.sleep(60 - datetime.now(IST).second)


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
    daily_backup.start()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ DISCORD_BOT_TOKEN not set!")
        exit(1)
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ SUPABASE_URL or SUPABASE_KEY not set!")
        exit(1)
    print("🚀 Starting bot...")
    bot.run(BOT_TOKEN, log_handler=None)
