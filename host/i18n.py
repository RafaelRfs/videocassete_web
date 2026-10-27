"""Mensagens que o processo local devolve para a extensão, em português e inglês.

A extensão manda o idioma do navegador em cada mensagem ("lang": "pt-BR", "en-US"...). O host chama
set_lang() ao receber, e o worker, que roda separado do navegador, lê o idioma salvo no job.json.
"""
LANG = 'en'

MESSAGES = {
    'pt': {
        'cert_untrusted': 'o certificado HTTPS de {host} não é confiável para o sistema; o download foi recusado por segurança.',
        'http_429': 'o site recusou por excesso de pedidos (HTTP 429). Espere alguns minutos ou use menos conexões paralelas.',
        'http_429_one': 'o site recusou por excesso de pedidos (HTTP 429), mesmo com 1 conexão.{detail} Espere alguns minutos e clique em Retomar.',
        'http_auth': 'HTTP {code} em {url}: o servidor pediu login, o link expirou ou recusou o ritmo dos pedidos.{detail} '
                     'Deixe a página do vídeo aberta (e logada) e clique em Retomar: a extensão manda os cookies atualizados '
                     'e continua de onde parou. Se repetir, use "Gravar pela página".',
        'failed_after': 'falhou depois de {n} tentativas: {url}{detail}',
        'server_said': ' Resposta do servidor: "{reason}".',
        'youtube_internal': 'esse é um pedaço interno do YouTube; use o link da página do vídeo (youtube.com/watch?v=...).',
        'not_a_stream': 'esse link é uma página ou um texto, não um stream. Abra a página do vídeo, dê play e escolha o '
                        'stream na lista "Nesta aba"; ou cole o endereço do .m3u8/.mpd.',
        'not_video': 'o link não parece ser vídeo (tipo: {ctype})',
        'unknown': 'desconhecido',
        'live': 'transmissão ao vivo: esta versão só baixa vídeos completos (VOD)',
        'no_segments': 'playlist sem segmentos',
        'tpl_no_duration': 'SegmentTemplate sem duration nem SegmentTimeline',
        'mpd_no_period': 'MPD sem Period',
        'mpd_no_tracks': 'MPD sem faixas de vídeo ou áudio',
        'audio_only': 'só áudio',
        'drm': 'mídia criptografada com DRM (Widevine/PlayReady/FairPlay){detail}: o download direto não decripta DRM.',
        'service_ciphered': 'o serviço entregou os segmentos cifrados; eles não formam um vídeo. Use "Gravar pela página" '
                            'ou o botão Baixar do próprio site. Remover este item libera o espaço dos segmentos',
        'aes_needs_ffmpeg': 'este stream é criptografado (AES-128) e precisa do ffmpeg para decriptar. Instale com: {hint}',
        'ffmpeg_merge_failed': 'o ffmpeg falhou ao juntar: {err}',
        'ffmpeg_rec_failed': 'o ffmpeg falhou ao finalizar a gravação: {err}',
        'need_ytdlp': 'para baixar de YouTube, Instagram e Facebook instale o yt-dlp: {hint}',
        'yt_login': 'o site pediu login. Entre na sua conta neste navegador, abra o vídeo e tente de novo '
                    '(a extensão manda os cookies do site para o yt-dlp).',
        'yt_private': 'vídeo privado ou só para membros: entre com a conta que tem acesso e tente de novo.',
        'yt_unsupported': 'esse link não é de um vídeo que o yt-dlp reconheça. Use o link do post/reel/vídeo, '
                          'ou "Gravar" na lista de vídeos da página.',
        'yt_no_video': 'esse post não tem vídeo (só foto ou texto).',
        'yt_unknown': 'erro desconhecido',
        'nothing_recorded': 'nada foi gravado',
        'job_not_found': 'download não encontrado: {id}',
        'spawn_failed': 'não consegui iniciar o processo de download',
        'still_running': 'esse download ainda está rodando',
        'cancel_first': 'cancele o download antes de remover',
        'unknown_cmd': 'comando desconhecido: {cmd}',
        'rec_default_title': 'gravação',
        'rec_conn_closed': 'conexão com o navegador encerrada: salvo o que foi gravado',
        'cancelled_reason': 'cancelado',
    },
    'en': {
        'cert_untrusted': 'the HTTPS certificate of {host} is not trusted by the system; the download was refused for safety.',
        'http_429': 'the site refused because of too many requests (HTTP 429). Wait a few minutes or use fewer parallel connections.',
        'http_429_one': 'the site refused because of too many requests (HTTP 429), even with 1 connection.{detail} Wait a few minutes and click Resume.',
        'http_auth': 'HTTP {code} on {url}: the server asked for login, the link expired or it refused the request rate.{detail} '
                     'Keep the video page open (and logged in) and click Resume: the extension sends fresh cookies '
                     'and continues where it stopped. If it happens again, use "Record from the page".',
        'failed_after': 'failed after {n} attempts: {url}{detail}',
        'server_said': ' Server response: "{reason}".',
        'youtube_internal': 'this is an internal YouTube chunk; use the video page link (youtube.com/watch?v=...).',
        'not_a_stream': 'this link is a page or text, not a stream. Open the video page, press play and pick the '
                        'stream under "In this tab"; or paste the .m3u8/.mpd address.',
        'not_video': 'the link does not look like a video (type: {ctype})',
        'unknown': 'unknown',
        'live': 'live stream: this version only downloads complete videos (VOD)',
        'no_segments': 'playlist without segments',
        'tpl_no_duration': 'SegmentTemplate without duration or SegmentTimeline',
        'mpd_no_period': 'MPD without Period',
        'mpd_no_tracks': 'MPD without video or audio tracks',
        'audio_only': 'audio only',
        'drm': 'media encrypted with DRM (Widevine/PlayReady/FairPlay){detail}: direct download does not decrypt DRM.',
        'service_ciphered': 'the service delivered ciphered segments; they do not form a video. Use "Record from the page" '
                            'or the site\'s own Download button. Removing this item frees the segments\' space',
        'aes_needs_ffmpeg': 'this stream is encrypted (AES-128) and needs ffmpeg to decrypt. Install it with: {hint}',
        'ffmpeg_merge_failed': 'ffmpeg failed to merge: {err}',
        'ffmpeg_rec_failed': 'ffmpeg failed to finish the recording: {err}',
        'need_ytdlp': 'to download from YouTube, Instagram and Facebook install yt-dlp: {hint}',
        'yt_login': 'the site asked for login. Sign in to your account in this browser, open the video and try again '
                    '(the extension sends the site cookies to yt-dlp).',
        'yt_private': 'private or members-only video: sign in with an account that has access and try again.',
        'yt_unsupported': 'yt-dlp does not recognize this link as a video. Use the post/reel/video link, '
                          'or "Record" in the page video list.',
        'yt_no_video': 'this post has no video (only photo or text).',
        'yt_unknown': 'unknown error',
        'nothing_recorded': 'nothing was recorded',
        'job_not_found': 'download not found: {id}',
        'spawn_failed': 'could not start the download process',
        'still_running': 'this download is still running',
        'cancel_first': 'cancel the download before removing it',
        'unknown_cmd': 'unknown command: {cmd}',
        'rec_default_title': 'recording',
        'rec_conn_closed': 'connection with the browser closed: saved what was recorded',
        'cancelled_reason': 'cancelled',
    },
}


def normalize(lang):
    return 'pt' if str(lang or '').lower().startswith('pt') else 'en'


def set_lang(lang):
    global LANG
    LANG = normalize(lang)


def tr(key, **kw):
    text = MESSAGES[LANG].get(key) or MESSAGES['en'][key]
    return text.format(**kw) if kw else text
