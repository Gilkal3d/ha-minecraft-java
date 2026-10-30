#!/usr/bin/env python3
"""
MC Modded Server - painel web do add-on (backend).

Expõe uma API JSON em :8099 consumida pelo static/index.html.
Acesso autenticado via Ingress do Home Assistant (não há auth própria).

Funcionalidades:
  - status, start/stop/restart do servidor (via flag + RCON)
  - console ao vivo + envio de comandos (RCON)
  - editor do server.properties
  - gerenciador da whitelist (RCON se online, whitelist.json + API Mojang se offline)
  - gerenciador de mods (lista, upload, ativar/desativar, remover, instalar do Modrinth)
"""
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid as uuidlib
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path("/data")
MODS_DIR = DATA / "mods"
STATIC_DIR = Path("/panel/static")
OPTIONS_FILE = DATA / "options.json"    # formulário do Home Assistant (padrões)
CUSTOM_FILE = DATA / "custom.json"      # aba "Configurações" do painel (prioridade)
RCON_FILE = DATA / ".rcon.json"
FLAG_FILE = DATA / ".server_stopped"
LOG_FILE = DATA / "logs" / "latest.log"
PROPS_FILE = DATA / "server.properties"
WHITELIST_FILE = DATA / "whitelist.json"
BACKUP_DIR = DATA / "backups"
STATE_FILE = DATA / ".panel_state.json"  # marcas dos agendamentos (uma por dia)
HA_URL = "http://supervisor/core/api"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
# O Supervisor do HA injeta INGRESS_PORT quando o add-on usa ingress;
# 8099 é apenas o fallback (ex.: execução manual para depuração)
PORT = int(os.environ.get("INGRESS_PORT") or 8099)
MAX_UPLOAD = 2 * 1024 * 1024 * 1024  # 2 GB (streaming em blocos, não usa RAM)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ---------------------------------------------------------------- utilidades
def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def options():
    """Configuração efetiva: options do HA como base, custom.json por cima."""
    merged = _read_json(OPTIONS_FILE)
    merged.update(_read_json(CUSTOM_FILE))
    return merged


# Chaves editáveis pela aba "Configurações" do painel
SET_KEYS_STR = ["loader", "minecraft_version", "memory", "motd", "difficulty",
                "gamemode", "level_name", "level_type", "rcon_password",
                "daily_restart", "daily_backup", "notify_service"]
SET_KEYS_BOOL = ["eula", "aikar_flags", "online_mode", "pvp", "allow_nether",
                 "enable_command_block", "notifications"]
SET_KEYS_INT = ["max_players", "view_distance", "backup_keep"]
SET_KEYS_LIST = ["whitelist", "ops", "modrinth_projects"]
SET_KEYS = SET_KEYS_STR + SET_KEYS_BOOL + SET_KEYS_INT + SET_KEYS_LIST


def rcon_password():
    try:
        return json.loads(RCON_FILE.read_text(encoding="utf-8"))["rcon_password"]
    except Exception:
        return ""


# Estado simples (agendamentos já disparados hoje, aviso de caiu/religou)
def _state():
    return _read_json(STATE_FILE)


def _state_set(**kv):
    st = _state()
    st.update(kv)
    write_json_file(STATE_FILE, st)


def _java_pids():
    try:
        out = subprocess.run(
            ["pgrep", "-f", "java"], capture_output=True, text=True, timeout=5).stdout
        return [p for p in out.split() if p.strip().isdigit()]
    except Exception:
        return []


def server_running():
    return bool(_java_pids())


def _proc_rss_mb(pid):
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


def _proc_uptime_s(pid):
    try:
        return max(0.0, time.time() - Path(f"/proc/{pid}").stat().st_ctime)
    except Exception:
        return 0.0


def server_stats():
    """(ram_mb, uptime_s) do processo Java mais antigo, ou (0, 0)."""
    best_mb, best_up = 0.0, 0.0
    for pid in _java_pids():
        up = _proc_uptime_s(pid)
        if up > best_up:
            best_up = up
            best_mb = _proc_rss_mb(pid)
    return best_mb, best_up


