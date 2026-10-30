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

> **Prioridade de configuração:** as opções do formulário do add-on são os
> **padrões**. Tudo que você salvar na aba **Configurações** do painel web
> (com descrições em português) tem prioridade sobre elas. Para voltar ao
> formulário do HA, use **Restaurar padrões** nessa aba.

## Painel web

| Aba | O que faz |
|---|---|
| **Visão geral** | Status, RAM em uso, uptime e **barra de progresso do primeiro start** (baixando loader → instalando → gerando mundo). Mostra quem está online com **avatar**. |
| **Console** | Log ao vivo + campo para enviar comandos (RCON). Filtro de busca, seguir rolagem, **histórico de comandos (seta ↑)**, botões rápidos (`list`, `time`, `weather`, `save-all`…) e **download do log**. |
| **Jogadores** | Quem está online (com avatar), botões de **kick / ban / OP / whitelist**, quem entrou ou saiu na sessão, e a lista de whitelist editável. |
| **Backups** | **Cria, restaura, baixa e exclui** backups do mundo + configs em `.zip`. Restaura também um backup **baixado do seu PC** (envie o `.zip` em **blocos de 8 MB** — contorna o limite de 16 MB do ingress do HA —, com **barra de progresso** e retomada automática; até **2 GB**). A restauração roda em **segundo plano com status em tempo real**. Configura quantos manter (os antigos são apagados). |
| **Configurações** | Formulário completo em **português**, cada opção explicando para que serve: loader, versão, RAM, MOTD, dificuldade, modo de jogo, PvP, Nether, whitelist, ops, mods do Modrinth, RCON etc. Inclui **agendamentos** (restart e backup diários) e **notificações no Home Assistant**. Salva e reinicia o servidor em um clique. |
| **server.properties** | Editor de texto completo; salva com o servidor parado. |
| **Mods** | Instala do Modrinth por slug (versão compatível + dependências), envia `.jar`, ativa/desativa/exclui mods. |

Start/Stop/Reiniciar no cabeçalho: o **parar** é gracioso (envia `/stop` via RCON);
o painel segura o servidor parado até você iniciar de novo.

## Home Assistant (sensores e notificações)

A partir da **versão 1.2.0** o add-on usa a API do próprio Home Assistant para
criar sensores e enviar notificações (é preciso apenas abrir o painel uma vez
para o add-on subir e publicar).

**Sensores criados automaticamente:**

| Entidade | Mostra |
|---|---|
| `sensor.mc_server_status` | `online` / `offline` (+ loader, versão, uptime, RAM) |
| `sensor.mc_players_online` | nº de jogadores online (+ nomes, máx.) |
| `sensor.mc_ram` | RAM do servidor em MB |

**Notificações:** ative em **Configurações → Notificações no Home Assistant** e
escolha o serviço (ex.: `notify.mobile_app_celular`). O add-on avisa quando alguém
**entra/sai**, quando o servidor **cai/religa** e quando um **backup termina**.
Deixe o serviço vazio para usar a notificação persistente do HA.

**Agendamentos:** em **Configurações → Agendamentos** defina um horário (HH:MM)
para **restart diário** e **backup diário**. O restart é gracioso (RCON `stop`) e o
servidor religa sozinho; o backup respeita quantos manter.

**Exemplo de automação** — avisar quando alguém entrar:

```yaml
automation:
  - alias: "MC: jogador entrou"
    trigger:
      - platform: state
        entity_id: sensor.mc_players_online
    condition:
      - condition: numeric_state
        entity_id: sensor.mc_players_online
        above: 0
    action:
      - service: notify.mobile_app_celular
        data:
          message: "Alguém entrou no servidor de Minecraft!"
```

## Solução de problemas

| Sintoma | Causa / solução |
|---|---|
| Add-on fica em **"Iniciando"** e o painel mostra *"O aplicativo está iniciando…"* | **Versão ≥ 1.1.2:** o Supervisor exige um healthcheck ativo para marcar o add-on como "iniciado"; `HEALTHCHECK NONE` deixava o estado preso em "iniciando" para sempre (mesmo com o servidor rodando). A 1.1.2 usa um healthcheck do painel e resolve em ~10s. Se o Log mostrar várias linhas `[addon] MC Modded Server iniciando...` seguidas de `Failed to bind to port`, é um loop de reinício (também corrigido na 1.1.1+). |
| Log repete `EULA ainda não aceita` | Defina `eula: true` nas configurações do add-on e reinicie. |
| Servidor inicia e some (reinicia sozinho) | Falta de RAM: NeoForge/Forge precisam de ≥ 4G livres no host. Diminua `memory` ou libere RAM do Home Assistant. O kernel mata o Java (OOM) e o add-on reinicia. |
| Painel carrega, mas o Minecraft demora | Normal no **primeiro start** com mods (download do loader + mods + geração do mundo). O painel já funciona nesse meio-tempo. |
| Mudou arquivos do add-on e nada mudou | O HA só reconstrói a imagem quando a **versão** sobe ou você usa **⋮ → Rebuild**. Nunca desinstale para atualizar (apaga o mundo em `/data`). |

> **Dica:** ligue a opção **Watchdog** nas configurações do add-on — se o painel
> web parar de responder, o Supervisor reinicia o add-on automaticamente.

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

- **Backup**: use a aba **Backups** do painel (cria/restaura `.zip` do mundo).
  Os backups ficam em `/data/backups` (sobrevivem a atualizações do add-on), mas
  **não entram no backup nativo do HA** — então **baixe os `.zip` para o seu PC**
  de tempos em tempos (botão **⬇ Baixar**). Para proteção extra, use também o
  backup nativo do HA (Configurações → Sistema → Backups) ou Samba.
- **Desempenho**: em hardware limitado, prefira Fabric + Lithium/FerriteCore.
  Reduza `view-distance` para 8 ou menos.
- **Domínio externo**: use o proxy Duck DNS/Caddy do HA ou Cloudflare Tunnel;
  nunca exponha a 25565 direto na internet com `online-mode=false`.
