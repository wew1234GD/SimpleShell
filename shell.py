import os
import shutil
import time
import sys
import signal
import atexit
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style
import miniupnpc
import subprocess

COMMANDS = [
    "ls", "cd", "pwd", "mkdir", "touch", "rm",
    "cat", "clear", "exit", "help", "credits", "public"
]

upnp_instance = None
mapped_port = None
mapped_proto = None
fw_rule_name = None

def cleanup_upnp():
    global upnp_instance, mapped_port, mapped_proto, fw_rule_name
    if not any([upnp_instance, fw_rule_name]):
        return
    if upnp_instance and mapped_port and mapped_proto:
        try:
            print(f"[info] Удаление проброса порта {mapped_port}/{mapped_proto} через UPnP...")
            upnp_instance.deleteportmapping(mapped_port, mapped_proto)
            print("[info] Проброс удалён.")
        except Exception as e:
            if 'NoSuchEntryInArray' in str(e):
                print(f"[info] UPnP-проброс для {mapped_port}/{mapped_proto} уже отсутствует.")
            else:
                print(f"[error] Ошибка удаления UPnP: {e}")
        finally:
            upnp_instance = None
            mapped_port = None
            mapped_proto = None
    if fw_rule_name:
        try:
            print(f"[info] Удаление правила брандмауэра '{fw_rule_name}'...")
            subprocess.run([
                "netsh", "advfirewall", "firewall", "delete", "rule",
                f'name="{fw_rule_name}"'
            ], check=True, capture_output=True, text=True)
            print("[info] Правило удалено.")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip()
            if 'No rules match the specified criteria' in stderr or 'Ни одно правило не соответствует' in stderr:
                print(f"[info] Правило '{fw_rule_name}' уже отсутствует.")
            else:
                print(f"[error] Не удалось удалить правило брандмауэра: {stderr}")
        finally:
            fw_rule_name = None


def handle_signal(sig, frame):
    cleanup_upnp()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
atexit.register(cleanup_upnp)

class CommandOnlyCompleter(Completer):
    def __init__(self, commands):
        self.commands = commands

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        words = text.split()
        if len(words) <= 1 and not text.endswith(" "):
            for cmd in self.commands:
                if cmd.startswith(document.get_word_before_cursor()):
                    yield Completion(cmd, start_position=-len(document.get_word_before_cursor()))

def add_firewall_rule(port, proto):
    global fw_rule_name
    fw_rule_name = f"MiniShell_{port}_{proto}"
    cmd = [
        "netsh", "advfirewall", "firewall", "add", "rule",
        f'name="{fw_rule_name}"', "dir=in", "action=allow",
        f"protocol={proto}", f"localport={port}"
    ]
    try:
        print(f"[info] Добавление правила брандмауэра '{fw_rule_name}' для {proto}/{port}...")
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("[info] Правило успешно добавлено.")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip()
        if 'exist' in stderr.lower() or 'уже существует' in stderr or 'access is denied' in stderr.lower() or 'отказано' in stderr.lower():
            print(f"[warn] При добавлении правила: {stderr}")
            print("[warn] Убедитесь, что запускаете скрипт от имени администратора, или создайте правило вручную.")
            return True 
        print(f"[error] Не удалось добавить правило брандмауэра: {stderr}")
        return False

style = Style.from_dict({
    "prompt": "ansigreen bold",
    "error": "ansired",
    "info": "ansiblue"
})

completer = CommandOnlyCompleter(COMMANDS)

def setup_upnp(port, proto):
    global upnp_instance, mapped_port, mapped_proto
    u = miniupnpc.UPnP()
    u.discoverdelay = 200
    try:
        devices = u.discover()
        if not devices:
            print(f"[error] UPnP устройства не найдены.")
            return None, None
        u.selectigd()
        external_ip = u.externalipaddress()

        if u.addportmapping(port, proto, u.lanaddr, port, 'Public port for app', ''):
            upnp_instance = u
            mapped_port = port
            mapped_proto = proto
            return external_ip, port
        else:
            print(f"[error] Не удалось добавить порт {port}/{proto} через UPnP.")
            return None, None
    except Exception as e:
        print(f"[error] Ошибка UPnP: {e}")
        return None, None

