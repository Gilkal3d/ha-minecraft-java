#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# MC Modded Server - supervisor (PID 1) do add-on
#
# Fontes de configuração (nesta ordem de prioridade):
#   1. /data/custom.json  — aba "Configurações" do painel web (descrições em PT)
#   2. /data/options.json — formulário nativo do Home Assistant (padrões)
#
# As opções são RELIDAS a cada start do servidor, então alterações do painel
# são aplicadas ao reiniciar o servidor pelo próprio painel.
# ---------------------------------------------------------------------------
set -u

OPTIONS_FILE=/data/options.json
CUSTOM_FILE=/data/custom.json
FLAG=/data/.server_stopped
RCON_FILE=/data/.rcon.json

OPT="{}"
EULA=FALSE

# mescla: options do HA como base, custom.json do painel por cima
load_merged() {
  if [ -f "$CUSTOM_FILE" ] && [ -s "$CUSTOM_FILE" ]; then
    OPT="$(jq -s '.[0] * .[1]' "$OPTIONS_FILE" "$CUSTOM_FILE" 2>/dev/null || cat "$OPTIONS_FILE")"
  else
    OPT="$(cat "$OPTIONS_FILE" 2>/dev/null || echo '{}')"
  fi
}

get() { printf '%s' "$OPT" | jq -r "$1 // empty"; }

# aplica tudo como variáveis de ambiente (a imagem itzg faz o trabalho pesado:
# instalar Fabric/NeoForge/Forge, baixar mods do Modrinth, Java correto etc.)
apply_env() {
  load_merged

  # --- EULA (exigência da Mojang) ---
  if [ "$(get '.eula')" = "true" ]; then
    export EULA=TRUE
  else
    export EULA=FALSE
  fi

  # --- Loader, versão e memória ---
  export TYPE="$(get '.loader')"
  export VERSION="$(get '.minecraft_version')"
  export MEMORY="$(get '.memory')"
  if [ "$(get '.aikar_flags')" = "true" ]; then
    export USE_AIKAR_FLAGS=true
  else
    unset USE_AIKAR_FLAGS 2>/dev/null || true
  fi

  # --- Propriedades do servidor ---
  export MOTD="$(get '.motd')"
  export MAX_PLAYERS="$(get '.max_players')"
  export ONLINE_MODE="$(get '.online_mode')"
  export DIFFICULTY="$(get '.difficulty')"
  export GAME_MODE="$(get '.gamemode')"
  export PVP="$(get '.pvp')"
  export ALLOW_NETHER="$(get '.allow_nether')"
  export ENABLE_COMMAND_BLOCK="$(get '.enable_command_block')"
  export LEVEL_NAME="$(get '.level_name')"
  export LEVEL_TYPE="$(get '.level_type')"
  export VIEW_DISTANCE="$(get '.view_distance')"
  export SERVER_PORT=25565

  # --- Fuso horário ---
  if [ -f /etc/timezone ]; then
    export TZ="$(cat /etc/timezone)"
  fi

  # --- RCON (sempre ativo: o painel usa para comandos e parada graciosa) ---
  RCON_PASS="$(get '.rcon_password')"
  if [ -z "$RCON_PASS" ]; then
    RCON_PASS="$(jq -r '.rcon_password // empty' "$RCON_FILE" 2>/dev/null || true)"
    if [ -z "$RCON_PASS" ]; then
      RCON_PASS="$(head -c 64 /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 24)"
    fi
  fi
  if [ "$(jq -r '.rcon_password // empty' "$RCON_FILE" 2>/dev/null)" != "$RCON_PASS" ]; then
    printf '{"rcon_password":"%s"}\n' "$RCON_PASS" > "$RCON_FILE"
  fi
  export ENABLE_RCON=true
  export RCON_PORT=25575
  export RCON_PASSWORD="$RCON_PASS"

  # --- Listas: whitelist, ops e mods do Modrinth ---
  local WL OPS MR
  WL="$(printf '%s' "$OPT" | jq -r '(.whitelist // []) | map(select(length>0)) | join(",")')"
  [ -n "$WL" ] && export WHITELIST="$WL"

  OPS="$(printf '%s' "$OPT" | jq -r '(.ops // []) | map(select(length>0)) | join(",")')"
  [ -n "$OPS" ] && export OPS="$OPS"

  MR="$(printf '%s' "$OPT" | jq -r '(.modrinth_projects // []) | map(select(length>0)) | join(",")')"
  if [ -n "$MR" ]; then
    export MODRINTH_PROJECTS="$MR"
    export MODRINTH_DOWNLOAD_DEPENDENCIES=required
  fi
}

# --- Painel web (reinicia sozinho se cair) -----------------------------------
( while true; do python3 /panel/panel.py; sleep 2; done ) &

# --- Loop do servidor ---------------------------------------------------------
child=""
on_term() {
  echo "[addon] Encerrando..."
  [ -n "$child" ] && kill -TERM "$child" 2>/dev/null
}
trap on_term TERM INT

echo "[addon] MC Modded Server iniciando..."

while true; do
  apply_env
  if [ -f "$FLAG" ]; then
    sleep 3
    continue
  fi
  if [ "$EULA" != "TRUE" ]; then
    sleep 10
    continue
  fi
  echo "[addon] Loader=${TYPE:-?} Versão=${VERSION:-?} Memória=${MEMORY:-?}"
  /image/scripts/start &
  child=$!
  wait "$child"
  child=""
  sleep 3
done
