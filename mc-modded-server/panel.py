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
import re
import socket
import struct
import subprocess
import urllib.parse
import urllib.request
import uuid as uuidlib
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
PORT = 8099
MAX_UPLOAD = 300 * 1024 * 1024  # 300 MB

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
                "gamemode", "level_name", "level_type", "rcon_password"]
SET_KEYS_BOOL = ["eula", "aikar_flags", "online_mode", "pvp", "allow_nether",
                 "enable_command_block"]
SET_KEYS_INT = ["max_players", "view_distance"]
SET_KEYS_LIST = ["whitelist", "ops", "modrinth_projects"]
SET_KEYS = SET_KEYS_STR + SET_KEYS_BOOL + SET_KEYS_INT + SET_KEYS_LIST


def rcon_password():
    try:
        return json.loads(RCON_FILE.read_text(encoding="utf-8"))["rcon_password"]
    except Exception:
        return ""


def server_running():
    try:
        return subprocess.run(
            ["pgrep", "-f", "java"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


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
        self._json({
            "running": running,
            "stopped_by_user": FLAG_FILE.exists(),
            "eula": (opts.get("eula") is True),
            "loader": opts.get("loader"),
            "version": opts.get("minecraft_version"),
            "memory": opts.get("memory"),
            "players": players,
            "log_exists": LOG_FILE.exists(),
        })

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
        saved = []
        for raw_name, content in files:
            name = Path(raw_name).name
            if not name.endswith(".jar"):
                saved.append(f"{name}: ignorado (só .jar)")
                continue
            if len(content) > MAX_UPLOAD:
                saved.append(f"{name}: arquivo grande demais")
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
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[panel] ouvindo em :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
