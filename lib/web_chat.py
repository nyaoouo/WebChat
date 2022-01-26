import ctypes
import time
import os
import re
import traceback
import webbrowser
from ctypes import *
from pathlib import Path
from asyncio import set_event_loop_policy, WindowsSelectorEventLoopPolicy, new_event_loop, \
    CancelledError, set_event_loop, run as arun, sleep as asleep
from threading import Thread

from aiohttp import web, WSMsgType

set_event_loop_policy(WindowsSelectorEventLoopPolicy())
special_char = re.compile(r"[\uE020-\uE0DB]")


def parse_msg_chain(msg_chain):
    msg = []
    skip = False
    for m in msg_chain:
        match m.Type:
            case 'Interactable/Item':
                msg.append({
                    'type': 'item',
                    'data': {
                        'id': m.item_id,
                        'hq': m.is_hq,
                        'collect': m.is_collect,
                        'name': m.display_name,
                    },
                })
                skip = True
            case 'Interactable/MapPositionLink':
                msg.append({
                    'type': 'map',
                    'data': {
                        'map': m.map_id,
                        'x': m.map_x,
                        'y': m.map_y,
                        'name': m.text()
                    },
                })
                skip = True
            case 'Interactable/Status':
                pass
            case 'Interactable/LinkTerminator':
                skip = False
            case _:
                if skip: continue

        match m.Type:
            case 'Icon':
                msg.append({
                    'type': 'icon',
                    'data': m.icon_id,
                })
            case 'AutoTranslateKey' | 'Text':
                if m.Type == 'AutoTranslateKey':
                    text = f"\ue040{m.text()}\ue041"
                else:
                    text = m.text()
                if msg and msg[-1]['type'] == 'text':
                    msg[-1]['data'] += text
                else:
                    msg.append({
                        'type': 'text',
                        'data': text,
                    })
    return msg


def run(host: str, port: int):
    def open_web():
        webbrowser.open_new_tab(f"http://127.0.0.1:{port}/index.html")

    from .memory import PROCESS_FILENAME, BASE_ADDR, read_memory, read_string
    game_base_dir = Path(PROCESS_FILENAME).parent.parent
    if (game_base_dir / "FFXIVBoot.exe").exists() or (game_base_dir / "rail_files" / "rail_game_identify.json").exists():
        os.environ['game_language'] = "chs"
        os.environ['game_ext'] = '3'
    else:
        os.environ['game_language'] = "en"
        os.environ['game_ext'] = '4'

    from .text_pattern import find_signature_address, find_signature_point
    print_chat_log_offset = find_signature_address("40 55 53 56 41 54 41 57 48 8D AC 24 ?? ?? ?? ?? 48 81 EC 20 02 00 00 48 8B 05")
    do_text_command_offset = find_signature_address("48 89 5C 24 ? 57 48 83 EC 20 48 8B FA 48 8B D9 45 84 C9")
    text_command_ui_module_offset = find_signature_point("48 8B 05 * * * * 48 8B D9 8B 40 14 85 C0")
    player_name_addr = find_signature_point("48 8D 0D * * * * E8 ? ? ? ? 0F B6 F0 0F B6 05 ? ? ? ?") + 1 + BASE_ADDR

    def player_name():
        return read_string(player_name_addr)

    from .memory.struct_factory import PointerStruct, OffsetStruct
    ui_module = read_memory(POINTER(c_void_p), text_command_ui_module_offset + BASE_ADDR)
    _do_text_command = CFUNCTYPE(c_int64, c_void_p, c_void_p, c_int64, c_char)(do_text_command_offset + BASE_ADDR)
    TextCommandStruct = OffsetStruct({"cmd": c_void_p, "t1": c_longlong, "tLength": c_longlong, "t3": c_longlong}, full_size=400)

    def do_text_command(command: str | bytes):
        if isinstance(command, str): command = command.encode('utf-8')
        cmd_size = len(command)
        cmd = OffsetStruct({"cmd": c_char * cmd_size}, full_size=cmd_size + 30)(cmd=command)
        arg = TextCommandStruct(cmd=addressof(cmd), t1=64, tLength=cmd_size + 1, t3=0)
        return _do_text_command(ui_module[0], addressof(arg), 0, 0)

    from .hook import Hook
    from .se_string import ChatLog, get_message_chain

    class PrintChatLogHook(Hook):
        restype = c_int64
        argtypes = [c_int64, c_ushort, POINTER(c_char_p), POINTER(c_char_p), c_uint, c_ubyte]
        auto_install = True

        def __init__(self, func_address: int):
            super().__init__(func_address)

        def hook_function(self, manager, channel_id, p_sender, p_msg, sender_id, parm):
            try:
                Thread(target=on_log, args=(ChatLog(
                    time.time(),
                    channel_id,
                    get_message_chain(bytearray(p_sender[0])),
                    get_message_chain(bytearray(p_msg[0]))
                ),)).start()
            except:
                pass
            return self.original(manager, channel_id, p_sender, p_msg, sender_id, parm)

    clients = dict()
    client_count = 0
    history = []

    async def root_handler(request):
        return web.HTTPFound('/index.html')

    async def ws_handler(request):
        nonlocal client_count
        ws = web.WebSocketResponse()
        cid = client_count
        client_count += 1
        clients[cid] = ws
        await ws.prepare(request)
        try:
            for m in history[-200:]:
                await ws.send_json(m)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        do_text_command(msg.data)
                    except Exception as e:
                        await ws.send_json({
                            'epoch': time.time(),
                            'sender': [{'type': 'text', 'data': 'error'}],
                            'text': [{'type': 'text', 'data': str(e)}],
                            'channel': -1,
                        })
                elif msg.type == WSMsgType.ERROR:
                    pass
        except CancelledError:
            pass
        del clients[cid]
        return ws

    def on_log(chat_log: ChatLog):
        nonlocal history
        set_event_loop(loop)
        if chat_log.channel_id == 56 and chat_log.messages_text == "web_chat":
            open_web()
        data = {
            'epoch': chat_log.timestamp,
            'sender': parse_msg_chain(chat_log.sender) if special_char.sub('', chat_log.sender_text) != player_name() else None,
            'msg': parse_msg_chain(chat_log.messages),
            'channel': chat_log.channel_id,
        }
        history.append(data)
        if len(history) > 500:
            history = history[-200:]
        for cid, ws in clients.items():
            arun(ws.send_json(data))

    async def main():
        set_event_loop(loop)
        app = web.Application()
        app.router.add_route('GET', '/', root_handler)
        app.router.add_route('GET', '/ws', ws_handler)
        app.router.add_static('/', path=Path(os.getcwd()) / 'res')
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        open_web()
        while True: await asleep(10)

    hook = PrintChatLogHook(print_chat_log_offset + BASE_ADDR)
    hook.install_and_enable()
    loop = new_event_loop()
    set_event_loop(loop)
    loop.run_until_complete(main())
