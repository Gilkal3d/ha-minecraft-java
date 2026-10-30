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

  # Versão fixa do carregador (vazio = recomendada/mais recente do itzg).
  # Serve para casar com a versão instalada no client do jogador.
  LOADER_V="$(get '.loader_version')"
  if [ -n "$LOADER_V" ]; then
    case "$TYPE" in
      NEOFORGE) export NEOFORGE_VERSION="$LOADER_V" ;;
      FORGE)    export FORGE_VERSION="$LOADER_V" ;;
      FABRIC)   export FABRIC_LOADER_VERSION="$LOADER_V" ;;
    esac
  fi
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
( while true; do
    python3 /panel/panel.py
    echo "[addon] Painel web caiu; reiniciando em 2s..." >&2
    sleep 2
  done ) &

# sleep interrompível: com o `wait`, o bash executa o trap assim que chega o
# SIGTERM (um `sleep` simples só deixaria o trap rodar ao terminar)
nap() { sleep "$1" & wait $! 2>/dev/null; }

# O painel web roda como root e o servidor roda como 'mc' (uid 1000). Arquivos
# de jogo criados/alterados pelo painel precisam pertencer ao 'mc', senão o init
# do itzg falha com "Permission denied" (ex.: /data/whitelist.json) e o servidor
# entra em loop de reinício com código 1.
fix_data_ownership() {
  chown mc:mc /data 2>/dev/null || true
  local f d
  for f in whitelist.json ops.json banned-players.json banned-ips.json \
           usercache.json eula.txt server.properties; do
    [ -e "/data/$f" ] && chown mc:mc "/data/$f" 2>/dev/null
  done
  for d in "${LEVEL_NAME:-world}" logs cache crash-reports libraries versions \
           assets config defaultconfigs mods neoform runs; do
    [ -d "/data/$d" ] && chown mc:mc "/data/$d" 2>/dev/null
  done
}

# --- Loop do servidor ---------------------------------------------------------
# Flags de estado:
#   stopping = 1  -> o add-on recebeu SIGTERM (parar/reiniciar pelo HA) e deve
#                    encerrar o container DEPOIS de parar o servidor graciosamente
stopping=0
warned_eula=false
child=""

on_term() {
  echo "[addon] Encerrando o add-on (parando o servidor graciosamente)..."
  stopping=1
  [ -n "$child" ] && kill -TERM "$child" 2>/dev/null
}
trap on_term TERM INT

echo "[addon] MC Modded Server iniciando..."

while true; do
  apply_env

  [ "$stopping" = "1" ] && exit 0

  if [ -f "$FLAG" ]; then
    nap 3
    continue
  fi

  if [ "$EULA" != "TRUE" ]; then
    if [ "$warned_eula" != "true" ]; then
      echo "[addon] EULA ainda não aceita. Abra as configurações do add-on e defina 'eula: true'."
      warned_eula=true
    fi
    nap 10
    continue
  fi
  warned_eula=false

  # Nunca inicia um segundo servidor enquanto o anterior ainda está encerrando
  # (a parada graciosa do mc-server-runner demora até 60s; sem esta espera o
  # novo processo falharia com "Failed to bind to port" em loop)
  waited=0
  while pgrep -f 'java|mc-server-runner' >/dev/null 2>&1; do
    if [ "$waited" -ge 90 ]; then
      echo "[addon] Servidor anterior não encerrou; forçando parada..."
      pkill -TERM -f 'java|mc-server-runner' 2>/dev/null
      nap 5
      waited=0
    fi
    [ "$stopping" = "1" ] && exit 0
    nap 2
    waited=$((waited + 2))
  done

  echo "[addon] Loader=${TYPE:-?} Versão=${VERSION:-?} Memória=${MEMORY:-?}"
  fix_data_ownership
  /image/scripts/start &
  child=$!
  wait "$child"
  rc=$?
  child=""

  # Recebemos SIGTERM durante o wait: NÃO reinicia o servidor, sai limpo
  # (o tini/ docker então para o container sem precisar de SIGKILL)
  if [ "$stopping" = "1" ]; then
    echo "[addon] Servidor finalizado (código $rc). Add-on encerrado."
    exit 0
  fi

  if [ ! -f "$FLAG" ] && [ "$EULA" = "TRUE" ]; then
    echo "[addon] Servidor saiu (código $rc); reiniciando em 3s..."
  fi
  nap 3
done