def run_shell():
    session = PromptSession()
    cwd = Path.cwd()

    while True:
        try:
            text = session.prompt(f"{cwd} $ ", completer=completer, style=style)
            args = text.strip().split()
            if not args:
                continue
            command, *params = args

            if command == "exit":
                print("[info] Завершение сессии...")
                break
            elif command == "help":
                print("\nДоступные команды:")
                for cmd in COMMANDS:
                    print(f" - {cmd}")
                print()
            elif command == "pwd":
                print(cwd)
            elif command == "ls":
                for f in cwd.iterdir():
                    print(f"{'[DIR]' if f.is_dir() else '     '} {f.name}")
            elif command == "cd":
                target = Path.home() if not params else (cwd / params[0]).resolve()
                if target.exists() and target.is_dir():
                    cwd = target
                else:
                    print("Папка не найдена.")
            elif command == "mkdir":
                for p in params:
                    try:
                        (cwd / p).mkdir()
                    except Exception as e:
                        print(f"Ошибка создания '{p}': {e}")
            elif command == "touch":
                for p in params:
                    try:
                        (cwd / p).touch()
                    except Exception as e:
                        print(f"Ошибка создания '{p}': {e}")
            elif command == "rm":
                for p in params:
                    path = cwd / p
                    try:
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                    except Exception as e:
                        print(f"Ошибка удаления '{p}': {e}")
            elif command == "cat":
                for p in params:
                    try:
                        with open(cwd / p, "r", encoding="utf-8") as f:
                            print(f.read())
                    except Exception as e:
                        print(f"Ошибка чтения '{p}': {e}")
            elif command == "clear":
                os.system("cls" if os.name == "nt" else "clear")
            elif command == "credits":
                print("""
   _____ _                 _            _____ _          _ _ 
  / ____(_)               | |          / ____| |        | | |
 | (___  _ _ __ ___  _ __ | | ___     | (___ | |__   ___| | |
  \___ \| | '_ ` _ \| '_ \| |/ _ \     \___ \| '_ \ / _ \ | |
  ____) | | | | | | | |_) | |  __/     ____) | | | |  __/ | |
 |_____/|_|_| |_| |_| .__/|_|\___|    |_____/|_| |_|\___|_|_|
                    | |                                      
                    |_|                                     
Version: V1.11
Creator: Candy
                """)
            elif command == "public":
                if len(params) < 2:
                    print("Использование: public <TCP|UDP> --port:<num>")
                    continue
                proto = params[0].upper()
                if proto not in ("TCP", "UDP"):
                    print("Неверный протокол. Укажите TCP или UDP.")
                    continue
                port = None
                for p in params[1:]:
                    if p.startswith("--port:"):
                        try:
                            port = int(p.split(':', 1)[1])
                        except ValueError:
                            pass
                if port is None:
                    print("Параметр порта не найден или неверен.")
                    continue

                if not add_firewall_rule(port, proto):
                    continue

                print(f"Попытка проброса порта {port}/{proto} через UPnP")
                ext_ip, ext_port = setup_upnp(port, proto)
                if ext_ip is None:
                    print("[error] Проброс не удался. Возможно нет UPnP устройства или порт занят.")
                else:
                    print(f"[info] Проброс успешен! Подключайтесь по адресу {ext_ip}:{ext_port}")
                    print("[info] Нажмите Ctrl+C или введите exit для выхода и удаления проброса.")
                    try:
                        while True:
                            time.sleep(10)
                            os.system("cls" if os.name == "nt" else "clear")
                            print(f"[info] Ваш публичный адрес: {ext_ip}:{ext_port}")
                            print("[info] Нажмите Ctrl+C или введите exit для выхода.")
                    except KeyboardInterrupt:
                        print("\n[info] Выход из режима public...")
                        break
            else:
                print(f"Неизвестная команда: {command}")
        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            print()
            break

run_shell()
