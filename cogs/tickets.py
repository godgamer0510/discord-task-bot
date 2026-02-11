import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
from dateutil import parser
import datetime
import asyncio
import random
import string

# 日本時間 (JST) 定義
JST = datetime.timezone(datetime.timedelta(hours=9))

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def update_event_message(self, interaction: discord.Interaction, message_id: int):
        data = await db.get_event_data(message_id)
        if not data:
            await interaction.response.send_message("このイベントデータは既に削除されています。", ephemeral=True)
            return

        event_info, participants = data
        current_count = len(participants)
        required = event_info['required_num']
        
        mode_map = {'normal': '通常', 'many': '多め', 'brutal': '🔥鬼畜🔥'}
        mode_str = mode_map.get(event_info.get('reminder_mode', 'normal'), '通常')

        if current_count >= required:
            color = discord.Color.green()
            status_text = "✅ **決行決定 (人員確保済)** - 準備を進めてください"
        else:
            color = discord.Color.orange()
            status_text = f"⚠ **募集中** - あと {required - current_count} 枚必要です"

        embed = discord.Embed(title=f"📋 {event_info['title']}", color=color)
        embed.add_field(name="📅 日時", value=event_info['date_str'], inline=True)
        embed.add_field(name="📍 場所", value=event_info['location'], inline=True)
        embed.add_field(name="🔔 通知モード", value=mode_str, inline=True)
        embed.add_field(name="👥 チケット状況", value=f"目標: {required}枚 / **現在: {current_count}枚**", inline=False)
        embed.add_field(name="ステータス", value=status_text, inline=False)
        
        member_mentions = [f"<@{uid}>" for uid in participants]
        embed.add_field(name="🎫 参加者一覧", value="\n".join(member_mentions) if member_mentions else "なし", inline=False)
        embed.set_footer(text=f"Event ID: {message_id}")

        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="チケットを取る (参加)", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        
        event_info, participants = await db.get_event_data(msg_id)
        if len(participants) >= event_info['required_num']:
            if interaction.user.id not in participants:
                await interaction.response.send_message("定員に達しています！", ephemeral=True)
                return

        success = await db.add_participant(msg_id, interaction.user.id)
        
        if success:
            await self.update_event_message(interaction, msg_id)
            await interaction.response.send_message("チケットを発行しました！", ephemeral=True)

            # DM通知ロジック (決行決定時)
            event_info, new_participants = await db.get_event_data(msg_id)
            if len(new_participants) == event_info['required_num']:
                notify_text = (
                    f"🎉 **決行決定のお知らせ**\n\n"
                    f"案件「**{event_info['title']}**」のメンバーが集まりました！\n"
                    f"日時: {event_info['date_str']}\n"
                    f"場所: {event_info['location']}\n\n"
                    f"作業の準備をお願いします！"
                )
                guild = interaction.guild
                for uid in new_participants:
                    member = guild.get_member(uid)
                    if member:
                        try:
                            await member.send(notify_text)
                        except discord.Forbidden:
                            pass
        else:
            await interaction.response.send_message("既にチケットを持っています。", ephemeral=True)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="ticket:leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        await db.remove_participant(msg_id, interaction.user.id)
        await self.update_event_message(interaction, msg_id)
        await interaction.response.send_message("チケットを返却しました。", ephemeral=True)

    @discord.ui.button(label="管理者削除", style=discord.ButtonStyle.danger, custom_id="ticket:delete")
    async def delete_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_info, _ = await db.get_event_data(interaction.message.id)
        if not event_info:
            await interaction.message.delete()
            return
        if interaction.user.id != event_info['owner_id'] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("削除権限がありません。", ephemeral=True)
            return
        await db.delete_event(interaction.message.id)
        await interaction.message.delete()
        await interaction.response.send_message("募集を削除しました。", ephemeral=True)