def _progress_from_log(text):
    """Estima o progresso do primeiro start (0-100) e uma fase legível."""
    low = text.lower()
    steps = [
        ("baixando instalador", 10), ("downloading installer", 10),
        ("baixando", 20), ("downloading", 20),
        ("instalando", 45), ("installing", 45),
        ("loading mods", 60), ("carregando mods", 60),
        ("loading minecraft", 65), ("preparing start region", 75),
        ("preparando a regiao", 75), ("preparing spawn area", 75),
        ("generating spawn", 75), ("done (", 100),
    ]
    best = 0
    for needle, pct in steps:
        if needle in low:
            best = max(best, pct)
    if best >= 100:
        return 100, "Pronto"
    for label, pct in steps:
        if pct == best and best > 0:
            return best, label.replace("done (", "").strip().capitalize()
    return 0, "Aguardando o servidor"


def log_progress():
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0, "Aguardando o servidor"
    return _progress_from_log(text)


# ---------------------------------------------------------------- backups
def _zip_add(zf, root, arc=""):
    """Adiciona um arquivo ou diretório ao zip com caminhos relativos corretos,
    pulando pastas pesadas/irrelevantes (logs, cache, libraries...)."""
    skip_dirs = {"logs", "cache", "crash-reports", "backups", "libraries",
                 "versions", "assets", "neoform", "runs", ".cache"}
    skip_files = {"session.lock", "launcher_profile.json"}

    if root.is_file():
        if root.name not in skip_files:
            zf.write(root, arc or root.name)
        return
    for child in sorted(root.iterdir()):
        child_arc = f"{arc}/{child.name}" if arc else child.name
        if child.is_dir():
            if child.name.lower() not in skip_dirs and not child.name.startswith("."):
                _zip_add(zf, child, child_arc)
        elif child.name not in skip_files:
            zf.write(child, child_arc)


