# MC Modded Server

Servidor **Minecraft Java Edition** para Home Assistant com **Fabric, NeoForge,
Forge, Paper, Purpur ou Vanilla** e um **painel web completo** via Ingress:
console ao vivo, editor de `server.properties`, whitelist e gerenciador de mods.

Construído sobre a imagem oficial
[`itzg/minecraft-server`](https://github.com/itzg/docker-minecraft-server)
— a solução mais madura e testada para servidores MC em Docker.

## Como usar

1. Aceite a **EULA** nas configurações do add-on (`eula: true`) — exigência da Mojang.
2. Escolha o **loader** (Fabric/NeoForge/Forge/…) e a **versão do Minecraft**.
3. Inicie o add-on. Na primeira vez o servidor gera o mundo (pode demorar).
4. Abra o **painel** (ícone na barra lateral do HA) para gerenciar tudo.
5. Porta do jogo: `25565` (configure em **Configurações → Rede** do add-on).

## Configurações do add-on

| Opção | Descrição |
|---|---|
| `eula` | Aceite da EULA da Mojang. `false` = servidor não inicia. |
| `loader` | `FABRIC`, `NEOFORGE`, `FORGE`, `VANILLA`, `PAPER` ou `PURPUR`. |
| `minecraft_version` | Versão do Minecraft, ex.: `1.21.4`, `1.20.1`, `LATEST`. |
| `memory` | RAM do servidor, ex.: `4G`, `8G`. |
| `aikar_flags` | Flags de JVM afinadas para servidores (recomendado). |
| `motd`, `max_players`, `online_mode`, `difficulty`, `gamemode`, `pvp`, `allow_nether`, `enable_command_block`, `level_name`, `level_type`, `view_distance` | Propriedades básicas aplicadas a cada start. |
| `whitelist` | Lista de nomes de jogadores (mantida a cada start). |
| `ops` | Lista de operadores (admins do jogo). |
| `modrinth_projects` | Slugs do Modrinth baixados automaticamente a cada start, ex.: `fabric-api`, `lithium`. |
| `rcon_password` | Senha do RCON. Deixe vazio para gerar uma automaticamente. |

> **Importante:** `loader`, `minecraft_version`, `memory`, `whitelist`, `ops` e
> `modrinth_projects` são lidos ao iniciar o **add-on**. Depois de alterar, clique
> em **Parar → Iniciar** (ou Salvar, que reinicia) no add-on.

## Painel web

| Aba | O que faz |
|---|---|
| **Console** | Log ao vivo + campo para enviar comandos (RCON). |
| **server.properties** | Editor de texto completo; salva com o servidor parado. |
| **Whitelist** | Adiciona/remove jogadores; o UUID é resolvido pela API da Mojang (ou UUID offline em servidores `online-mode=false`). |
| **Mods** | Instala do Modrinth por slug (versão compatível + dependências), envia `.jar`, ativa/desativa/exclui mods. |

Start/Stop/Reiniciar no cabeçalho: o **parar** é gracioso (envia `/stop` via RCON);
o painel segura o servidor parado até você iniciar de novo.

## Instalando mods

- **Modrinth (painel)**: informe o slug do projeto (a última parte da URL,
  ex.: `modrinth.com/mods/lithium` → `lithium`). A versão compatível com o seu
  loader/versão do Minecraft é escolhida automaticamente.
- **Upload (painel)**: envie `.jar` direto do seu computador (mods que não estão
  no Modrinth, ex.: exclusivos do CurseForge).
- **Opções do add-on**: liste slugs em `modrinth_projects` para baixar em cada start.
- **Files/Samba**: jogue os `.jar` em `mc_modded/mods/` no diretório de dados do add-on.

Após mexer em mods, use **Reiniciar** no painel. No cliente, o modloader, versão
do Minecraft e mods precisam ser os mesmos do servidor.

## Versões antigas do Minecraft

A imagem padrão usa **Java 21** (MC 1.18–1.21.x). Para servidores mais antigos,
edite o `Dockerfile` do add-on (aba Configuração/avançado do add-on) trocando a
tag da base:

- MC 1.13–1.17 → `itzg/minecraft-server:java17`
- MC ≤ 1.12.2 → `itzg/minecraft-server:java8`

## Dicas

- **Backup**: o mundo fica em `mc_modded/` no diretório de dados do add-on.
  Use o backup nativo do HA (Configurações → Sistema → Backups) ou Samba.
- **Desempenho**: em hardware limitado, prefira Fabric + Lithium/FerriteCore.
  Reduza `view-distance` para 8 ou menos.
- **Domínio externo**: use o proxy Duck DNS/Caddy do HA ou Cloudflare Tunnel;
  nunca exponha a 25565 direto na internet com `online-mode=false`.
