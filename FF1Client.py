import asyncio
import json
import time
from asyncio import StreamReader, StreamWriter
from typing import List

import Utils
from CommonClient import CommonContext, server_loop, gui_enabled, console_loop, ClientCommandProcessor, logger

SYSTEM_MESSAGE_ID = 0

DATA_KEEP_ALIVE = b'\n'

CONNECTION_TIMING_OUT_STATUS = "Connection timing out. Please restart your emulator then restart ff1_connector.lua"
CONNECTION_REFUSED_STATUS = "Connection Refused. Please start your emulator make sure ff1_connector.lua is running"
CONNECTION_RESET_STATUS = "Connection was reset. Please restart your emulator then restart ff1_connector.lua"
CONNECTION_TENTATIVE_STATUS = "Initial Connection Made"
CONNECTION_CONNECTED_STATUS = "Connected"
CONNECTION_INITIAL_STATUS = "Connection has not been initiated"


class FF1CommandProcessor(ClientCommandProcessor):
    def __init__(self, ctx: CommonContext):
        super().__init__(ctx)

    def _cmd_nes(self):
        """Check NES Connection State"""
        if isinstance(self.ctx, FF1Context):
            logger.info(f"NES Status: {self.ctx.nes_status}")


class FF1Context(CommonContext):
    def __init__(self, server_address, password):
        super().__init__(server_address, password)
        self.nes_streams: (StreamReader, StreamWriter) = None
        self.nes_sync_task = None
        self.messages = {}
        self.locations_array = None
        self.nes_status = CONNECTION_INITIAL_STATUS

    command_processor = FF1CommandProcessor

    async def server_auth(self, password_requested: bool = False):
        if password_requested and not self.password:
            await super(FF1Context, self).server_auth(password_requested)
        if not self.auth:
            logger.info('Enter slot name:')
            self.auth = await self.console_input()

        await self.send_msgs([{"cmd": 'Connect',
                               'password': self.password, 'name': self.auth, 'version': Utils.version_tuple,
                               'tags': {},
                               'uuid': Utils.get_unique_identifier(), 'game': 'Final Fantasy'
                               }])

    def _set_message(self, msg: str, msg_id: int):
        self.messages[(time.time(), msg_id)] = msg

    def on_package(self, cmd: str, args: dict):
        if cmd == 'Connected':
            self.game = self.games.get(self.slot, None)
        elif cmd == 'Print':
            msg = args['text']
            if ': !' not in msg:
                self._set_message(msg, SYSTEM_MESSAGE_ID)
        elif cmd == "ReceivedItems":
            msg = f"Recieved {', '.join([self.item_name_getter(item.item) for item in args['items']])}"
            self._set_message(msg, SYSTEM_MESSAGE_ID)
        elif cmd == 'PrintJSON':
            print_type = args['type']
            item = args['item']
            receiving_player_id = args['receiving']
            receiving_player_name = self.player_names[receiving_player_id]
            sending_player_id = item.player
            sending_player_name = self.player_names[item.player]
            if print_type == 'Hint':
                msg = f"Hint: Your {self.item_name_getter(item.item)} is at" \
                      f" {self.player_names[item.player]}'s {self.location_name_getter(item.location)}"
                self._set_message(msg, item.item)
            elif print_type == 'ItemSend' and receiving_player_id != self.slot:
                if sending_player_id == self.slot:
                    if receiving_player_id == self.slot:
                        msg = f"You found your own {self.item_name_getter(item.item)}"
                    else:
                        msg = f"You sent {self.item_name_getter(item.item)} to {receiving_player_name}"
                else:
                    if receiving_player_id == sending_player_id:
                        msg = f"{sending_player_name} found their {self.item_name_getter(item.item)}"
                    else:
                        msg = f"{sending_player_name} sent {self.item_name_getter(item.item)} to " \
                              f"{receiving_player_name}"
                self._set_message(msg, item.item)


def get_payload(ctx: FF1Context):
    current_time = time.time()
    return json.dumps(
        {
            "items": [item.item for item in ctx.items_received],
            "messages": {f'{key[0]}:{key[1]}': value for key, value in ctx.messages.items()
                         if key[0] > current_time - 10}
        }
    )


async def parse_locations(locations_array: List[int], ctx: FF1Context):
    if locations_array == ctx.locations_array:
        return
    else:
        # print("New values")
        ctx.locations_array = locations_array
        locations_checked = []
        if locations_array[0xFE] & 0x02 != 0 and not ctx.finished_game:
            await ctx.send_msgs([
                {"cmd": "StatusUpdate",
                 "status": 30}
            ])
            ctx.finished_game = True
        for location in ctx.missing_locations:
            # index will be - 0x100 or 0x200
            index = location
            if location < 0x200:
                # Location is a chest
                index -= 0x100
                flag = 0x04
            else:
                # Location is an NPC
                index -= 0x200
                flag = 0x02

            # print(f"Location: {ctx.location_name_getter(location)}")
            # print(f"Index: {str(hex(index))}")
            # print(f"value: {locations_array[index] & flag != 0}")
            if locations_array[index] & flag != 0:
                locations_checked.append(location)
        if locations_checked:
            # print([ctx.location_name_getter(location) for location in locations_checked])
            await ctx.send_msgs([
                {"cmd": "LocationChecks",
                 "locations": locations_checked}
            ])


