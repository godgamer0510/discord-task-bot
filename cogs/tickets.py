import discord
from discord import app_commands
from discord.ext import commands
from database import db

class TicketView(discord.ui.View):
    def __init__(self):
        # timeout=None は永続Viewの必須要件
        super().__init__(timeout=None)

    async def update_event_message(self, interaction: discord.Interaction, message_id: int):
        data = await db.get_event_data(message_id)
        if not data:
            await interaction.response.send_message("このイベントデータは既に削除されています。", ephemeral=True)
            return

        event_info, participants = data
        current_count = len(participants)
        required = event_info['required_num']
        
        # ステータス判定ロジック
        if current_count >= required:
            color = discord.Color.green()
            status_text = "✅ **決行決定 (人員確保済)** - 準備を進めてください"
        else:
            color = discord.Color.orange()
            status_text = f"⚠ **募集中** - あと {required - current_count} 枚必要です"

        # Embed再構築
        embed = discord.Embed(title=f"📋 {event_info['title']}", color=color)
        embed.add_field(name="📅 日時", value=event_info['date_str'], inline=True)
        embed.add_field(name="📍 場所", value=event_info['location'], inline=True)
        embed.add_field(name="👥 チケット状況", value=f"目標: {required}枚 / **現在: {current_count}枚**", inline=False)
        embed.add_field(name="ステータス", value=status_text, inline=False)
        
        member_mentions = [f"<@{uid}>" for uid in participants]
        embed.add_field(name="🎫 参加者一覧", value="\n".join(member_mentions) if member_mentions else "なし", inline=False)
        embed.set_footer(text=f"Event ID: {message_id}")

        await interaction.message.edit(embed=embed, view=self)

    # custom_id を固定することで、Bot再起動後もハンドラを紐付けられる
    @discord.ui.button(label="チケットを取る (参加)", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        
        # 既に定員かチェック（オプション: 定員超えを許可するならここは緩める）
        event_info, participants = await db.get_event_data(msg_id)
        if len(participants) >= event_info['required_num']:
            # 自分が参加済みでなければエラー、参加済みならスルー（連打対策）
            if interaction.user.id not in participants:
                await interaction.response.send_message("定員に達しています！", ephemeral=True)
                return

        success = await db.add_participant(msg_id, interaction.user.id)
        if success:
            await self.update_event_message(interaction, msg_id)
            await interaction.response.send_message("チケットを発行しました！", ephemeral=True)
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
        # 権限チェック (作成者のみ、または管理者権限)
        event_info, _ = await db.get_event_data(interaction.message.id)
        if not event_info:
            await interaction.message.delete()
            return

        if interaction.user.id != event_info['owner_id'] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("削除権限がありません（作成者のみ削除可）。", ephemeral=True)
            return

        await db.delete_event(interaction.message.id)
        await interaction.message.delete()
        await interaction.response.send_message("募集を削除しました。", ephemeral=True)


class RecruitModal(discord.ui.Modal, title="タスク募集チケットの発行"):
    task_name = discord.ui.TextInput(label="タスク・作業内容", style=discord.TextStyle.short)
    date_str = discord.ui.TextInput(label="日時", placeholder="例: 10/25 13:00~")
    location = discord.ui.TextInput(label="場所・マップURL", placeholder="GoogleMap URLなど")
    required_num = discord.ui.TextInput(label="必要人数", placeholder="数字のみ (例: 3)", min_length=1, max_length=2)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            req_num = int(self.required_num.value)
        except ValueError:
            await interaction.response.send_message("人数は半角数字で入力してください。", ephemeral=True)
            return

        embed = discord.Embed(title=f"📋 {self.task_name.value}", color=discord.Color.orange())
        embed.add_field(name="📅 日時", value=self.date_str.value, inline=True)
        embed.add_field(name="📍 場所", value=self.location.value, inline=True)
        embed.add_field(name="👥 チケット状況", value=f"目標: {req_num}枚 / **現在: 0枚**", inline=False)
        embed.add_field(name="ステータス", value="⚠ **募集中**", inline=False)
        embed.set_footer(text="Initializing...")

        # 先にメッセージを送信してIDを確定させる
        await interaction.response.send_message(embed=embed, view=TicketView())
        msg = await interaction.original_response()

        # DBに保存
        await db.create_event(
            message_id=msg.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild_id,
            owner_id=interaction.user.id,
            title=self.task_name.value,
            date_str=self.date_str.value,
            location=self.location.value,
            required_num=req_num
        )

class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="recruit", description="作業・タスクの募集チケットを発行します")
    async def recruit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RecruitModal())

async def setup(bot):
    await bot.add_cog(TicketsCog(bot))