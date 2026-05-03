import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime, timezone, timedelta


# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("DISCORD_BOT_TOKEN", "")
DATA_FILE  = "alarms.json"
IST        = timezone(timedelta(hours=5, minutes=30))
DAY_NAMES  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── DATA HELPERS ──────────────────────────────────────────────────────────────
def load_alarms() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

def save_alarms(data: dict) -> None:
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def next_alarm_id(user_alarms: list) -> int:
    return max((a["id"] for a in user_alarms), default=0) + 1

# ── MODALS ────────────────────────────────────────────────────────────────────

class SetAlarmModal(discord.ui.Modal, title="⏰  Set Your Alarm"):
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
        raw_time = self.time_input.value.strip()
        try:
            h, m = map(int, raw_time.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "❌  Invalid time. Use HH:MM format (e.g. `08:00`).", ephemeral=True
            )
            return

        alarms = load_alarms()
        uid = str(interaction.user.id)
        if uid not in alarms:
            alarms[uid] = []

        alarm_id = next_alarm_id(alarms[uid])
        days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"] if "daily" in self.days else self.days

        alarms[uid].append({
            "id":      alarm_id,
            "hour":    h,
            "minute":  m,
            "days":    days,
            "message": self.message_input.value.strip(),
            "active":  True,
        })
        save_alarms(alarms)

        days_label = "Every Day" if len(days) == 7 else ", ".join(days)
        embed = discord.Embed(title="✅  Alarm Created!", color=0x57F287)
        embed.add_field(name="🆔  ID",      value=f"`#{alarm_id}`",         inline=True)
        embed.add_field(name="🕐  Time",    value=f"`{h:02d}:{m:02d} IST`", inline=True)
        embed.add_field(name="📅  Days",    value=f"`{days_label}`",         inline=True)
        embed.add_field(name="💬  Message", value=self.message_input.value.strip(), inline=False)
        embed.set_footer(text="I'll DM you at the set time on selected day(s).")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── DAY PICKER VIEW ───────────────────────────────────────────────────────────

class DayPickerSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Every Day",  value="daily", emoji="🔁"),
            discord.SelectOption(label="Monday",     value="Mon",   emoji="1️⃣"),
            discord.SelectOption(label="Tuesday",    value="Tue",   emoji="2️⃣"),
            discord.SelectOption(label="Wednesday",  value="Wed",   emoji="3️⃣"),
            discord.SelectOption(label="Thursday",   value="Thu",   emoji="4️⃣"),
            discord.SelectOption(label="Friday",     value="Fri",   emoji="5️⃣"),
            discord.SelectOption(label="Saturday",   value="Sat",   emoji="6️⃣"),
            discord.SelectOption(label="Sunday",     value="Sun",   emoji="7️⃣"),
        ]
        super().__init__(
            placeholder="📅  Select day(s) for your alarm…",
            min_values=1,
            max_values=8,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_days = self.values
        await interaction.response.send_modal(SetAlarmModal(self.values))


class DayPickerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.selected_days = []
        self.add_item(DayPickerSelect())


# ── DELETE VIEW ───────────────────────────────────────────────────────────────

class DeleteSelect(discord.ui.Select):
    def __init__(self, user_alarms: list):
        options = []
        for a in user_alarms:
            days_label = "Daily" if len(a["days"]) == 7 else "/".join(a["days"])
            options.append(discord.SelectOption(
                label=f"#{a['id']}  {a['hour']:02d}:{a['minute']:02d} IST  ({days_label})",
                description=a["message"][:50],
                value=str(a["id"]),
                emoji="🗑️",
            ))
        super().__init__(
            placeholder="Select alarm(s) to delete…",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        alarms = load_alarms()
        uid    = str(interaction.user.id)
        ids_to_delete = {int(v) for v in self.values}
        before = len(alarms.get(uid, []))
        alarms[uid] = [a for a in alarms.get(uid, []) if a["id"] not in ids_to_delete]
        save_alarms(alarms)
        deleted = before - len(alarms[uid])
        await interaction.response.send_message(
            f"🗑️  Deleted **{deleted}** alarm(s) successfully.", ephemeral=True
        )
        self.view.stop()


class DeleteView(discord.ui.View):
    def __init__(self, user_alarms: list):
        super().__init__(timeout=60)
        self.add_item(DeleteSelect(user_alarms))


# ── MAIN PANEL VIEW ───────────────────────────────────────────────────────────

class AlarmPanelView(discord.ui.View):
    """Persistent 3-button panel — lives in a channel forever."""

    def __init__(self):
        super().__init__(timeout=None)   # timeout=None = persistent across restarts

    @discord.ui.button(
        label="Set Alarm",
        style=discord.ButtonStyle.primary,
        emoji="⏰",
        custom_id="panel:set_alarm",
    )
    async def set_alarm(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⏰  Set an Alarm",
            description="Pick the day(s) you want to be reminded, then fill in the time and message.",
            color=0x5865F2,
        )
        embed.set_footer(text="Select days below — the form will open automatically.")
        await interaction.response.send_message(
            embed=embed, view=DayPickerView(), ephemeral=True
        )

    @discord.ui.button(
        label="My Alarms",
        style=discord.ButtonStyle.secondary,
        emoji="📋",
        custom_id="panel:my_alarms",
    )
    async def my_alarms(self, interaction: discord.Interaction, button: discord.ui.Button):
        alarms = load_alarms()
        uid    = str(interaction.user.id)
        user_alarms = alarms.get(uid, [])

        if not user_alarms:
            await interaction.response.send_message(
                "📭  You have no active alarms. Click **Set Alarm** to create one!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="📋  Your Alarms", color=0x5865F2)
        for a in user_alarms:
            days_label = "Every Day" if len(a["days"]) == 7 else ", ".join(a["days"])
            embed.add_field(
                name=f"#{a['id']}  •  {a['hour']:02d}:{a['minute']:02d} IST  •  {days_label}",
                value=f"💬 {a['message']}",
                inline=False,
            )
        embed.set_footer(text=f"{len(user_alarms)} alarm(s) active  •  Click 'Delete Alarms' to remove")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Delete Alarms",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="panel:delete_alarms",
    )
    async def delete_alarms(self, interaction: discord.Interaction, button: discord.ui.Button):
        alarms = load_alarms()
        uid    = str(interaction.user.id)
        user_alarms = alarms.get(uid, [])

        if not user_alarms:
            await interaction.response.send_message(
                "📭  You have no alarms to delete.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🗑️  Delete Alarms",
            description="Select the alarm(s) you want to remove:",
            color=0xED4245,
        )
        await interaction.response.send_message(
            embed=embed, view=DeleteView(user_alarms), ephemeral=True
        )


# ── SLASH COMMAND TO SPAWN THE PANEL ─────────────────────────────────────────

@bot.tree.command(name="panel", description="Post the alarm panel in this channel (admin only)")
@app_commands.checks.has_permissions(manage_channels=True)
async def spawn_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⏰  Alarm Panel",
        description=(
            "Set personalized DM reminders delivered straight to your inbox.\n\n"
            "🔵  **Set Alarm** — choose days, time & message\n"
            "⚪  **My Alarms** — view all your active alarms\n"
            "🔴  **Delete Alarms** — remove alarms you no longer need"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="All responses are private (only you can see them) • Times in IST")
    await interaction.channel.send(embed=embed, view=AlarmPanelView())
    await interaction.response.send_message("✅  Panel posted!", ephemeral=True)


# ── BACKGROUND ALARM CHECKER ──────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def check_alarms():
    now_ist = datetime.now(IST)
    now_day = DAY_NAMES[now_ist.weekday()]
    now_h   = now_ist.hour
    now_m   = now_ist.minute

    alarms = load_alarms()
    for uid, user_alarms in alarms.items():
        for alarm in user_alarms:
            if not alarm.get("active", True):
                continue
            if alarm["hour"] == now_h and alarm["minute"] == now_m and now_day in alarm["days"]:
                try:
                    user = await bot.fetch_user(int(uid))
                    embed = discord.Embed(
                        title="⏰  Alarm!",
                        description=alarm["message"],
                        color=0xFEE75C,
                        timestamp=datetime.now(timezone.utc),
                    )
                    embed.set_footer(text=f"Alarm #{alarm['id']}  •  {now_h:02d}:{now_m:02d} IST")
                    await user.send(embed=embed)
                    print(f"✅  Alarm #{alarm['id']} → user {uid}")
                except discord.Forbidden:
                    print(f"⚠️  Cannot DM user {uid} — DMs are closed")
                except Exception as e:
                    print(f"❌  Error → user {uid}: {e}")


@check_alarms.before_loop
async def before_check():
    await bot.wait_until_ready()
    now  = datetime.now(IST)
    wait = 60 - now.second
    await asyncio.sleep(wait)


# ── STARTUP ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    # Register persistent view so buttons survive restarts
    bot.add_view(AlarmPanelView())
    print(f"✅  Logged in as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"✅  Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌  Sync failed: {e}")
    check_alarms.start()


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌  DISCORD_BOT_TOKEN not set!")
        exit(1)
    bot.run(DISCORD_BOT_TOKEN)
