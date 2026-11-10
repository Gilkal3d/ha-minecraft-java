# Vídeo explicativo — MC Modded Server

Arquivo gerado: **`MC-Modded-Server-Explicacao.mp4`** (1920×1080, 30 fps, ~4 min 30 s)

O vídeo explica, em português, como o add-on **MC Modded Server** funciona por
dentro: arquitetura, boot do container, camadas de configuração, as 8 abas do
painel, RCON, backups, mods, integração com o Home Assistant, permissões e os
problemas que já foram corrigidos.

Ele vem **narrado em português (Brasil)** por voz neural (Piper TTS), com a
narração sincronizada cena a cena.

| Arquivo | O que é |
|---|---|
| `MC-Modded-Server-Explicacao.mp4` | **Vídeo final**, com narração (49,9 MB) — versionado no repositório |
| `MC-Modded-Server-Explicacao-sem-narracao.mp4` | Mesma coisa sem áudio, para trocar a voz depois (45,8 MB) — **não versionado** (`.gitignore`) |
| `narracao.json` | Roteiro da narração, com o tempo de cada cena |
| `narracao.wav` | Trilha de áudio gerada (22.050 Hz, mono) — **não versionado** (`.gitignore`) |
| `gerar-narracao.ps1` | Script que gera e sincroniza a narração |
| `render.html` | Gerador do vídeo (canvas, 12 cenas, timeline de 270 s) |

## Conteúdo

| # | Cena | O que explica |
|---|---|---|
| 1 | Capa | O que é o add-on e os loaders suportados |
| 2 | Arquitetura | Jogador → servidor → painel → Home Assistant, e o container do add-on |
| 3 | Como o add-on sobe | Supervisor → Dockerfile → `mc-run.sh` (PID 1) → `panel.py` → `itzg/minecraft-server` |
| 4 | Configurações | `options.json` (padrões do HA) vs `custom.json` (painel, tem prioridade) e o mapa de variáveis de ambiente |
| 5 | O painel | As 8 abas do painel web |
| 6 | Console / RCON | Log ao vivo, comandos, parada graciosa, porta 25575 |
| 7 | Backups | Criar, guardar, baixar, restaurar em segundo plano e as armadilhas tratadas no código |
| 8 | Mods | Modrinth por slug, dependências, upload de `.jar`, ativar/desativar |
| 9 | Integração com o HA | Sensores, notificações, agendamentos e exemplo de automação |
| 10 | Permissões | `root` vs usuário `mc` e o histórico de correções (1.1.2 → 1.2.11) |
| 11 | Como usar | Passo a passo de instalação + dicas de RAM, backup, atualização e exposição |
| 12 | Resumo | Pontos principais e link do repositório |

## Como regerar a narração

A narração usa **Piper TTS** (offline, neural, licença MIT) com a voz
`pt_BR-faber-medium`.

1. Baixe o [Piper](https://github.com/rhasspy/piper/releases) e a voz
   [`pt_BR-faber-medium`](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR/faber/medium)
   (`.onnx` + `.onnx.json`).

2. Ajuste o roteiro em `narracao.json`. Cada segmento tem a cena a que pertence;
   o script posiciona as falas dentro da janela de cada cena e avisa se o texto
   estourar o tempo.

   ```json
   { "cena": "backups", "texto": "Para restaurar do PC, o painel contorna o limite do Ingress..." }
   ```

3. Gere a trilha:

   ```powershell
   powershell -File gerar-narracao.ps1 `
     -PiperExe "C:\piper\piper.exe" `
     -Modelo   "C:\piper\pt_BR-faber-medium.onnx"
   ```

   Saída: `narracao.wav` (270 s, 22.050 Hz, mono, com silêncio entre as falas).

4. Junte ao vídeo:

   ```powershell
   ffmpeg -y -i MC-Modded-Server-Explicacao-sem-narracao.mp4 -i narracao.wav `
     -c:v copy -c:a aac -b:a 160k -ar 44100 -ac 2 -movflags +faststart `
     MC-Modded-Server-Explicacao.mp4
   ```

### Ajustes rápidos

| Parâmetro | Efeito |
|---|---|
| `length_scale` no `narracao.json` | `0.92` = ritmo atual. Menor = mais rápido (mais curto), maior = mais devagar. |
| `lead_in` / `gap` | Pausa antes da primeira fala de cada cena e intervalo entre falas. |
| `cenas[].inicio` / `cenas[].fim` | Janela de tempo de cada cena. Precisa bater com o `render.html`. |

## Como regerar o vídeo

O vídeo é desenhado quadro a quadro num `<canvas>` e gravado com
`MediaRecorder`; depois o `.webm` é convertido para MP4 com `ffmpeg`.

1. **`render.html`** — gerador completo (timeline de 270 s a 30 fps).
   Abra o arquivo num navegador Chromium/Chrome. As funções úteis:

   ```js
   window.__meta              // duração total e cenas
   window.previewAt(segundos) // renderiza um quadro para inspeção
   window.startRecordingRT(0, 'saida.webm') // grava em tempo real
   ```

   Para gravar, a página precisa ser servida por HTTP (o `fetch` de upload usa
   a mesma origem):

   ```powershell
   python -m http.server 8787   # ou qualquer servidor estático
   ```

   O `render.html` faz `POST /upload?name=saida.webm` ao terminar — aponte esse
   endpoint para o seu servidor estático ou comente o bloco do `fetch` e salve o
   blob manualmente.

2. **Conversão** (garante 30 fps cravados mesmo com a pequena deriva de
   tempo do `MediaRecorder`):

   ```powershell
   ffmpeg -y -i saida.webm `
     -vf "setpts=N/(30*TB)" -r 30 -vsync cfr `
     -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p -movflags +faststart `
     MC-Modded-Server-Explicacao.mp4
   ```

## Por que gravar em tempo real

A primeira tentativa empurrava os quadros o mais rápido possível com
`canvas.captureStream(0)` + `track.requestFrame()`. O Chromium **c coalesce as
chamadas** e só materializa ~37 quadros por segundo de gravação (808 quadros em
 vez de 8100). Com `captureStream(30)` e a animação avançando pelo
`performance.now()`, o resultado é 1 quadro por intervalo (600 quadros em 20 s
de teste, 8099 no vídeo final).