class RecruitModal(discord.ui.Modal, title="タスク募集チケットの発行"):
    task_name = discord.ui.TextInput(label="タスク・作業内容", style=discord.TextStyle.short)
    date_str = discord.ui.TextInput(label="日時 (例: 2026/02/15 21:00)", placeholder="YYYY/MM/DD HH:MM の形式推奨")
    location = discord.ui.TextInput(label="場所・マップURL", placeholder="GoogleMap URLなど")
    required_num = discord.ui.TextInput(label="必要人数", placeholder="数字のみ (例: 3)", min_length=1, max_length=2)
    # 5つ目の項目を追加 (Discord Modalの上限は5つ)
    reminder_mode = discord.ui.TextInput(
        label="通知モード (1:通常, 2:多め, 3:鬼畜)", 
        placeholder="1, 2, 3 のいずれかを入力", 
        default="1",
        min_length=1, 
        max_length=1
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            req_num = int(self.required_num.value)
        except ValueError:
            await interaction.response.send_message("人数は半角数字で入力してください。", ephemeral=True)
            return

        # モード判定
        mode_val = self.reminder_mode.value.strip()
        if mode_val == "2":
            mode = "many"
            mode_display = "多め"
        elif mode_val == "3":
            mode = "brutal"
            mode_display = "🔥鬼畜🔥"
        else:
            mode = "normal"
            mode_display = "通常"

        # 日付解析処理
        try:
            dt = parser.parse(self.date_str.value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            timestamp = dt.timestamp()
        except Exception:
            timestamp = None
            warning_msg = "\n⚠ 日時形式を認識できなかったため、リマインダー機能は無効です (募集は作成されます)。"
        else:
            warning_msg = ""

        embed = discord.Embed(title=f"📋 {self.task_name.value}", color=discord.Color.orange())
        embed.add_field(name="📅 日時", value=self.date_str.value, inline=True)
        embed.add_field(name="📍 場所", value=self.location.value, inline=True)
        embed.add_field(name="🔔 通知モード", value=mode_display, inline=True)
        embed.add_field(name="👥 チケット状況", value=f"目標: {req_num}枚 / **現在: 0枚**", inline=False)
        embed.add_field(name="ステータス", value="⚠ **募集中**", inline=False)
        embed.set_footer(text="Initializing...")

        await interaction.response.send_message(embed=embed, view=TicketView())
        msg = await interaction.original_response()
        
        if warning_msg:
             await interaction.followup.send(warning_msg, ephemeral=True)

        await db.create_event(
            message_id=msg.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild_id,
            owner_id=interaction.user.id,
            title=self.task_name.value,
            date_str=self.date_str.value,
            location=self.location.value,
            required_num=req_num,
            start_timestamp=timestamp,
            reminder_mode=mode
        )

class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_spams = {} # {message_id: {'task': Task, 'code': str}}
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()
        # 進行中のスパムタスクを全てキャンセル
        for spam_data in self.active_spams.values():
            spam_data['task'].cancel()

    @app_commands.command(name="recruit", description="作業・タスクの募集チケットを発行します")
    async def recruit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RecruitModal())

    @app_commands.command(name="stop_spam", description="[鬼畜モード用] リマインダーを停止します")
    @app_commands.describe(passphrase="Botが提示した解除コード")
    async def stop_spam(self, interaction: discord.Interaction, passphrase: str):
        # ユーザーが参加している、かつ現在スパム中のイベントを探す
        target_event_id = None
        
        # 本来はDBチェックすべきですが、解除コードが一致すればOKとする簡易実装
        for msg_id, data in self.active_spams.items():
            if data['code'] == passphrase:
                target_event_id = msg_id
                break
        
        if target_event_id:
            self.active_spams[target_event_id]['task'].cancel()
            del self.active_spams[target_event_id]
            await interaction.response.send_message("✅ リマインダーの停止に成功しました。遅れないように！", ephemeral=False)
        else:
            await interaction.response.send_message("❌ 解除コードが間違っているか、既に停止しています。", ephemeral=True)

    # --- 1分ごとの監視ループ ---
    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        try:
            events = await db.get_upcoming_events()
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()

            for event in events:
                minutes_before = await db.get_guild_notify_time(event['guild_id'])
                notify_threshold = minutes_before * 60

                time_until_start = event['start_timestamp'] - now

                if 0 < time_until_start <= notify_threshold:
                    await self.dispatch_reminder(event)
                    await db.mark_notification_sent(event['message_id'])
                
                elif time_until_start <= 0:
                    await db.mark_notification_sent(event['message_id'])

        except Exception as e:
            print(f"Loop Error: {e}")

    async def dispatch_reminder(self, event):
        mode = event.get('reminder_mode', 'normal')
        
        if mode == 'normal':
            await self.send_normal_reminder(event)
        elif mode == 'many':
            # 非同期で実行（ループを止めないため）
            asyncio.create_task(self.send_many_reminders(event))
        elif mode == 'brutal':
            asyncio.create_task(self.start_brutal_spam(event))

    async def send_normal_reminder(self, event):
        _, participants = await db.get_event_data(event['message_id'])
        if not participants: return

        guild = self.bot.get_guild(event['guild_id'])
        if not guild: return

        text = self.create_reminder_text(event, "⏰ **まもなく開始です！**")

        for uid in participants:
            member = guild.get_member(uid)
            if member:
                try:
                    await member.send(text)
                except discord.Forbidden:
                    pass

    async def send_many_reminders(self, event):
        """チャンネルとDMに複数回通知"""
        _, participants = await db.get_event_data(event['message_id'])
        if not participants: return

        guild = self.bot.get_guild(event['guild_id'])
        channel = guild.get_channel(event['channel_id']) if guild else None
        
        mentions = " ".join([f"<@{uid}>" for uid in participants])
        text = self.create_reminder_text(event, "⏰ **[しつこめ通知] まもなく開始です！**")

        # 3回繰り返す
        for i in range(3):
            # チャンネル通知
            if channel:
                try:
                    await channel.send(f"{mentions}\n{text}")
                except:
                    pass
            
            # DM通知
            for uid in participants:
                member = guild.get_member(uid)
                if member:
                    try:
                        await member.send(text)
                    except:
                        pass
            
            await asyncio.sleep(60) # 1分間隔

    async def start_brutal_spam(self, event):
        """解除コード入力まで無限メンション"""
        _, participants = await db.get_event_data(event['message_id'])
        if not participants: return

        guild = self.bot.get_guild(event['guild_id'])
        channel = guild.get_channel(event['channel_id']) if guild else None

        # 解除コード生成 (長めのランダム文字列)
        random_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        passphrase = f"I_WILL_ATTEND_THE_EVENT_IMMEDIATELY_{random_suffix}"
        
        # 警告送信
        warning_msg = (
            f"😈 **鬼畜リマインダー発動** 😈\n\n"
            f"イベント「{event['title']}」の時間です。\n"
            f"通知を止めるには、以下のコマンドを正確に入力してください（コピペ推奨）：\n"
            f"```\n/stop_spam passphrase:{passphrase}\n```"
        )
        
        mentions = " ".join([f"<@{uid}>" for uid in participants])

        if channel:
            await channel.send(f"{mentions}\n{warning_msg}")

        # スパムタスク開始
        task = asyncio.create_task(self.spam_loop(channel, mentions, participants, guild))
        self.active_spams[event['message_id']] = {'task': task, 'code': passphrase}

    async def spam_loop(self, channel, mentions_str, participant_ids, guild):
        try:
            while True:
                # チャンネルでメンション
                if channel:
                    try:
                        await channel.send(f"起きろ！！ {mentions_str} 時間だぞ！！")
                    except:
                        pass
                
                # DMでもメンション
                for uid in participant_ids:
                    member = guild.get_member(uid)
                    if member:
                        try:
                            await member.send("⏰ 時間だ！起きろ！早く来い！ ⏰")
                        except:
                            pass
                
                # Discordのレートリミットを考慮しつつもウザい頻度で (約2秒)
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            if channel:
                await channel.send("✅ リマインダーが停止されました。")

    def create_reminder_text(self, event, header):
        return (
            f"{header}\n\n"
            f"案件: **{event['title']}**\n"
            f"時間: {event['date_str']}\n"
            f"場所: {event['location']}\n\n"
            f"集合をお願いします！"
        )

    @reminder_loop.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TicketsCog(bot))