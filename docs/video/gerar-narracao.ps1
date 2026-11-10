<#
  Gera a narração em PT-BR (Piper TTS) e monta a trilha de áudio sincronizada
  com a linha do tempo do vídeo (docs/video/narracao.json).

  Requer:
    - piper.exe  (https://github.com/rhasspy/piper/releases)
    - pt_BR-faber-medium.onnx (+ .onnx.json) em https://huggingface.co/rhasspy/piper-voices

  Uso:
    powershell -File gerar-narracao.ps1 `
      -PiperExe "C:\piper\piper.exe" `
      -Modelo   "C:\piper\pt_BR-faber-medium.onnx"
#>
param(
  [string]$PiperExe    = "C:\Users\Gilton\AppData\Local\Temp\opencode\tts\piper\piper\piper.exe",
  [string]$Modelo      = "C:\Users\Gilton\AppData\Local\Temp\opencode\tts\pt_BR-faber-medium.onnx",
  [string]$Narracao    = (Join-Path $PSScriptRoot "narracao.json"),
  [string]$Saida       = (Join-Path $PSScriptRoot "narracao.wav"),
  [string]$Work        = "C:\Users\Gilton\AppData\Local\Temp\opencode\tts\segs",
  [double]$DuracaoVideo = 270
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Work | Out-Null

# ----------------------------------------------------------- WAV (PCM 16 bits)
function Get-WavPcm {
  param([string]$Path)
  $fs = [System.IO.File]::OpenRead($Path)
  try {
    $br = New-Object System.IO.BinaryReader($fs)
    $null = $br.ReadBytes(12)                        # RIFF + tamanho + WAVE
    $off = 0; $len = 0
    while ($fs.Position -lt $fs.Length) {
      $id = [System.Text.Encoding]::ASCII.GetString($br.ReadBytes(4))
      $size = $br.ReadInt32()
      $pos = $fs.Position
      if ($id -eq 'data') { $off = $pos; $len = $size; break }
      $fs.Seek($size + ($size % 2), 'Current') | Out-Null
    }
    $fs.Seek($off, 'Begin') | Out-Null
    return ,$br.ReadBytes($len)
  } finally { $fs.Dispose() }
}

function Write-Wav {
  param([string]$Path, [byte[]]$Data, [int]$Rate)
  $ms = [System.IO.File]::Create($Path)
  try {
    $br = New-Object System.IO.BinaryWriter($ms)
    $br.Write([System.Text.Encoding]::ASCII.GetBytes('RIFF'))
    $br.Write([int](36 + $Data.Length))
    $br.Write([System.Text.Encoding]::ASCII.GetBytes('WAVEfmt '))
    $br.Write([int]16)
    $br.Write([int16]1)          # PCM
    $br.Write([int16]1)          # mono
    $br.Write([int]$Rate)
    $br.Write([int]($Rate * 2))  # bytes por segundo
    $br.Write([int16]2)          # alinhamento do bloco
    $br.Write([int16]16)         # bits
    $br.Write([System.Text.Encoding]::ASCII.GetBytes('data'))
    $br.Write([int]$Data.Length)
    $br.Write($Data, 0, $Data.Length)
    $br.Flush()
  } finally { $ms.Dispose() }
}

# ------------------------------------------------------------- narração
$spec = Get-Content $Narracao -Raw -Encoding UTF8 | ConvertFrom-Json
$sr        = [int]$spec.sample_rate
$scale     = [double]$spec.length_scale
$lead      = [double]$spec.lead_in
$gap       = [double]$spec.gap
$cenas     = $spec.cenas
$segmentos = $spec.segmentos

# gera um WAV por segmento (entrada UTF-8 sem BOM, para não perder acentos)
Write-Host "Gerando $($segmentos.Count) segmentos de fala (length_scale=$scale)..."
$pcm = @{}
$i = 0
foreach ($s in $segmentos) {
  $i++
  $wav = Join-Path $Work ("seg{0:D3}.wav" -f $i)
  $txt = Join-Path $Work ("seg{0:D3}.txt" -f $i)
  [System.IO.File]::WriteAllText($txt, $s.texto, (New-Object System.Text.UTF8Encoding($false)))
  $args = "--model `"$Modelo`" --output_file `"$wav`" --length_scale $scale"
  Start-Process -FilePath $PiperExe -ArgumentList $args -RedirectStandardInput $txt `
                -RedirectStandardOutput "$txt.out" -RedirectStandardError "$txt.err" `
                -Wait -NoNewWindow | Out-Null
  $bytes = Get-WavPcm $wav
  if (-not $bytes -or $bytes.Length -eq 0) { throw "Falha ao gerar o segmento $i ($($s.texto))" }
  $pcm[$i] = $bytes
}

# ------------------------------------------------- posicionamento por cena
Write-Host "`nPosicionando as falas dentro de cada cena:"
$plano = @()
$problemas = 0
foreach ($cena in $cenas) {
  $doCena = @($segmentos | Where-Object { $_.cena -eq $cena.id })
  $t = [double]$cena.inicio + $lead
  $n = 0
  foreach ($s in $doCena) {
    $idx = [array]::IndexOf([object[]]$segmentos, $s) + 1
    $dur = $pcm[$idx].Length / 2.0 / $sr
    $plano += [pscustomobject]@{ idx = $idx; t = $t; dur = $dur; fim = $t + $dur; cena = $cena.id; texto = $s.texto }
    Write-Host ("  {0,-12} {1,6:N2}s + {2,5:N2}s = {3,6:N2}s  (cena ate {4}s)  {5}" -f $cena.id, $t, $dur, ($t + $dur), $cena.fim, ($s.texto.Substring(0, [Math]::Min(52, $s.texto.Length))))
    $t += $dur + $gap
    $n++
  }
  $ultimo = $plano[-1]
  if ($ultimo.fim -gt [double]$cena.fim) {
    Write-Host ("  ATENCAO: a cena {0} termina em {1:N2}s mas o texto vai ate {2:N2}s (estoura {3:N2}s)" -f $cena.id, $cena.fim, $ultimo.fim, ($ultimo.fim - [double]$cena.fim)) -ForegroundColor Yellow
    $problemas++
  }
}
if ($plano[-1].fim -gt $DuracaoVideo) {
  Write-Host ("  ATENCAO: o ultimo segmento termina em {0:N2}s, depois do fim do video ({1}s)" -f $plano[-1].fim, $DuracaoVideo) -ForegroundColor Yellow
  $problemas++
}
if ($problemas -eq 0) { Write-Host "  OK - todas as cenas comportam a fala." -ForegroundColor Green }

# --------------------------------------------------------------- montagem
$total = [int]($DuracaoVideo * $sr)
$buf = New-Object byte[] ($total * 2)
foreach ($p in $plano) {
  $b = $pcm[$p.idx]
  $off = [int]([math]::Round($p.t * $sr)) * 2
  $n = [math]::Min($b.Length, $buf.Length - $off)
  if ($n -gt 0) { [Array]::Copy($b, 0, $buf, $off, $n) }
}
Write-Wav $Saida $buf $sr

$fala = ($plano | Measure-Object -Property dur -Sum).Sum
Write-Host ("`nTrilha: {0}" -f $Saida) -ForegroundColor Green
Write-Host ("Fala: {0:N1}s em {1:N1}s de video ({2:P0} de ocupacao)." -f $fala, $DuracaoVideo, ($fala / $DuracaoVideo))
