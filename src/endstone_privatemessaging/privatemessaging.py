from endstone.plugin import Plugin
from endstone.command import Command, CommandSender
from endstone.event import (
    event_handler,
    PlayerQuitEvent,
    PlayerCommandEvent,
    ServerCommandEvent,
)
from endstone import Player


DEFAULT_MESSAGES = {
    "usage": "&4Lệnh Sai. Nhập /help để xem tất cả lệnh.",
    "player_not_found": "&6Người chơi &e'{query}' &6không tìm thấy.",
    "cannot_message_self": "&6Bạn không thể tự nhắn tin cho chính mình.",
    "no_reply_target": "&6Không có ai để trả lời.",
    "target_offline": "&e'{target}' &6đang ngoại tuyến.",
    "console_cannot_reply": "&4Chỉ người chơi mới có thể sử dụng /r - /reply.",
    "did_you_mean": "&6Ý bạn là: &e'{list}'",
    "message_to_sender": "&5đến {target}: {message}",
    "message_to_target": "&5{sender} thì thầm: {message}",
}

INTERCEPTED_MSG_COMMANDS = {"msg", "tell", "whisper",}
INTERCEPTED_REPLY_COMMANDS = {"r", "reply"}


class PrivateMessaging(Plugin):
    api_version = "0.11"

    name = "PrivateMessaging"
    version = "1.0.0"
    description = "Privatemessaging"

    commands = {
        "pm": {
            "description": "Gửi tin nhắn riêng cho một người chơi",
            "usages": ["/pm <player> <message>"],
            "permissions": ["privatemessaging.command.msg"],
        },
        "reply": {
            "description": "Trả lời người chơi gần nhất đã nhắn tin cho bạn",
            "usages": ["/reply <message>"],
            "permissions": ["privatemessaging.command.reply"],
        },
    }

    permissions = {
        "privatemessaging.command.msg": {
            "description": "Allows sending private messages",
            "default": True,
        },
        "privatemessaging.command.reply": {
            "description": "Allows using reply command",
            "default": True,
        },
    }

    def __init__(self):
        super().__init__()
        self._reply_targets: dict[str, str] = {}
        self._prefix: str = ""
        self._fuzzy_match: bool = True
        self._suggestion_limit: int = 5
        self._log_messages: bool = True
        self._messages: dict = {}

    def on_load(self) -> None:
        self.logger.info("PrivateMessaging loading...")
        self.save_default_config()

    def on_enable(self) -> None:
        try:
            config = self.config
            raw_prefix = config.get("prefix", "&a&lPrivateMessaging&r")
            self._fuzzy_match = bool(config.get("fuzzy_match", True))
            self._suggestion_limit = int(config.get("suggestion_limit", 5))
            self._log_messages = bool(config.get("log_messages", True))

            cfg_messages = config.get("messages", {}) or {}
            self._messages = {}
            for key, default_val in DEFAULT_MESSAGES.items():
                self._messages[key] = cfg_messages.get(key, default_val)
        except Exception as e:
            self.logger.warning(f"Config error, using fallback: {e}")
            raw_prefix = "&a&lPrivateMessaging&r"
            self._messages = dict(DEFAULT_MESSAGES)

        self._prefix = self._colorize(raw_prefix)

        self.register_events(self)
        self.logger.info("PrivateMessaging enabled - intercepting vanilla commands!")

    def on_disable(self) -> None:
        self._reply_targets.clear()
        self.logger.info("PrivateMessaging disabled!")

    @staticmethod
    def _parse_command_line(raw: str) -> "tuple[str | None, str]":
        cmd_line = raw.strip()
        if cmd_line.startswith("/"):
            cmd_line = cmd_line[1:]
        if not cmd_line:
            return None, ""

        parts = cmd_line.split(maxsplit=1)
        if not parts:
            return None, ""

        cmd_name = parts[0].lower()
        raw_args = parts[1] if len(parts) > 1 else ""
        return cmd_name, raw_args

    @event_handler
    def on_player_command(self, event: PlayerCommandEvent) -> None:
        cmd_name, raw_args = self._parse_command_line(event.command)
        if cmd_name is None:
            return

        if cmd_name in INTERCEPTED_MSG_COMMANDS:
            event.is_cancelled = True
            args = raw_args.split() if raw_args else []
            self._handle_msg(event.player, args)
            return

        if cmd_name in INTERCEPTED_REPLY_COMMANDS:
            event.is_cancelled = True
            args = raw_args.split() if raw_args else []
            self._handle_reply(event.player, args)
            return

    @event_handler
    def on_server_command(self, event: ServerCommandEvent) -> None:
        cmd_name, raw_args = self._parse_command_line(event.command)
        if cmd_name is None:
            return

        if cmd_name in INTERCEPTED_MSG_COMMANDS:
            event.is_cancelled = True
            args = raw_args.split() if raw_args else []
            self._handle_msg(event.sender, args)
            return

        if cmd_name in INTERCEPTED_REPLY_COMMANDS:
            event.is_cancelled = True
            args = raw_args.split() if raw_args else []
            self._handle_reply(event.sender, args)
            return

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent) -> None:
        name = event.player.name
        self._reply_targets.pop(name, None)
        stale = [k for k, v in self._reply_targets.items() if v == name]
        for k in stale:
            del self._reply_targets[k]

    def on_command(
        self, sender: CommandSender, command: Command, args: list[str]
    ) -> bool:
        name = command.name.lower()
        if name == "pm":
            return self._handle_msg(sender, args)
        if name == "reply":
            return self._handle_reply(sender, args)
        return False

    def _msg(self, key: str, **kwargs) -> str:
        template = self._messages.get(key, DEFAULT_MESSAGES.get(key, ""))
        kwargs.setdefault("prefix", self._prefix)
        try:
            text = template.format(**kwargs)
        except (KeyError, IndexError):
            text = template
        return self._colorize(text)

    @staticmethod
    def _colorize(text: str) -> str:
        if not text:
            return ""
        return text.replace("&", "\u00a7")

    def _handle_msg(self, sender: CommandSender, args: list[str]) -> bool:
        if len(args) < 2:
            sender.send_message(self._msg("usage"))
            return False

        query = args[0]
        message = " ".join(args[1:])
        sender_name = sender.name if isinstance(sender, Player) else "Console"

        target = self._find_player(query)

        if target is None:
            sender.send_message(self._msg("player_not_found", query=query))
            suggestions = self._find_similar(query, self._suggestion_limit)
            if suggestions:
                sender.send_message(
                    self._msg("did_you_mean", list=", ".join(suggestions))
                )
            return True

        if isinstance(sender, Player) and target.name.lower() == sender_name.lower():
            sender.send_message(self._msg("cannot_message_self"))
            return True

        self._send_pm(sender, target, message)
        return True

    def _handle_reply(self, sender: CommandSender, args: list[str]) -> bool:
        if not isinstance(sender, Player):
            sender.send_message(self._msg("console_cannot_reply"))
            return True

        if len(args) < 1:
            sender.send_message(self._msg("usage"))
            return False

        sender_name = sender.name
        message = " ".join(args)

        if sender_name not in self._reply_targets:
            sender.send_message(self._msg("no_reply_target"))
            return True

        target_name = self._reply_targets[sender_name]
        target = self._find_exact(target_name)

        if target is None:
            sender.send_message(self._msg("target_offline", target=target_name))
            del self._reply_targets[sender_name]
            return True

        self._send_pm(sender, target, message)
        return True

    def _send_pm(
        self, sender: CommandSender, target: Player, message: str
    ) -> None:
        s_name = sender.name if isinstance(sender, Player) else "Console"
        t_name = target.name

        sender.send_message(
            self._msg("message_to_sender", sender=s_name, target=t_name, message=message)
        )
        target.send_message(
            self._msg("message_to_target", sender=s_name, target=t_name, message=message)
        )

        if isinstance(sender, Player):
            self._reply_targets[s_name] = t_name
        self._reply_targets[t_name] = s_name

        if self._log_messages:
            self.logger.info(f"[PM] {s_name} -> {t_name}: {message}")

    def _find_exact(self, name: str) -> "Player | None":
        q = name.lower().strip()
        for p in self.server.online_players:
            if p.name.lower() == q:
                return p
        return None

    def _find_player(self, name: str) -> "Player | None":
        q = name.lower().strip()
        if not q:
            return None

        online = list(self.server.online_players)

        for p in online:
            if p.name.lower() == q:
                return p

        if not self._fuzzy_match:
            return None

        matches = [p for p in online if p.name.lower().startswith(q)]
        best = self._pick_best(matches)
        if best:
            return best
        if len(matches) > 1:
            return None

        matches = [p for p in online if q in p.name.lower()]
        best = self._pick_best(matches)
        if best:
            return best
        if len(matches) > 1:
            return None

        matches = [p for p in online if self._is_subseq(q, p.name.lower())]
        return self._pick_best(matches)

    @staticmethod
    def _pick_best(matches: list) -> "Player | None":
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        matches.sort(key=lambda p: len(p.name))
        shortest = len(matches[0].name)
        top = [p for p in matches if len(p.name) == shortest]
        return top[0] if len(top) == 1 else None

    @staticmethod
    def _is_subseq(query: str, target: str) -> bool:
        it = iter(target)
        return all(c in it for c in query)

    def _find_similar(self, name: str, limit: int = 5) -> list[str]:
        q = name.lower().strip()
        if not q:
            return []

        results: list[tuple[int, int, str]] = []
        for p in self.server.online_players:
            ln = p.name.lower()
            if ln.startswith(q):
                results.append((0, len(p.name), p.name))
            elif q in ln:
                results.append((1, len(p.name), p.name))
            elif self._is_subseq(q, ln):
                results.append((2, len(p.name), p.name))

        results.sort()
        return [n for _, _, n in results[:limit]]