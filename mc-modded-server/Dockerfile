# ---- MC Modded Server (add-on para Home Assistant) ----
#
# Base: imagem oficial itzg/minecraft-server — instala automaticamente
# Fabric, NeoForge, Forge, Paper ou Vanilla conforme as opções do add-on.
#
# Tag java21 cobre MC 1.18 até 1.21.x (recomendado).
# Para Minecraft 1.17 ou anteriores, troque abaixo para:
#   itzg/minecraft-server:java17  (MC 1.13 - 1.17)
#   itzg/minecraft-server:java8   (MC <= 1.12.2)
#
FROM itzg/minecraft-server:java21

USER root

# python3 = backend do painel; jq = leitura das opções do add-on;
# procps = pgrep usado pelo painel para detectar o servidor
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 curl ca-certificates jq procps \
    && rm -rf /var/lib/apt/lists/*

COPY mc-run.sh /mc-run.sh
COPY panel.py /panel/panel.py
COPY static/ /panel/static/

# Garante LF (o Git no Windows pode ter gravado CRLF, o que quebra o shebang)
RUN chmod +x /mc-run.sh \
    && sed -i 's/\r$//' /mc-run.sh

# Healthcheck do PAINEL (não do jogo). O Supervisor do HA exige um healthcheck
# ativo para marcar o add-on como "iniciado": com HEALTHCHECK NONE o estado
# ficava preso em "Iniciando" para sempre e o painel via Ingress nunca carregava.
# Apontando para o painel: fica "healthy" em ~10s (antes do Minecraft terminar
# de carregar) e NÃO acusa erro quando você para o servidor de propósito.
HEALTHCHECK --interval=10s --timeout=6s --start-period=20s --retries=3 \
  CMD curl -fsS --max-time 4 "http://127.0.0.1:${INGRESS_PORT:-8099}/api/status" >/dev/null || exit 1

# Painel web (acessado via Ingress do Home Assistant)
EXPOSE 8099

ENTRYPOINT ["/mc-run.sh"]
