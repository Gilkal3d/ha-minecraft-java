#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# MC Modded Server - supervisor (PID 1) do add-on
#
# Lê as opções do add-on (/data/options.json), exporta como variáveis de
# ambiente (a imagem itzg/minecraft-server faz todo o trabalho pesado:
# instalar Fabric/NeoForge/Forge, mods do Modrinth, Java correto etc.)
# e controla start/stop do servidor via a flag /data/.server_stopped.
# O painel web (panel.py) cria e apaga essa flag.
# ---------------------------------------------------------------------------
set -u

OPTIONS_FILE=/data/options.json
FLAG=/data/.server_stopped
RCON_FILE=/data/.rcon.json

opt() { jq -r "$1 // empty" "$OPTIONS_FILE" 2>/dev/null || true; }

# --- EULA (obrigatória pela Mojang) -----------------------------------------
if [ "$(opt .eula)" = "true" ]; then
  export EULA=TRUE
else
  export EULA=FALSE
  echo "[addon] AVISO: aceite a EULA nas opções do add-on (eula: true)."
fi

# --- Loader e versão --------------------------------------------------------
export TYPE="$(opt .loader)"
export VERSION="$(opt .minecraft_version)"
export MEMORY="$(opt .memory)"
[ "$(opt .aikar_flags)" = "true" ] && export USE_AIKAR_FLAGS=true

# --- Propriedades básicas do servidor ---------------------------------------
export MOTD="$(opt .motd)"
export MAX_PLAYERS="$(opt .max_players)"
export ONLINE_MODE="$(opt .online_mode)"
export DIFFICULTY="$(opt .difficulty)"
export GAME_MODE="$(opt .gamemode)"
export PVP="$(opt .pvp)"
export ALLOW_NETHER="$(opt .allow_nether)"
export ENABLE_COMMAND_BLOCK="$(opt .enable_command_block)"
export LEVEL_NAME="$(opt .level_name)"
export LEVEL_TYPE="$(opt .level_type)"
export VIEW_DISTANCE="$(opt .view_distance)"
export SERVER_PORT=25565

# --- Fuso horário -----------------------------------------------------------
if [ -f /etc/timezone ]; then
  export TZ="$(cat /etc/timezone)"
fi

# --- RCON (sempre habilitado; o painel web usa para comandos/stop gracioso) --
RCON_PASS="$(opt .rcon_password)"
if [ -z "$RCON_PASS" ]; then
  # reutiliza a senha gerada em execuções anteriores
  RCON_PASS="$(jq -r '.rcon_password // empty' "$RCON_FILE" 2>/dev/null || true)"
  if [ -z "$RCON_PASS" ]; then
    RCON_PASS="$(head -c 64 /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 24)"
  fi
fi
printf '{"rcon_password":"%s"}\n' "$RCON_PASS" > "$RCON_FILE"
export ENABLE_RCON=true
export RCON_PORT=25575
export RCON_PASSWORD="$RCON_PASS"

# --- Whitelist e ops (listas do add-on) -------------------------------------
WL="$(jq -r '.whitelist // [] | map(select(length>0)) | join(",")' "$OPTIONS_FILE" 2>/dev/null || true)"
[ -n "$WL" ] && export WHITELIST="$WL"

OPS="$(jq -r '.ops // [] | map(select(length>0)) | join(",")' "$OPTIONS_FILE" 2>/dev/null || true)"
[ -n "$OPS" ] && export OPS="$OPS"

# --- Mods do Modrinth (slugs nas opções do add-on) ---------------------------
MR="$(jq -r '.modrinth_projects // [] | map(select(length>0)) | join(",")' "$OPTIONS_FILE" 2>/dev/null || true)"
if [ -n "$MR" ]; then
  export MODRINTH_PROJECTS="$MR"
  export MODRINTH_DOWNLOAD_DEPENDENCIES=required
fi

# --- Painel web (reinicia sozinho se cair) -----------------------------------
( while true; do python3 /panel/panel.py; sleep 2; done ) &

# --- Loop do servidor --------------------------------------------------------
child=""
on_term() {
  echo "[addon] Encerrando..."
  [ -n "$child" ] && kill -TERM "$child" 2>/dev/null
}
trap on_term TERM INT

echo "[addon] Loader=${TYPE:-?} Versão=${VERSION:-?} Memória=${MEMORY:-?}"

while true; do
  if [ -f "$FLAG" ]; then
    sleep 3
    continue
  fi
  if [ "$EULA" != "TRUE" ]; then
    sleep 10
    continue
  fi
  /image/scripts/start &
  child=$!
  wait "$child"
  child=""
  sleep 3
done