def make_backup(tag="manual"):
    """Cria um .zip com mundo + configs essenciais. Retorna (nome, tamanho)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"backup-{stamp}-{tag}.zip"
    dest = BACKUP_DIR / name
    level = (options().get("level_name") or "world").strip() or "world"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in (level, "server.properties", "whitelist.json", "ops.json",
                      "banned-players.json", "banned-ips.json", "eula.txt",
                      "config", "defaultconfigs", "mods"):
            p = DATA / entry
            if p.exists():
                _zip_add(zf, p, arc=entry)
    prune_backups()
    return name, dest.stat().st_size


def prune_backups():
    try:
        keep = int(options().get("backup_keep") or 7)
    except Exception:
        keep = 7
    keep = max(1, keep)
    zips = sorted(BACKUP_DIR.glob("backup-*.zip"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for old in zips[keep:]:
        try:
            old.unlink()
        except Exception:
            pass


def _safe_name(name):
    return bool(re.fullmatch(r"backup-[0-9]{8}-[0-9]{6}-[a-z]+\.zip", name or ""))


# ---------------------------------------------------------------- HA bridge
def _ha_post(path, payload):
    if not SUPERVISOR_TOKEN:
        return False
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        HA_URL + path, data=data, method="POST",
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except Exception:
        return False


def _notify(message, title="MC Modded Server"):
    """Envia notificação ao HA. Serviço vazio => persistent_notification."""
    if not (options().get("notifications") is True):
        return
    svc = (options().get("notify_service") or "").strip()
    if svc:
        if svc.startswith("notify."):
            svc = svc[len("notify."):]
        _ha_post(f"/services/notify/{svc}", {"title": title, "message": message})
    else:
        _ha_post("/services/persistent_notification/create",
                 {"title": title, "message": message})


def _publish_sensors():
    running = server_running()
    opts = options()
    ram_mb, uptime_s = server_stats()
    players = None
    if running:
        try:
            out = rcon_cmd("list")
            m = re.search(r"(\d+).*?of.*?(\d+)", out)
            names = [n.strip() for n in out.split(":", 1)[1].split(",")] \
                if ":" in out else []
            players = {"online": int(m.group(1)) if m else len(names),
                       "max": int(m.group(2)) if m else 0,
                       "names": [n for n in names if n]}
        except Exception:
            pass

    attrs = {"friendly_name": "Servidor MC (status)", "icon": "mdi:cube-outline",
             "attribution": "Add-on MC Modded Server",
             "loader": opts.get("loader"), "version": opts.get("minecraft_version")}
    _ha_post("/states/sensor.mc_server_status",
             {"state": "online" if running else "offline",
              "attributes": {**attrs, "uptime_s": int(uptime_s),
                             "ram_mb": round(ram_mb, 1)}})

    online = players["online"] if players else 0
    names = ", ".join(players["names"]) if players and players["names"] else ""
    _ha_post("/states/sensor.mc_players_online",
             {"state": online,
              "attributes": {"friendly_name": "MC - Jogadores online",
                             "icon": "mdi:account-group",
                             "names": names,
                             "max": players["max"] if players else 0}})

    _ha_post("/states/sensor.mc_ram",
             {"state": round(ram_mb, 1),
              "attributes": {"friendly_name": "MC - RAM do servidor",
                             "unit_of_measurement": "MB", "icon": "mdi:memory"}})


def _ha_loop():
    """Thread: publica sensores e detecta entrada/saída de jogadores."""
    prev_names = set()
    prev_running = False
    while True:
        try:
            running = server_running()
            names = set()
            if running:
                try:
                    out = rcon_cmd("list")
                    if ":" in out:
                        names = {n.strip() for n in out.split(":", 1)[1].split(",")} - {""}
                except Exception:
                    pass
            _publish_sensors()
            # notificações de entrada/saída
            if names - prev_names and prev_running:
                for n in sorted(names - prev_names):
                    _notify(f"👤 {n} entrou no servidor.")
            if prev_names - names and running:
                for n in sorted(prev_names - names):
                    _notify(f"👋 {n} saiu do servidor.")
            # caiu/religou inesperadamente (sem flag do usuário)
            if prev_running and not running and not FLAG_FILE.exists():
                _notify("⚠️ O servidor saiu do ar. O add-on vai tentar religar.",
                        "MC Server - alerta")
            prev_names, prev_running = names, running
        except Exception:
            pass
        time.sleep(20)


def _sched_loop():
    """Thread: restart diário (via RCON stop, sem flag => auto-restart) e backup."""
    while True:
        try:
            opts = options()
            today = time.strftime("%Y-%m-%d")
            now_hm = time.strftime("%H:%M")
            st = _state()

            dr = (opts.get("daily_restart") or "").strip()
            if dr and now_hm == dr and st.get("restart_day") != today:
                if server_running():
                    try:
                        rcon_cmd("stop")  # sem FLAG => o supervisor religa sozinho
                    except Exception:
                        pass
                _state_set(restart_day=today)
                _notify(f"🔄 Restart diário agendado ({dr}) iniciado.")

            db = (opts.get("daily_backup") or "").strip()
            if db and now_hm == db and st.get("backup_day") != today:
                name, size = make_backup("auto")
                _state_set(backup_day=today)
                _notify(f"💾 Backup automático criado: {name} ({size // 1048576} MB).")
        except Exception:
            pass
        time.sleep(25)


def tail_log(lines=250):
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out = [ANSI_RE.sub("", l) for l in text.splitlines()[-lines:]]
    return out


# ---------------------------------------------------------------- RCON
class RconError(Exception):
    pass


class Rcon:
    """Cliente RCON mínimo (protocolo Source)."""

    def __init__(self, password=None, host="127.0.0.1", port=25575, timeout=4):
        self.password = password or rcon_password()
        self.req_id = 0
        self.sock = socket.create_connection((host, port), timeout)
        self.sock.settimeout(timeout)
        self._send(3, self.password)  # AUTH
        rid, _ = self._recv()
        if rid == -1:
            raise RconError("senha RCON inválida")

    def _send(self, ptype, body):
        self.req_id += 1
        payload = struct.pack("<ii", self.req_id, ptype) + body.encode("utf-8", "replace") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self.req_id

    def _recv(self):
        header = self._read(4)
        (length,) = struct.unpack("<i", header)
        data = self._read(length)
        rid, ptype = struct.unpack("<ii", data[:8])
        return rid, data[8:-2].decode("utf-8", "replace")

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("conexão RCON fechada")
            buf += chunk
        return buf

    def command(self, cmd):
        self._send(2, cmd)
        parts = []
        try:
            self.sock.settimeout(0.8)
            while True:
                _, body = self._recv()
                parts.append(body)
        except (socket.timeout, RconError):
            pass
        finally:
            self.sock.settimeout(4)
        return "\n".join(p for p in parts if p).strip()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def rcon_cmd(cmd):
    r = Rcon()
    try:
        return r.command(cmd)
    finally:
        r.close()


# ---------------------------------------------------------------- UUID
def mojang_uuid(name, online_mode=True):
    """UUID do jogador: Mojang se online_mode, senão UUID offline (vanilla)."""
    if online_mode:
        try:
            req = urllib.request.Request(
                f"https://api.mojang.com/users/profiles/minecraft/{urllib.parse.quote(name)}",
                headers={"User-Agent": "HA-MC-Modded-Addon"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.load(resp)["id"]
            return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
        except Exception:
            pass
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_DNS, f"OfflinePlayer:{name}"))


def read_json_file(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_file(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- Modrinth
LOADERS_MAP = {
    "FABRIC": ["fabric"],
    "FORGE": ["forge"],
    "NEOFORGE": ["neoforge"],
    "PAPER": ["paper", "purpur", "spigot", "bukkit", "folia"],
    "PURPUR": ["purpur", "paper", "spigot", "bukkit"],
    "VANILLA": None,
}


def modrinth_get(path):
    url = "https://api.modrinth.com/v2" + path
    req = urllib.request.Request(url, headers={"User-Agent": "HA-MC-Modded-Addon"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def modrinth_install(slug):
    opts = options()
    loader = (opts.get("loader") or "FABRIC").upper()
    mc_version = opts.get("minecraft_version") or ""

    project = modrinth_get(f"/project/{urllib.parse.quote(slug)}")
    ptype = project.get("project_type")
    if ptype not in ("mod", "plugin", "datapack"):
        raise RuntimeError(f"'{slug}' não é um mod/plugin/datapack no Modrinth")

    query = {}
    if mc_version and mc_version.upper() != "LATEST":
        query["game_versions"] = json.dumps([mc_version])
    loaders = LOADERS_MAP.get(loader)
    if loaders:
        query["loaders"] = json.dumps(loaders)

    versions = modrinth_get(
        f"/project/{urllib.parse.quote(slug)}/version?" + urllib.parse.urlencode(query))
    if not versions:
        raise RuntimeError(
            f"Nenhuma versão de '{slug}' compatível com {loader} {mc_version}")

    v = versions[0]
    files = v.get("files") or []
    f = next((x for x in files if x.get("primary")), files[0])
    MODS_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODS_DIR / Path(f["filename"]).name
    req = urllib.request.Request(f["url"], headers={"User-Agent": "HA-MC-Modded-Addon"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return f"{f['filename']} ({v.get('version_number', '?')})"


# ---------------------------------------------------------------- parser multipart
def parse_multipart(body, content_type):
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        return None
    boundary = b"--" + m.group(1).encode()
    files = []
    for part in body.split(boundary):
        if b"\r\n\r\n" not in part:
            continue
        header, content = part.split(b"\r\n\r\n", 1)
        content = content.rstrip(b"\r\n")
        fm = re.search(rb'filename="([^"]*)"', header)
        if not fm or not fm.group(1):
            continue
        files.append((fm.group(1).decode("utf-8", "replace"), content))
    return files


def _stream_multipart_to_file(rfile, total_len, boundary, out_fh, max_bytes):
    """Lê um multipart/form-data em blocos, gravando o conteúdo do primeiro arquivo
    direto no disco (sem segurar tudo na memória). Retorna o filename, ou None se
    não achar nenhum arquivo. Levanta RuntimeError se exceder max_bytes.

    O payload tem a forma:
      --boundary\\r\\n
      Content-Disposition: form-data; name="file"; filename="x.zip"\\r\\n
      [headers]\\r\\n
      \\r\\n
      <conteúdo do arquivo>
      \\r\\n--boundary--\\r\\n
    """
    chunk = 64 * 1024
    remaining = total_len
    # 1) acumula e localiza o fim dos cabeçalhos da primeira parte (\r\n\r\n)
    buf = b""
    sep = -1
    while remaining > 0 and sep == -1:
        data = rfile.read(min(chunk, remaining))
        if not data:
            break
        remaining -= len(data)
        buf += data
        sep = buf.find(b"\r\n\r\n")
    if sep == -1:
        return None
    fm = re.search(rb'filename="([^"]*)"', buf[:sep])
    if not fm or not fm.group(1):
        return None
    filename = fm.group(1).decode("utf-8", "replace")
    # 2) o que já veio depois do \r\n\r\n é início do conteúdo do arquivo
    pending = buf[sep + 4:]
    written = 0

    def _flush(data):
        nonlocal written
        if not data:
            return
        written += len(data)
        if written > max_bytes:
            raise RuntimeError(f"arquivo grande demais (máx. {MAX_UPLOAD // (1024 * 1024)} MB)")
        out_fh.write(data)

    # procura o boundary de fechamento no conteúdo já bufferizado
    idx = pending.find(boundary)
    if idx != -1:
        _flush(pending[:idx])
        return filename
    _flush(pending)
    # 3) continua lendo em blocos até achar o fim do arquivo (boundary)
    #    usa uma janela para não perder o boundary que cruza a borda entre blocos
    window = b""
    while remaining > 0:
        data = rfile.read(min(chunk, remaining))
        if not data:
            break
        remaining -= len(data)
        scan = window + data
        idx = scan.find(boundary)
        if idx != -1:
            _flush(scan[:idx])
            return filename
        # mantém os últimos bytes (menor que o boundary) para o próximo scan
        keep = len(boundary) - 1
        _flush(scan[:-keep] if keep > 0 else scan)
        window = scan[-keep:] if keep > 0 else b""
    return filename


# ---------------------------------------------------------------- handlers
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass  # silencia access log

    # -------- infra HTTP --------
    def _json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "close_connection", False):
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, msg, status=400):
        self._json({"ok": False, "error": str(msg)}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _path(self):
        parsed = urllib.parse.urlparse(self.path)
        path = re.sub(r"^/api/hassio_ingress/[0-9a-f-]+", "", parsed.path) or "/"
        return path, urllib.parse.parse_qs(parsed.query)

    # -------- rotas GET --------
    def do_GET(self):
        path, query = self._path()
        try:
            if path == "/api/status":
                return self.api_status()
            if path == "/api/players":
                return self.api_players()
            if path == "/api/backups":
                return self.api_backups()
            if path == "/api/backups/download":
                return self.api_backup_download(query)
            if path == "/api/log/download":
                return self.api_log_download()
            if path == "/api/settings":
                return self._json({
                    "settings": options(),
                    "has_custom": CUSTOM_FILE.exists() and CUSTOM_FILE.stat().st_size > 2,
                    "ha_defaults": _read_json(OPTIONS_FILE),
                })
            if path == "/api/console":
                n = int((query.get("lines") or ["250"])[0])
                return self._json({"lines": tail_log(n)})
            if path == "/api/properties":
                text = PROPS_FILE.read_text(encoding="utf-8", errors="replace") \
                    if PROPS_FILE.exists() else ""
                return self._json({"content": text, "running": server_running()})
            if path == "/api/whitelist":
                return self.api_whitelist()
            if path == "/api/mods":
                return self.api_mods()
            # SPA: qualquer outra rota serve o index
            return self._static("index.html", "text/html")
        except Exception as e:  # noqa: BLE001
            return self._error(e, 500)

    # -------- rotas POST --------
    def do_POST(self):
        path, query = self._path()
        try:
            if path == "/api/settings":
                if "reset" in query:
                    CUSTOM_FILE.unlink(missing_ok=True)
                    return self._json({"ok": True, "settings": options()})
                data = json.loads(self._body() or b"{}")
                incoming = data.get("settings") or {}
                clean = {}
                for k in SET_KEYS_STR:
                    if k in incoming:
                        clean[k] = str(incoming[k] or "")
                for k in SET_KEYS_BOOL:
                    if k in incoming:
                        clean[k] = bool(incoming[k])
                for k in SET_KEYS_INT:
                    if k in incoming:
                        try:
                            clean[k] = int(incoming[k])
                        except (TypeError, ValueError):
                            pass
                for k in SET_KEYS_LIST:
                    if k in incoming:
                        vals = incoming[k]
                        if isinstance(vals, str):
                            vals = [v.strip() for v in vals.splitlines()]
                        clean[k] = [str(v).strip() for v in vals if str(v).strip()]
                if not clean:
                    return self._error("nenhuma configuração enviada")
                CUSTOM_FILE.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
                restarted = False
                if query.get("restart"):
                    # o supervisor relê as opções a cada start
                    if server_running():
                        try:
                            rcon_cmd("stop")
                        except Exception:
                            pass
                    FLAG_FILE.unlink(missing_ok=True)
                    restarted = True
                return self._json({"ok": True, "settings": options(),
                                   "restarted": restarted})
            if path == "/api/server/start":
                FLAG_FILE.unlink(missing_ok=True)
                return self._json({"ok": True})
            if path == "/api/server/stop":
                if server_running():
                    try:
                        rcon_cmd("stop")  # parada graciosa
                    except Exception:
                        pass
                FLAG_FILE.touch()
                return self._json({"ok": True})
            if path == "/api/server/restart":
                if server_running():
                    try:
                        rcon_cmd("stop")
                    except Exception:
                        pass
                FLAG_FILE.touch()
                FLAG_FILE.unlink(missing_ok=True)  # volta a rodar após parar
                return self._json({"ok": True})
            if path == "/api/console":
                data = json.loads(self._body() or b"{}")
                cmd = (data.get("cmd") or "").strip()
                if not cmd:
                    return self._error("comando vazio")
                out = rcon_cmd(cmd)
                return self._json({"ok": True, "output": out})
            if path == "/api/properties":
                if server_running():
                    return self._error("Pare o servidor antes de salvar server.properties")
                data = json.loads(self._body() or b"{}")
                PROPS_FILE.write_text(data.get("content") or "", encoding="utf-8")
                if query.get("restart"):
                    FLAG_FILE.unlink(missing_ok=True)
                return self._json({"ok": True})
            if path == "/api/whitelist/add":
                return self.api_whitelist_add(query)
            if path == "/api/whitelist/remove":
                return self.api_whitelist_remove(query)
            if path == "/api/players/action":
                return self.api_player_action(query)
            if path == "/api/backups/create":
                return self.api_backup_create()
            if path == "/api/backups/upload":
                return self.api_backup_upload()
            if path == "/api/backups/restore":
                return self.api_backup_restore(query)
            if path == "/api/backups/delete":
                return self.api_backup_delete(query)
            if path == "/api/mods/upload":
                return self.api_mods_upload()
            if path == "/api/mods/toggle":
                return self.api_mods_toggle(query)
            if path == "/api/mods/delete":
                return self.api_mods_delete(query)
            if path == "/api/modrinth":
                data = json.loads(self._body() or b"{}")
                slug = (data.get("slug") or "").strip()
                if not slug:
                    return self._error("informe o slug do projeto")
                name = modrinth_install(slug)
                return self._json({"ok": True, "installed": name})
            return self._error("rota desconhecida", 404)
        except Exception as e:  # noqa: BLE001
            return self._error(e, 500)

    # -------- status --------
    def api_status(self):
        opts = options()
        running = server_running()
        players = None
        if running:
            try:
                out = rcon_cmd("list")
                m = re.search(r"(\d+).*?of.*?(\d+)", out)
                names = out.split(":", 1)[1].strip() if ":" in out else ""
                if m:
                    players = {"online": int(m.group(1)), "max": int(m.group(2)),
                               "names": names}
            except Exception:
                pass
        ram_mb, uptime_s = server_stats()
        pct, phase = log_progress()
        self._json({
            "running": running,
            "stopped_by_user": FLAG_FILE.exists(),
            "eula": (opts.get("eula") is True),
            "loader": opts.get("loader"),
            "version": opts.get("minecraft_version"),
            "memory": opts.get("memory"),
            "motd": opts.get("motd"),
            "players": players,
            "log_exists": LOG_FILE.exists(),
            "ram_mb": round(ram_mb, 1),
            "uptime_s": int(uptime_s),
            "startup": {"percent": pct, "phase": phase},
        })

    # -------- jogadores --------
    def api_players(self):
        opts = options()
        running = server_running()
        online = []
        if running:
            try:
                out = rcon_cmd("list")
                if ":" in out:
                    online = [n.strip() for n in out.split(":", 1)[1].split(",") if n.strip()]
            except Exception:
                pass
        # sessão atual a partir do log (entrou/saiu)
        events = []
        try:
            for line in LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                clean = ANSI_RE.sub("", line)
                m = re.match(r"\[.*?\]:?\s*([A-Za-z0-9_]{1,16}) (joined the game|left the game)",
                             clean.strip())
                if m:
                    events.append({"name": m.group(1),
                                   "type": "join" if "joined" in m.group(2) else "leave"})
        except Exception:
            pass
        # mantém apenas quem está online agora
        online_set = {n.lower() for n in online}
        history = [e for e in events
                   if (e["type"] == "leave") or (e["name"].lower() in online_set)]
        self._json({
            "running": running,
            "online": online,
            "max": int(opts.get("max_players") or 0),
            "ops": [o.strip() for o in (opts.get("ops") or [])],
            "history": history[-40:],
        })

    def api_player_action(self, query):
        name = (query.get("name") or [""])[0].strip()
        action = (query.get("action") or [""])[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
            return self._error("nome de jogador inválido")
        if not server_running():
            return self._error("servidor parado — ação disponível só com o servidor online")
        cmds = {
            "kick": f"kick {name}",
            "ban": f"ban {name}",
            "pardon": f"pardon {name}",
            "op": f"op {name}",
            "deop": f"deop {name}",
            "wl_add": f"whitelist add {name}",
            "wl_remove": f"whitelist remove {name}",
        }
        if action not in cmds:
            return self._error("ação desconhecida")
        out = rcon_cmd(cmds[action])
        self._json({"ok": True, "output": out})

    # -------- backups --------
    def api_backups(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        items = []
        for p in sorted(BACKUP_DIR.glob("backup-*.zip"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            st = p.stat()
            items.append({"name": p.name, "size": st.st_size,
                          "created": int(st.st_mtime)})
        self._json({"backups": items,
                    "keep": int(options().get("backup_keep") or 7),
                    "running": server_running(),
                    "max_upload": MAX_UPLOAD})

    def api_backup_create(self):
        if server_running():
            try:
                rcon_cmd("save-all flush")  # garante mundo consistente
            except Exception:
                pass
        name, size = make_backup("manual")
        threading.Timer(1.0, lambda: _notify(
            f"💾 Backup manual criado: {name} ({size // 1048576} MB).")).start()
        self._json({"ok": True, "name": name, "size": size})

    def api_backup_upload(self):
        """Recebe um .zip de backup enviado do computador (streaming em blocos)."""
        ctype = self.headers.get("Content-Type", "")
        boundary = re.search(r'boundary="?([^";]+)"?', ctype)
        if not boundary:
            return self._error("requisição sem arquivo (boundary ausente)")
        boundary = b"--" + boundary.group(1).encode()
        # NOTE: o Content-Length inclui o multipart todo (cabeçalhos + boundary),
        # então não vale usá-lo para limitar o tamanho do arquivo. O limite é
        # aplicado sobre os bytes realmente escritos, dentro do streaming.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        new_name = f"backup-{stamp}-upload.zip"
        dest = BACKUP_DIR / new_name
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            with open(dest, "wb") as fh:
                name = _stream_multipart_to_file(
                    self.rfile, length, boundary, fh, max_bytes=MAX_UPLOAD)
        except RuntimeError as e:
            dest.unlink(missing_ok=True)
            self.close_connection = True
            return self._error(str(e))
        except Exception:
            dest.unlink(missing_ok=True)
            # marca a conexão para fechar: garante que uma nova requisição
            # (retry do usuário) nunca leia dados órfãos desta
            self.close_connection = True
            raise
        if not name or not name.lower().endswith(".zip"):
            dest.unlink(missing_ok=True)
            return self._error("só é permitido enviar arquivos .zip")
        # garante que é um zip válido (não corrompido/truncado)
        if not zipfile.is_zipfile(dest):
            dest.unlink(missing_ok=True)
            return self._error("o arquivo enviado não é um .zip válido")
        prune_backups()
        return self._json({"ok": True, "name": new_name, "size": dest.stat().st_size})

    def api_backup_restore(self, query):
        name = (query.get("name") or [""])[0]
        if not _safe_name(name):
            return self._error("nome de backup inválido")
        if server_running():
            return self._error("Pare o servidor antes de restaurar um backup")
        src = BACKUP_DIR / name
        if not src.exists():
            return self._error("backup não encontrado", 404)
        # segurança: faz um backup do estado atual antes de restaurar
        try:
            make_backup("pre-restore")
        except Exception:
            pass
        level = (options().get("level_name") or "world").strip() or "world"
        with zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                # evita path traversal
                if info.filename.startswith("/") or ".." in info.filename.split("/"):
                    return self._error("backup corrompido (caminho inválido)")
            zf.extractall(DATA)
        self._json({"ok": True, "message": "Backup restaurado. Inicie o servidor.",
                    "level": level})

    def api_backup_delete(self, query):
        name = (query.get("name") or [""])[0]
        if not _safe_name(name):
            return self._error("nome de backup inválido")
        p = BACKUP_DIR / name
        if not p.exists():
            return self._error("backup não encontrado", 404)
        p.unlink()
        self._json({"ok": True})

    def api_backup_download(self, query):
        name = (query.get("name") or [""])[0]
        if not _safe_name(name):
            return self._error("nome de backup inválido")
        p = BACKUP_DIR / name
        if not p.exists():
            return self._error("backup não encontrado", 404)
        self._file(p, "application/zip", name)

    def api_log_download(self):
        if not LOG_FILE.exists():
            return self._error("log não encontrado", 404)
        self._file(LOG_FILE, "text/plain", "latest.log")

    # -------- whitelist --------
    def api_whitelist(self):
        entries = read_json_file(WHITELIST_FILE, [])
        opts = options()
        enabled = "whitelist=true" in (
            PROPS_FILE.read_text(encoding="utf-8", errors="replace").lower()
            if PROPS_FILE.exists() else "")
        self._json({
            "enabled": enabled,
            "online_mode": bool(opts.get("online_mode", True)),
            "entries": [{"name": e.get("name", "?"), "uuid": e.get("uuid", "?")}
                        for e in entries],
        })

    def api_whitelist_add(self, query):
        name = (query.get("name") or [""])[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
            return self._error("nome de jogador inválido")
        if server_running():
            out = rcon_cmd(f"whitelist add {name}")
            return self._json({"ok": True, "output": out})
        entries = read_json_file(WHITELIST_FILE, [])
        if not any(e.get("name", "").lower() == name.lower() for e in entries):
            entries.append({"uuid": mojang_uuid(name, options().get("online_mode", True)),
                            "name": name})
            write_json_file(WHITELIST_FILE, entries)
        return self._json({"ok": True})

    def api_whitelist_remove(self, query):
        name = (query.get("name") or [""])[0].strip()
        if not name:
            return self._error("nome ausente")
        if server_running():
            out = rcon_cmd(f"whitelist remove {name}")
            return self._json({"ok": True, "output": out})
        entries = read_json_file(WHITELIST_FILE, [])
        entries = [e for e in entries if e.get("name", "").lower() != name.lower()]
        write_json_file(WHITELIST_FILE, entries)
        return self._json({"ok": True})

    # -------- mods --------
    def api_mods(self):
        MODS_DIR.mkdir(parents=True, exist_ok=True)
        mods = []
        for p in sorted(MODS_DIR.iterdir()):
            if not p.is_file():
                continue
            if p.suffix == ".jar":
                mods.append({"name": p.name, "size": p.stat().st_size, "enabled": True})
            elif p.name.endswith(".jar.disabled"):
                mods.append({"name": p.name[:-len(".disabled")],
                             "size": p.stat().st_size, "enabled": False})
        opts = options()
        self._json({
            "mods": mods,
            "loader": opts.get("loader"),
            "version": opts.get("minecraft_version"),
            "running": server_running(),
        })

    def api_mods_upload(self):
        ctype = self.headers.get("Content-Type", "")
        files = parse_multipart(self._body(), ctype)
        if not files:
            return self._error("nenhum arquivo recebido")
        max_mod = 300 * 1024 * 1024  # mods são lidos inteiros na RAM: limite menor
        saved = []
        for raw_name, content in files:
            name = Path(raw_name).name
            if not name.endswith(".jar"):
                saved.append(f"{name}: ignorado (só .jar)")
                continue
            if len(content) > max_mod:
                saved.append(f"{name}: arquivo grande demais (máx. 300 MB)")
                continue
            (MODS_DIR / name).write_bytes(content)
            saved.append(f"{name}: enviado")
        return self._json({"ok": True, "results": saved})

    def api_mods_toggle(self, query):
        name = Path((query.get("name") or [""])[0]).name
        src = MODS_DIR / name
        disabled = MODS_DIR / (name + ".disabled")
        if src.exists():
            src.rename(disabled)
        elif disabled.exists():
            disabled.rename(MODS_DIR / name)
        else:
            return self._error("mod não encontrado", 404)
        return self._json({"ok": True})

    def api_mods_delete(self, query):
        name = Path((query.get("name") or [""])[0]).name
        for p in (MODS_DIR / name, MODS_DIR / (name + ".disabled")):
            if p.exists():
                p.unlink()
                return self._json({"ok": True})
        return self._error("mod não encontrado", 404)

    # -------- download de arquivos --------
    def _file(self, path, ctype, download_name):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{re.sub(r"[^A-Za-z0-9._-]", "_", download_name)}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -------- arquivos estáticos --------
    def _static(self, rel, ctype):
        path = (STATIC_DIR / rel).resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.is_file():
            return self._error("não encontrado", 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main():
    MODS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # threads de sensores/notificações e agendamentos (fire-and-forget)
    for fn in (_ha_loop, _sched_loop):
        t = threading.Thread(target=fn, daemon=True)
        t.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[panel] ouvindo em :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