async def nes_sync_task(ctx: FF1Context):
    logger.info("Starting nes connector. Use /nes for status information")
    while not ctx.exit_event.is_set():
        error_status = None
        if ctx.nes_streams:
            (reader, writer) = ctx.nes_streams
            msg = get_payload(ctx).encode()
            writer.write(msg)
            writer.write(b'\n')
            try:
                await asyncio.wait_for(writer.drain(), timeout=1.5)
                try:
                    # Data will return one of 2 things:
                    # 1. A keepalive response of \n
                    # 2. An array representing the memory values of the locations area
                    data = await asyncio.wait_for(reader.readline(), timeout=5)
                    # print(data)
                    if ctx.game is not None and data != DATA_KEEP_ALIVE:
                        # Not just a keep alive ping, parse
                        asyncio.create_task(parse_locations(json.loads(data.decode()), ctx))
                except asyncio.TimeoutError:
                    logger.debug("Read Timed Out, Reconnecting")
                    error_status = CONNECTION_TIMING_OUT_STATUS
                    writer.close()
                    ctx.nes_streams = None
                except ConnectionResetError as e:
                    logger.debug("Read failed due to Connection Lost, Reconnecting")
                    error_status = CONNECTION_RESET_STATUS
                    writer.close()
                    ctx.nes_streams = None
            except TimeoutError:
                logger.debug("Connection Timed Out, Reconnecting")
                error_status = CONNECTION_TIMING_OUT_STATUS
                writer.close()
                ctx.nes_streams = None
            except ConnectionResetError:
                logger.debug("Connection Lost, Reconnecting")
                error_status = CONNECTION_RESET_STATUS
                writer.close()
                ctx.nes_streams = None
            if ctx.nes_status == CONNECTION_TENTATIVE_STATUS:
                if not error_status:
                    logger.info("Successfully Connected to NES")
                    ctx.nes_status = CONNECTION_CONNECTED_STATUS
                else:
                    ctx.nes_status = f"Was tentatively connected but error occured: {error_status}"
            elif error_status:
                ctx.nes_status = error_status
                logger.info("Lost connection to nes and attempting to reconnect. Use /nes for status updates")
        else:
            try:
                logger.debug("Attempting to connect to NES")
                ctx.nes_streams = await asyncio.wait_for(asyncio.open_connection("localhost", 52980), timeout=10)
                ctx.nes_status = CONNECTION_TENTATIVE_STATUS
            except TimeoutError:
                logger.debug("Connection Timed Out, Trying Again")
                ctx.nes_status = CONNECTION_TIMING_OUT_STATUS
                continue
            except ConnectionRefusedError:
                logger.debug("Connection Refused, Trying Again")
                ctx.nes_status = CONNECTION_REFUSED_STATUS
                continue


if __name__ == '__main__':
    # Text Mode to use !hint and such with games that have no text entry
    Utils.init_logging("TextClient")

    async def main(args):
        ctx = FF1Context(args.connect, args.password)
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="ServerLoop")
        if gui_enabled:
            input_task = None
            from kvui import TextManager
            ctx.ui = TextManager(ctx)
            ui_task = asyncio.create_task(ctx.ui.async_run(), name="UI")
        else:
            input_task = asyncio.create_task(console_loop(ctx), name="Input")
            ui_task = None

        ctx.nes_sync_task = asyncio.create_task(nes_sync_task(ctx), name="NES Sync")
        await ctx.exit_event.wait()

        ctx.server_address = None
        if ctx.server and not ctx.server.socket.closed:
            await ctx.server.socket.close()
        if ctx.server_task:
            await ctx.server_task
        if ctx.nes_sync_task:
            await ctx.nes_sync_task

        while ctx.input_requests > 0:
            ctx.input_queue.put_nowait(None)
            ctx.input_requests -= 1

        if ui_task:
            await ui_task

        if input_task:
            input_task.cancel()


    import argparse
    import colorama

    parser = argparse.ArgumentParser(description="FF1 Archipelago Client")
    parser.add_argument('--connect', default=None, help='Address of the multiworld host.')
    parser.add_argument('--password', default=None, help='Password of the multiworld host.')
    if not Utils.is_frozen():  # Frozen state has no cmd window in the first place
        parser.add_argument('--nogui', default=False, action='store_true', help="Turns off Client GUI.")

    args, rest = parser.parse_known_args()
    colorama.init()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(args))
    loop.close()
    colorama.deinit()