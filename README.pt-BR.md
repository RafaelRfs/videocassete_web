# Videocassete Web

**[Read in English](README.md)**

Extensão de navegador com um pequeno processo local para baixar vídeos de streaming na velocidade da rede, em paralelo e fora do navegador. O resultado sai em MP4, ou em MP3 se você escolher só o áudio.

| O que faz | Como |
|---|---|
| Baixa streams **HLS** (`.m3u8`) e **DASH** (`.mpd`) e vídeos MP4 diretos | detecta o manifesto na aba, baixa os segmentos em paralelo e junta com o ffmpeg |
| Baixa de **YouTube, Instagram, Facebook, TikTok, X, Vimeo, Threads** | manda o link para o [yt-dlp](https://github.com/yt-dlp/yt-dlp) com os cookies da sua sessão |
| Salva **só o áudio em MP3** | melhor faixa de áudio → MP3 V0 (~245 kbps) com título, artista e capa |
| Lista **todos os vídeos de um feed** para escolher um | seção "Vídeos na página" no popup |
| **Grava pela página** quando não dá para baixar | `captureStream` + `MediaRecorder`, salvando no disco a cada segundo |

O download continua com a aba ou o navegador fechados. Se cair, o **Retomar** continua do segmento onde parou. A interface segue o idioma do navegador (português ou inglês).

Funciona no **Windows, macOS e Linux**, com **Chrome, Edge, Brave, Opera, Vivaldi, Chromium e Firefox**.

---

## Requisitos mínimos

| Item | Mínimo | Observação |
|---|---|---|
| Windows | 10 (1809) ou 11 | o instalador usa o `winget`, que já vem no Windows. Sem ele, baixa as ferramentas direto |
| macOS | 11 Big Sur | usa o [Homebrew](https://brew.sh) se existir. Sem ele, baixa as ferramentas direto |
| Linux | qualquer distro com glibc (Ubuntu 20.04+, Debian 11+, Fedora 36+…) | o ffmpeg vem do `apt`/`dnf`/`pacman`/`zypper` (pede `sudo`) |
| Chrome / Edge / Brave / Opera / Vivaldi | 121+ | |
| Firefox | 140+ | veja [Firefox](#firefox) |
| Python | 3.9+ | **só a biblioteca padrão, sem `pip`**. O instalador instala o Python se faltar |
| Disco | 2x o tamanho do vídeo | os segmentos ficam numa pasta temporária até a junção |

O instalador encontra ou instala o resto. Você não precisa instalar nada disto antes:
- **ffmpeg**: junta vídeo e áudio, decripta HLS AES-128 e converte gravações e MP3.
- **yt-dlp** e **Deno**: só para YouTube e redes sociais.

---

## Instalação (2 passos)

### 1. Rodar o instalador

Baixe o **`videocassete_web-<versão>.zip`** da [última versão](../../releases/latest) e extraia. Depois:

| Sistema | O que fazer |
|---|---|
| **Windows** | dois cliques em **`install.cmd`** |
| **macOS** | dois cliques em **`install.command`** (na primeira vez: botão direito → Abrir), ou `sh install.sh` no Terminal |
| **Linux** | no terminal, dentro da pasta: `sh install.sh` |

O instalador:
- pergunta antes de instalar o que falta (`--yes` não pergunta);
- copia o programa para uma pasta fixa:
  - Windows: `%LOCALAPPDATA%\VideocasseteWeb\app`;
  - macOS: `~/Library/Application Support/VideocasseteWeb/app`;
  - Linux: `~/.local/share/videocassete_web/app`;
- registra o processo local em todos os navegadores;
- testa a conexão;
- abre a pasta da extensão.

Depois disso, o zip baixado pode ser apagado.

### 2. Carregar a extensão

**Chrome, Edge, Brave, Opera, Vivaldi**
1. Abra `chrome://extensions` (`edge://extensions`, `brave://extensions`, `opera://extensions`).
2. Ligue o **Modo do desenvolvedor**.
3. Clique em **Carregar sem compactação** e escolha a pasta **`app/extension`** que o instalador abriu.
4. Fixe o ícone: clique no quebra-cabeça 🧩 ao lado da barra de endereço e no alfinete 📌.
5. Abra o popup. O selo no topo deve dizer **pronto**.

**Firefox**: instale o `.xpi` da [última versão](../../releases/latest) (um clique instala). Veja [Firefox](#firefox).

### Atualizar

Baixe o zip novo e rode o instalador de novo. Depois clique em recarregar ↻ no card da extensão em `chrome://extensions`. As configurações e os downloads ficam.

### Desinstalar

Rode `python install.py --uninstall` (Windows: `install.cmd --uninstall`) e remova a extensão do navegador.

---

## Como usar

### Vídeo HLS/DASH/MP4

1. Abra a página do vídeo e **dê play**. O ícone mostra quantos streams achou.
2. Clique no ícone. Em **Nesta aba** aparece cada stream com duração e qualidades.
3. Escolha a qualidade e clique em **Baixar**.
4. Acompanhe em **Downloads**. Pode fechar o popup, a aba ou o navegador.
5. O arquivo vai para a pasta **Downloads**, ou para a pasta escolhida em Configurações.

Se o vídeo já tocava antes de instalar a extensão, recarregue a página (F5) e dê play de novo.

### YouTube

Abra o vídeo e clique no ícone. O card mostra o título e as qualidades. Para salvar só o áudio, escolha **🎵 Só áudio (MP3)**.

### Instagram, Facebook e outros feeds

Em páginas com vários vídeos, **Vídeos na página** lista cada um com miniatura, perfil e legenda, duração e se está tocando. Cada vídeo tem estes botões:

- **👁**: rola a página até o vídeo e o destaca.
- **Baixar** (Vídeo ou 🎵 MP3): manda o link do post para o yt-dlp com a sua sessão. O arquivo sai como `facebook_20261027-121154.mp4`.
- **⏺ Gravar**: grava aquele vídeo pela página. Serve para posts sem link, como anúncios.

O feed só carrega os vídeos perto da tela. Role e clique em **↻ Atualizar** para listar mais.

### Gravar pela página (plano B)

O card tracejado **Gravar o vídeo pela página** funciona com qualquer vídeo que toca na aba. A velocidade muda o resultado:

- **1x**: tempo real, cópia fiel.
- **1,5x / 2x**: grava mais rápido, e no fim o ffmpeg devolve a duração original (recodifica).
- **4x**: pode perder quadros e áudio.

A gravação vai para o disco a cada segundo. Fechar a aba não perde o que já foi gravado.

### Configurações (no fim do popup)

- **Qualidade padrão**: melhor, até 1080p/720p/480p, ou menor.
- **Conexões paralelas**: padrão 8. Aumente (16–32) com internet rápida. O processo local diminui sozinho se o site recusar.
- **Pasta de destino**: vazio = Downloads.
- **Baixar um link**: cole um `.m3u8`, `.mpd`, MP4 ou link de vídeo do YouTube/rede social.

### Linha de comando (sem a extensão)

```sh
python <app>/host/stream_worker.py --probe --url https://.../manifest.mpd
python <app>/host/stream_worker.py --url https://.../master.m3u8 --quality 720 --threads 16 --header "Cookie: ..."
python <app>/host/stream_worker.py <dados>/jobs/<pasta-do-job>     # retoma um job
```

---

## Firefox

O Firefox só instala extensões assinadas pela Mozilla. As não assinadas carregam só de forma temporária e somem ao reiniciar.

- **Para usar:** instale o `.xpi` assinado da página de [Releases](../../releases/latest).
- **Para testar o código-fonte:**
  1. abra `about:debugging#/runtime/this-firefox`;
  2. clique em **Carregar extensão temporária…** e escolha o `manifest.json` da pasta `app/extension`.
- **Depois de instalar**, abra o popup uma vez. Se aparecer **Permitir acesso a todos os sites**, clique, porque o Firefox não concede esse acesso sozinho.
- **Gravar pela página** deixa o som da aba mudo enquanto grava. É uma limitação do `mozCaptureStream` no Firefox, e a gravação sai com áudio.

**Para o mantenedor assinar:**
1. Crie as chaves de API grátis em [addons.mozilla.org](https://addons.mozilla.org/developers/addon/api/key/).
2. Rode:

   ```sh
   python tools/build_release.py
   npx web-ext sign --channel unlisted --source-dir extension --api-key <JWT issuer> --api-secret <JWT secret>
   ```
3. Anexe o `.xpi` gerado ao release do GitHub. "Unlisted" quer dizer distribuição própria, sem página pública no AMO.

---

## Problemas comuns

| Sintoma | Causa e solução |
|---|---|
| Popup mostra **processo local ausente** | Rode o instalador de novo. No Linux com navegador **Snap/Flatpak**, o isolamento pode bloquear o Native Messaging: use o navegador do `.deb`/`.rpm` do fabricante. |
| "Could not establish connection. Receiving end does not exist." | O código de fundo da extensão está desatualizado. Recarregue a extensão em `chrome://extensions`. |
| Nada em "Nesta aba" | Dê play. Se já tocava, recarregue com F5. Ou use "Vídeos na página" ou a gravação. No Firefox, confira o acesso aos sites (acima). |
| HTTP 401/403 no meio do download | O login ou o token expirou. Deixe a página do vídeo aberta e logada, dê play e clique em **Retomar**: a extensão manda cookies e tokens novos. |
| "o site pediu login" (YouTube/Instagram/Facebook) | Entre na sua conta neste navegador e tente de novo. |
| "mídia criptografada com DRM" / "segmentos cifrados" | O conteúdo é protegido e não é decifrado. Use o botão de download do próprio site, se você tiver permissão. |
| O YouTube parou de funcionar de repente | O YouTube muda com frequência. Rode o instalador de novo: ele atualiza o yt-dlp. |
| macOS: "não pode ser aberto porque é de um desenvolvedor não identificado" | Botão direito em `install.command` → Abrir, ou `sh install.sh` no Terminal. |

Cada download tem um log em `<dados>/jobs/<job>/worker.log`. Ele registra os motivos de recusa do servidor e os nomes dos cabeçalhos usados, nunca os valores dos tokens.

---

## Limites

- **DRM** (Netflix, Prime Video, Disney+, Globoplay…) e **mídia cifrada pelo serviço** (ex.: gravações de reunião do Microsoft Stream/SharePoint com `enableEncryption`) não são decifrados nem contornados. A extensão detecta pelo conteúdo real e avisa. Um manifesto que só *anuncia* `<ContentProtection>` mas entrega mídia aberta baixa normalmente.
- **Ao vivo:** só vídeos completos (VOD).
- **DASH com vários períodos** (anúncios costurados): só o primeiro período é baixado.
- **Chrome Web Store / Edge Add-ons:** a extensão não é publicada nas lojas, porque as regras delas proíbem downloader de YouTube. Por isso o "Carregar sem compactação".
- Muitos downloads seguidos com a sua conta podem fazer YouTube, Instagram ou Facebook marcarem a conta por automação.
- Baixar conteúdo pode violar os termos de uso do site ou direitos autorais. Use em conteúdo que você tem direito de guardar.

---

## Como funciona

```
Navegador
 ├─ background.js   observa as requisições da aba: manifestos e cabeçalhos de autenticação por servidor
 ├─ popup.js        lista streams e vídeos, cookies por servidor, configurações e downloads
 ├─ scan.js         (injetado) lista os <video> da página e acha o link de cada post
 └─ recorder.js     (injetado) gravação pela página; manda pedaços de 1 s para o background
        │  Native Messaging (videocassete_web)
        ▼
host/stream_host.py   recebe os comandos, cria a pasta do job e dispara o worker desligado do navegador
        ▼
host/stream_worker.py processo independente: lê o manifesto, baixa em paralelo, retoma, junta com o ffmpeg
                      (ou chama o yt-dlp para YouTube e redes sociais)
```

**Segurança**
- Cookies e tokens vão só para o servidor dono deles. O token do manifesto não vaza para a CDN, e cada servidor recebe os próprios cookies.
- Esses dados ficam em arquivos dentro da pasta do job e são apagados quando o download termina ou é cancelado.
- O certificado HTTPS é sempre verificado:
  - no Windows, se falta a raiz de um site, o processo local pede ao Windows para baixá-la;
  - no macOS, com o Python do python.org, usa o pacote de certificados do sistema.

| Caminho | Papel |
|---|---|
| `extension/` | extensão Manifest V3 (Chromium e Firefox com o mesmo código), `_locales/en` e `_locales/pt_BR` |
| `host/stream_host.py` | Native Messaging host (comandos e gravação persistente) |
| `host/stream_worker.py` | downloader: HLS, DASH, MP4 direto, yt-dlp, junção, retomada |
| `host/platform_paths.py` | o que muda entre Windows, macOS e Linux |
| `host/i18n.py` | mensagens do processo local em português e inglês |
| `install.py` | instalador (dependências, cópia, launcher, registro nos navegadores, autoteste) |
| `tests/` | `python -m unittest discover -s tests` (downloads HLS/DASH/AES-128 reais contra um servidor local) |
| `tools/build_release.py` | gera os zips da versão em `dist/` |

Pasta de dados (`<dados>`):
- Windows: `%LOCALAPPDATA%\VideocasseteWeb`;
- macOS: `~/Library/Application Support/VideocasseteWeb`;
- Linux: `~/.local/share/videocassete_web`.

Ela guarda:
- `app/`: o programa instalado;
- `jobs/`: um diretório por download;
- `bin/`: ferramentas baixadas pelo instalador;
- `tools.json`.

## Licença

[MIT](LICENSE)
