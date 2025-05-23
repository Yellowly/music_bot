import discord
import time
from typing import Callable, Awaitable
from cmd_manager import CmdRunner, CmdContext, CmdResult
from .client import MusicBotClient, QueuedSong

import traceback

class MusicBot:
    def __init__(self, bot: CmdRunner, *, 
                 on_play: Callable[[QueuedSong, MusicBotClient], Awaitable[None]] | None = None,
                 on_queue: Callable[[QueuedSong, MusicBotClient], Awaitable[None]] | None = None,
                 on_dc: Callable[[MusicBotClient, str | None], Awaitable[None]] | None = None,
                 show_queue: Callable[[CmdContext, list[QueuedSong], int], Awaitable[None]] | None = None):
        self.clients: dict[int, MusicBotClient] = {}
        
        self._on_play: Callable[[QueuedSong, MusicBotClient], Awaitable[None]] = on_play if on_play else self._default_on_play
        self._on_queue: Callable[[QueuedSong, MusicBotClient], Awaitable[None]] = on_queue if on_queue else self._default_on_queue
        self._custom_on_dc: Callable[[MusicBotClient, str | None], Awaitable[None]] = on_dc if on_dc else self._default_on_dc
        self._show_queue: Callable[[CmdContext, list[QueuedSong], int], Awaitable[None]] = show_queue if show_queue else self._default_show_queue
        
        self._setup_commands(bot)
    
    def __getitem__(self, key: discord.Guild | int) -> MusicBotClient | None:
        return self.clients.get(key) if type(key)==int else self.clients.get(key.id)
        
    async def join(self, ctx: CmdContext) -> CmdResult:
        """Have the bot join a voice channel and create a MusicBotClient to play music with

        Args:
            ctx (CmdContext): Context given to this command

        Returns:
            CmdResult: The MusicBotClient instance that was created when joining. Otherwise, Error
        """
        user: discord.User | discord.Member = ctx.message.author
        
        # First, ensure the user sending the message is in a voice channel
        if user.voice and user.voice.channel != None:
            vc=user.voice.channel
            
            # Get the bot's voice client instance for this server
            guild_id: int = ctx.guild.id
            client: MusicBotClient | None = self.clients.get(guild_id)
            
            # Connect the client if one does not exist for this server
            if client==None:
                client: MusicBotClient = await vc.connect(timeout=60.0, self_deaf=True, cls=MusicBotClient)
                
                # Should remove client from clients list when the bot disconnects
                client.set_on_disconnect(self._on_dc)
                
                # Should print song when it gets queued
                client.set_on_play(self._on_play)
                
                # Bot should send updates regarding what song is playing to the channel it was summoned from
                # This command is used to set that up
                client.set_msg_channel(ctx.message.channel)
                
                # Print errors and send them to the discord channel as well
                async def log_err(client: MusicBotClient, e: Exception):
                    print('```'+''.join(traceback.extract_tb(e.__traceback__).format())+'```')
                    await client.msg_channel.send('```'+''.join(traceback.extract_tb(e.__traceback__).format())+'```')
                    
                client.set_on_err(log_err)
                
                self.clients[ctx.guild.id] = client
            # If a voice client for this server already exists, then -move should be explicitly called to move voice channels
            else: 
                if client.channel==vc:
                    return CmdResult.err("Already in vc")
                await client.move_to(vc)
                    
            return CmdResult.ok(client)
        else:
            return CmdResult.err("You must be in a voice channel!")
    
    async def disconnect(self, ctx: CmdContext) -> CmdResult:
        """Disconnect the bot from its voice channel

        Args:
            ctx (CmdContext): Context given to this command

        Returns:
            CmdResult: Result of running the disconnect command
        """
        # Get the bot's voice client instance for this server
        client: MusicBotClient | None = self.clients.get(ctx.guild.id)
        
        if client==None:
            return CmdResult.err("Bot is not connected to a voice channel!")
        else: 
            await client.disconnect(reason = f"Disconnected by <@{ctx.message.author.id}>", force = True)
            return CmdResult.ok(None)
        
    async def move(self, ctx: CmdContext) -> CmdResult:
        """Have the bot move to the voice channel the messaging user is in

        Args:
            ctx (CmdContext): Context given to this command

        Returns:
            CmdResult: Result of running the move command
        """
        # Get the bot's voice client instance for this server
        client: MusicBotClient | None = self.clients.get(ctx.guild.id)
        
        if client==None:
            return CmdResult.err("Bot must be connected to a voice channel before moving!")
        else: 
            # Move to message author's voice channel
            user = ctx.message.author
            if user.voice and user.voice.channel:
                client.move_to(user.voice.channel)
            else:
                return CmdResult.err("You must be in a voice channel!")
        return CmdResult.ok(None)
    
    async def play(self, ctx: CmdContext) -> CmdResult:
        """Plays a given song if the bot is inactive.
        If the bot is active, then this will just append the song to the queue.

        Args:
            ctx (CmdContext): Context given to this command

        Returns:
            CmdResult: Result of running the play command
        """
        # Get the bot's voice client instance for this server
        client: MusicBotClient | None = self.clients.get(ctx.guild.id)
        
        # If the client is not connected to a vc, join the vc
        if client==None:
            join_result: CmdResult = await self.join(ctx)
            if join_result.is_err(): return join_result
            else: client = join_result.unwrap()
        
        # Updates the channel that the bot should send messages to
        client.set_msg_channel(ctx.message.channel)
        
        # Add the song to the queue
        song: QueuedSong | Exception | None = await client.enqueue(ctx.arg)
        if song and type(song)==QueuedSong:
            await self._on_queue(song, client)
            if not client.is_active():
                client.play_next()
            return CmdResult.ok(None)
        elif song:
            return CmdResult.err(f"Could not queue song\n`{song}`")
        else:
            if not client.is_connected():
                return CmdResult.err()
            else:
                return CmdResult.err("Could not queue some\n`TypeError: 'NoneType' is not a valid song`")

            
    async def skip(self, ctx: CmdContext) -> CmdResult:
        """Skips to the next song in the queue

        Args:
            ctx (CmdContext): Context given to this command

        Returns:
            CmdResult: Result of running the skip command
        """
        # Get the bot's voice client instance for this server
        client: MusicBotClient | None = self.clients.get(ctx.guild.id)
        
        if client==None: return CmdResult.err("Bot is not connected to a voice channel!")
        
        # Play the next song
        client.play_next()
        
        return CmdResult.ok(None)
    
    async def show_queue(self, ctx: CmdContext) -> CmdResult:
        """Prints the music queue of the bot

        Args:
            ctx (CmdContext): Context given to the show queue command

        Returns:
            CmdResult: Result of running the command
        """
        # Get the bot's voice client instance for this server
        client: MusicBotClient | None = self.clients.get(ctx.guild.id)

        if client==None: return CmdResult.err("Bot is not connected to a voice channel!")
        
        # await ctx.message.channel.send("\n".join([f"{i+1}. {q.name}" for i,q in enumerate(client.queue)]))
        await self._show_queue(ctx, client.queue, client.curr_song()[1])
        
        return CmdResult.ok(None)
    
    async def loop(self, ctx: CmdContext) -> CmdResult:
        """Toggles the loop for the bot

        Args:
            ctx (CmdContext): Context given to the loop command

        Returns:
            CmdResult: Result of running the command
        """
        # Get the bot's voice client instance for this server
        client: MusicBotClient | None = self.clients.get(ctx.guild.id)

        if client==None: return CmdResult.err("Bot is not connected to a voice channel!")
        
        if client.toggle_loop():
            await ctx.message.channel.send("Loop enabled")
        else:
            await ctx.message.channel.send("Loop disabled")
        
        return CmdResult.ok(None)
    
    async def remove(self, ctx: CmdContext) -> CmdResult:
        """Removes a song from the queue

        Args:
            ctx (CmdContext): Context given to this command

        Returns:
            CmdResult: Result of running the remove command
        """
        if not ctx.arg.isnumeric(): return CmdResult.err("Must provide number of the song in queue to remove!")
        
        # Get the bot's voice client instance for this server
        client: MusicBotClient | None = self.clients.get(ctx.guild.id)
        
        if client==None: return CmdResult.err("Bot is not connected to a voice channel!")
        
        song: QueuedSong | Exception = client.pop_queue(int(ctx.arg) - 1)
        if type(song)==QueuedSong:
            await ctx.message.channel.send(f"Removed `{song.name}` from queue")
            return CmdResult.ok(None)
        else:
            return CmdResult.err(f"Could not remove song:\n`{str(song)}`")
        
    async def clear(self, ctx: CmdContext) -> CmdResult:
        """Clears the song queue

        Args:
            ctx (CmdContext): Context given to this command

        Returns:
            CmdResult: Result of running the clear command
        """        
        # Get the bot's voice client instance for this server
        client: MusicBotClient | None = self.clients.get(ctx.guild.id)
        
        if client==None: return CmdResult.err("Bot is not connected to a voice channel!")
        
        # Clear the queue. This should(?) never error
        client.clear_queue()
        
        return CmdResult.ok(None)
            
    def _setup_commands(self, bot: CmdRunner):
        """Setup all music bot related commands using discordbot.Bot, which will assign the given functions
        to run when a certain "command" message is sent in a discord text channel.

        Args:
            bot (Bot): The Bot instance we are assigning the commands to.
        """
        bot[['join', 'j']] = self.join
        bot['move'] = self.move
        bot[['play', 'p']] = self.play
        bot[['disconnect', 'leave', 'dc']] = self.disconnect
        bot['skip'] = self.skip
        bot[['queue', 'q']] = self.show_queue
        bot[['remove', 'rm']] = self.remove
        bot['loop'] = self.loop
        bot['clear'] = self.clear
    
    def set_on_play(self, on_play: Callable[[QueuedSong, MusicBotClient], Awaitable[None]]):
        self._on_play: Callable[[QueuedSong, MusicBotClient]] = on_play
        
    def set_on_queue(self, on_queue: Callable[[QueuedSong, MusicBotClient], Awaitable[None]]):
        """Set a command to run whenever a song is queued

        Args:
            on_queue (Callable[[QueuedSong, MusicBotClient], Awaitable[None]]): async function that gets run
        """
        self._on_queue: Callable[[QueuedSong, MusicBotClient]] = on_queue
        
    def set_on_disconnect(self, on_dc: Callable[[MusicBotClient, str | None], Awaitable[None]]):
        self._custom_on_dc: Callable[[MusicBotClient, str | None], Awaitable[None]] = on_dc
        
    def set_show_queue(self, show_queue: Callable[[CmdContext, list[QueuedSong], int], Awaitable[None]]):
        self._show_queue = show_queue
    
    ##### Private functions #####
    
    # Callbacks
    async def _default_on_play(self, song: QueuedSong, client: MusicBotClient):
        await client.msg_channel.send(embed=discord.Embed(title = "Now Playing", description = f"{song.name} [{song.duration}]\n<t:{int(time.time())}:R>", url=song.url)
                                      .set_thumbnail(url = song.thumbnail))
        
    async def _default_on_queue(self, song: QueuedSong, client: MusicBotClient):
        if client.is_active():
            await client.msg_channel.send(embed=discord.Embed(title = "Queued", description = f"{song.name} [{song.duration}]", url=song.url)
                                          .set_thumbnail(url = song.thumbnail))
    
    async def _on_dc(self, client: MusicBotClient, reason: str | None = None):
        # This must run when the bot disconnects
        temp: MusicBotClient = self.clients.get(client.guild.id)
        if temp: self.clients.pop(client.guild.id)
        
        # User may define a custom function that runs when the bot disconnects from a voice channel. 
        # For example: a disconnect message. 
        await self._custom_on_dc(client, reason)
        
    async def _default_on_dc(self, client: MusicBotClient, reason: str | None = None):
        await client.msg_channel.send(embed=discord.Embed(title="Disconnected", description=reason))
        
    async def _default_show_queue(self, ctx: CmdContext, queue: list[QueuedSong], curr_idx: int):
        if len(queue) > 0:
            for page in range((len(queue)-1)//8+1):
                start: int = page*8
                embed: discord.Embed = discord.Embed(
                    title="Queue" + (f" {page+1}" if len(queue) > 8 else ""),
                    description='\n'.join([f"{i+1+start}. "+(f"🎶 **{s.name}**" if i+start==curr_idx else s.name) + f" [[{s.duration}]({s.url})]" for i,s in enumerate(queue[start:start+9])]))
                await ctx.message.channel.send(embed=embed)
        else:
            await ctx.message.channel.send("Queue is empty!")
                
