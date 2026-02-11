import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
from dateutil import parser
import datetime
import asyncio
import random
import string
import io
from PIL import Image, ImageDraw, ImageFont

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
        # {message_id: {'task': Task, 'codes': {uid: code}, 'remaining': {uid}}}
        self.active_spams = {} 
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()
        for spam_data in self.active_spams.values():
            spam_data['task'].cancel()

    @app_commands.command(name="recruit", description="作業・タスクの募集チケットを発行します")
    async def recruit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RecruitModal())

    @app_commands.command(name="stop_spam", description="[鬼畜モード用] リマインダーを停止します")
    @app_commands.describe(passphrase="画像に表示されているコードを入力")
    async def stop_spam(self, interaction: discord.Interaction, passphrase: str):
        user_id = interaction.user.id
        
        # ユーザーが参加しており、かつ現在スパム中のイベントを探す
        target_event_id = None
        target_data = None

        for msg_id, data in self.active_spams.items():
            # そのユーザーが解除待ちリスト(remaining)にいるか？
            if user_id in data['remaining']:
                target_event_id = msg_id
                target_data = data
                break
        
        if not target_data:
            await interaction.response.send_message("❌ 現在、あなたを対象とした鬼畜リマインダーは動いていません（または既に解除済みです）。", ephemeral=True)
            return

        # コード照合
        correct_code = target_data['codes'].get(user_id)
        if correct_code and passphrase == correct_code:
            # 正解
            target_data['remaining'].remove(user_id)
            await interaction.response.send_message("✅ 解除成功！Botはあなたへの攻撃を停止しました。（他の遅刻者への攻撃は続きます...）", ephemeral=False)
            
            # 全員解除されたかチェック
            if not target_data['remaining']:
                target_data['task'].cancel()
                del self.active_spams[target_event_id]
                try:
                    channel = interaction.channel
                    if channel:
                        await channel.send("🎉 全員が起床しました。リマインダーを完全停止します。")
                except:
                    pass
        else:
            await interaction.response.send_message("❌ コードが間違っています！画像の文字を正確に入力してください。", ephemeral=True)

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
                try: await member.send(text)
                except: pass

    async def send_many_reminders(self, event):
        _, participants = await db.get_event_data(event['message_id'])
        if not participants: return
        guild = self.bot.get_guild(event['guild_id'])
        channel = guild.get_channel(event['channel_id']) if guild else None
        
        mentions = " ".join([f"<@{uid}>" for uid in participants])
        text = self.create_reminder_text(event, "⏰ **[しつこめ通知] まもなく開始です！**")

        for i in range(3):
            if channel:
                try: await channel.send(f"{mentions}\n{text}")
                except: pass
            for uid in participants:
                member = guild.get_member(uid)
                if member:
                    try: await member.send(text)
                    except: pass
            await asyncio.sleep(60)

    # --- 鬼畜モード関連 ---

    def generate_captcha(self, text):
        """Pillowを使ってコピペ不可能な画像を生成する"""
        width, height = 300, 100
        image = Image.new('RGB', (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)
        
        # ノイズ（点）を描画
        for _ in range(300):
            x = random.randint(0, width)
            y = random.randint(0, height)
            draw.point((x, y), fill=(random.randint(0, 200), random.randint(0, 200), random.randint(0, 200)))
        
        # ノイズ（線）を描画
        for _ in range(10):
            x1 = random.randint(0, width)
            y1 = random.randint(0, height)
            x2 = random.randint(0, width)
            y2 = random.randint(0, height)
            draw.line([(x1, y1), (x2, y2)], fill=(200, 200, 200), width=1)

        # 文字列描画 (デフォルトフォント使用)
        # 読みやすくするために位置を調整
        try:
            # 環境によってはTrueTypeフォントがないため、load_defaultを使う
            # デフォルトフォントは小さいので、少し工夫が必要だが、ここではシンプルに実装
            font = ImageFont.load_default()
            # テキストを中央付近に配置（デフォルトフォントはサイズ変更できないのでそのまま）
            # もしttfが使える環境なら ImageFont.truetype("arial.ttf", 30) などにする
            draw.text((20, 40), f"CODE: {text}", fill=(0, 0, 0))
        except Exception:
            pass
        
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer

    async def start_brutal_spam(self, event):
        """全員が解除するまで止まらないリマインダー"""
        _, participants = await db.get_event_data(event['message_id'])
        if not participants: return

        guild = self.bot.get_guild(event['guild_id'])
        channel = guild.get_channel(event['channel_id']) if guild else None

        # 参加者ごとにユニークなコードを生成
        user_codes = {}
        files_to_send = []
        
        warning_text = (
            f"😈 **鬼畜リマインダー発動** 😈\n"
            f"イベント「{event['title']}」の時間です。\n"
            f"**コピペ対策済みです。** 各自、割り当てられた画像のコードを目視で入力して停止してください。\n"
            f"コマンド: `/stop_spam passphrase:画像に書いてある文字`"
        )

        for uid in participants:
            # ランダムコード生成
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            user_codes[uid] = code
            
            # 画像生成
            img_buffer = self.generate_captcha(code)
            file = discord.File(fp=img_buffer, filename=f"code_{uid}.png")
            files_to_send.append(file)

        # メンション作成
        mentions = " ".join([f"<@{uid}>" for uid in participants])

        # コード画像の送信
        if channel:
            await channel.send(f"{mentions}\n{warning_text}")
            # 複数画像を一括送信（Discordの制限に注意。多すぎる場合は分割が必要だがここでは一括）
            # ファイル数が多いとエラーになる可能性があるため、参加人数が多い場合は注意が必要
            # ここでは参加者一人につき1ファイル送信する
            
            # 画像と誰宛かを明記して送信
            for uid, file in zip(participants, files_to_send):
                await channel.send(f"<@{uid}> さんの解除コード:", file=file)

        # スパムタスク管理データの作成
        # remainingセットに全員を入れる
        remaining_users = set(participants)
        
        task = asyncio.create_task(self.spam_loop(channel, participants, remaining_users, guild))
        self.active_spams[event['message_id']] = {
            'task': task, 
            'codes': user_codes, 
            'remaining': remaining_users
        }

    async def spam_loop(self, channel, all_participants, remaining_users, guild):
        try:
            while True:
                if not remaining_users:
                    break

                # 残っている人だけをメンション
                mentions = [f"<@{uid}>" for uid in remaining_users]
                mentions_str = " ".join(mentions)

                # チャンネル通知
                if channel and mentions:
                    try:
                        await channel.send(f"起きろ！！ {mentions_str} まだ解除できてないぞ！！")
                    except:
                        pass
                
                # DM通知 (残っている人のみ)
                for uid in list(remaining_users): # list化して反復中の変更を防ぐ
                    member = guild.get_member(uid)
                    if member:
                        try:
                            await member.send("⏰ 時間だ！コードを入力して解除しろ！ ⏰")
                        except:
                            pass
                
                await asyncio.sleep(2) # 2秒間隔
        except asyncio.CancelledError:
            pass

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