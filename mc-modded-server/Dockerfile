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

# O healthcheck da imagem original usa mc-health e fica "unhealthy"
# quando o usuário para o servidor de propósito; desativamos.
HEALTHCHECK NONE

# Painel web (acessado via Ingress do Home Assistant)
EXPOSE 8099

ENTRYPOINT ["/mc-run.sh"]
