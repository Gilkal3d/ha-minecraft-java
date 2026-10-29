# HA Minecraft Java Add-ons

Repositório de add-ons para Home Assistant com servidores **Minecraft Java Edition**
com suporte a **Fabric, NeoForge, Forge, Paper, Purpur e Vanilla**, com
**painel web completo** acessado pelo Ingress do Home Assistant.

## Add-ons

### MC Modded Server

Servidor Minecraft Java moddável com interface gráfica:

- **Escolha o loader**: Fabric, NeoForge, Forge, Paper, Purpur ou Vanilla
- **Escolha a versão do Minecraft**: qualquer versão oficial (ex.: `1.21.4`, `LATEST`, snapshots)
- **Aba Configurações em português**: cada opção do servidor explicando para que
  serve (loader, versão, RAM, MOTD, dificuldade, modo de jogo, PvP, whitelist,
  ops, mods…), salva e reinicia em um clique
- **Console ao vivo** + envio de comandos (via RCON)
- **Editor do `server.properties`** direto no painel
- **Gerenciador de whitelist** (adiciona/remove jogadores, gera UUID automaticamente)
- **Gerenciador de mods**: instalar do **Modrinth** por slug (com dependências),
  upload de `.jar`, ativar/desativar/excluir
- Instalação automática de mods do Modrinth via opções do add-on
- Flags do Aikar, memória configurável, crash com restart automático

## Instalação

1. Home Assistant → **Configuração → Add-ons → Loja de Add-ons → ⋮ (menu) → Repositórios**
2. Adicione esta URL do repositório:

   ```
   https://github.com/Gilkal3d/ha-minecraft-java
   ```

3. Recarregue a página, encontre o **MC Modded Server** e clique em **Instalar**
4. Nas **Configurações do add-on**, marque `eula: true` (aceite da EULA da Mojang)
5. Ajuste o loader, versão, memória etc. e clique em **Iniciar**
6. Abra o painel pelo ícone na barra lateral (ingress) ou pela aba **Log/Ingress**

## Recursos mínimos

- Minecraft com mods exige RAM de verdade: **4 GB** para Fabric leve,
  **6–8 GB** para modpacks médios, **10–12 GB** para packs grandes.
- Recomendado: Home Assistant OS em x86 (mini PC, NUC, VM) ou
  Raspberry Pi 4/5 com 8 GB.
- Java: a imagem padrão usa **Java 21** (cobrem MC 1.18–1.21.x).
  Para MC ≤ 1.17, edite o `Dockerfile` do add-on conforme comentários no topo do arquivo.

## Aviso legal

Este projeto não é afiliado à Mojang/Microsoft, Valve nem ao projeto Home Assistant.
O servidor baixa o software dos servidores oficiais da Mojang em tempo de execução e
você precisa aceitar a [EULA da Minecraft](https://aka.ms/MinecraftEULA) você mesmo.
