#!/usr/bin/env python3
"""
gerar_video.py — Blog Vida Útil
Para cada produto sem vídeo no estado "aprovacao_atual" (máx. 1 por execução):
  1. Baixa a foto do produto (CDN de imagens do ML — ver nota abaixo) + salva no Drive
  2. Gera narração PT-BR com Edge TTS (~35s)
  3. Monta vídeo vertical 9:16 (1080×1920) com FFmpeg — efeito Ken Burns (zoom + pan
     lento) sobre a foto única, já que não há mais um conjunto de 4-8 fotos disponível
  4. Faz upload do vídeo para WP media (URL pública)
  5. Publica no Facebook via Graph API
  6. Publica no Instagram como Reel via Graph API (create → poll → publish)
  7. Atualiza planilha controle_publicacoes (video_publicado=TRUE)
  8. Envia resumo via Telegram
Lê:   estado "aprovacao_atual" (aba estado_pipeline da planilha Google Sheets)
Grava: estado "aprovacao_atual" (video_publicado, fb_video_id, ig_media_id por post)

IMPORTANTE: este script NÃO chama mais a API pública do Mercado Livre
(api.mercadolibre.com) — ela bloqueia com 403 qualquer chamada de IP de
datacenter/cloud, incluindo os runners do GitHub Actions (ver memória
pipeline_sugestao_semanal_bloqueios, itens 2 e 7 — mesmo bloqueio já corrigido
em gerar_artigo.py). A foto do produto (uma única, capturada por
sugestao_semanal.py via search-items e repassada em "imagem_url" dentro de
"posts_criados") é baixada direto da CDN de imagens do ML
(http(s)://*.mlstatic.com) — domínio DIFERENTE de api.mercadolibre.com, sem a
mesma proteção de WAF (confirmado em 16/06/2026: requisição direta a partir de
um IP de datacenter retornou HTTP 200, com Access-Control-Allow-Origin: "*" e
sem redirecionamento de bloqueio — ver memória pipeline_sugestao_semanal_bloqueios,
item 8). Como só há 1 foto por produto (não 4-8 como antes), o vídeo usa essa
única foto com zoom/pan lento (Ken Burns) em vez de slideshow.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from base64 import b64encode
from datetime import datetime, timezone, timedelta

import edge_tts
import gspread
import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from estado_sheets import ler_estado, salvar_estado

# === Credenciais ===
WP_URL           = os.environ['WORDPRESS_URL'].rstrip('/')
WP_USER          = os.environ['WORDPRESS_USER']
WP_PASS          = os.environ['WORDPRESS_APP_PASSWORD']
META_PAGE_ID     = os.environ['META_PAGE_ID']
META_IG_ID       = os.environ['META_IG_ACCOUNT_ID']
META_TOKEN       = os.environ['META_PAGE_ACCESS_TOKEN']
TELEGRAM_TOKEN   = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

SPREADSHEET_ID = '1cH1KUvgSt2OFTBTfzcaDOFDA4OPUEfNiKCvDtJklMwU'
DRIVE_ROOT_ID  = '1S6o0KBtEutrcPdNAHowgrzn_lDYfFldJ'

GRAPH_API = 'https://graph.facebook.com/v20.0'
BRT        = timezone(timedelta(hours=-3))

WP_AUTH   = b64encode(f'{WP_USER}:{WP_PASS}'.encode()).decode()
WP_AUTH_H = {'Authorization': f'Basic {WP_AUTH}'}

VOICE_BR     = 'pt-BR-FranciscaNeural'
VIDEO_W, VIDEO_H = 1080, 1920
FONT_PATH    = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]

COL_VIDEO_PUB  = 8
COL_DATA_VIDEO = 9

HASHTAGS = (
    '#casainteligente #smarthome #automacaoresidencial '
    '#produtointeligente #mercadolivre #vidautil #casaconectada'
)

MAX_VIDEOS_POR_RUN = 1  # um vídeo por execução diária (alinha com artigo do dia)


# === Helpers ===

def enviar_telegram(texto: str):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    try:
        requests.post(url, json={
            'chat_id':                  TELEGRAM_CHAT_ID,
            'text':                     texto,
            'parse_mode':               'HTML',
            'disable_web_page_preview': True,
        }, timeout=15)
    except Exception as e:
        print(f'[WARN] Telegram: {e}')


# === Foto do produto (CDN de imagens do ML — sem o bloqueio da api.mercadolibre.com) ===

def baixar_foto_produto(imagem_url: str, destino: str) -> bool:
    """
    Baixa a (única) foto do produto já capturada por sugestao_semanal.py, direto
    da CDN de imagens do ML (*.mlstatic.com) — ver nota no topo do arquivo sobre
    por que esse domínio não tem o bloqueio 403 da api.mercadolibre.com.
    """
    try:
        r = requests.get(imagem_url, timeout=15, headers={'User-Agent': 'Blog-Vida-Util-Bot/1.0'})
        if r.ok:
            with open(destino, 'wb') as f:
                f.write(r.content)
            return True
        print(f'[ERRO] download foto {r.status_code}: {imagem_url[:70]}')
    except Exception as e:
        print(f'[ERRO] download foto: {e}')
    return False


# === Google Drive ===

def conectar_drive():
    creds_str = os.environ.get('GOOGLE_DRIVE_CREDENTIALS', '')
    if not creds_str:
        raise ValueError('GOOGLE_DRIVE_CREDENTIALS não configurada')
    creds_info = json.loads(creds_str)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)


def criar_pasta_drive(service, nome: str, parent_id: str) -> str:
    """Retorna ID da pasta (cria se não existir)."""
    query = (
        f"name='{nome}' and '{parent_id}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    result = service.files().list(q=query, fields='files(id)').execute()
    files = result.get('files', [])
    if files:
        return files[0]['id']

    meta = {
        'name':     nome,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents':  [parent_id],
    }
    return service.files().create(body=meta, fields='id').execute()['id']


def salvar_foto_drive(mlb_id: str, semana: str, foto_path: str) -> None:
    """Salva a foto do produto no Drive: {semana}/imagens/{mlb_id}/foto1.jpg"""
    try:
        service = conectar_drive()
        pasta_semana  = criar_pasta_drive(service, semana, DRIVE_ROOT_ID)
        pasta_imgs    = criar_pasta_drive(service, 'imagens', pasta_semana)
        pasta_produto = criar_pasta_drive(service, mlb_id, pasta_imgs)

        media = MediaFileUpload(foto_path, mimetype='image/jpeg', resumable=False)
        meta  = {'name': 'foto1.jpg', 'parents': [pasta_produto]}
        service.files().create(body=meta, media_body=media, fields='id').execute()

        print(f'[OK] Foto salva no Drive: {semana}/imagens/{mlb_id}/foto1.jpg')
    except Exception as e:
        print(f'[WARN] Drive upload: {e}')


# === Edge TTS ===

def gerar_texto_narracao(titulo: str) -> str:
    """~35 segundos de narração (≈140 palavras a 4 palavras/segundo)."""
    nome = titulo[:60]
    return (
        f'Conheça o {nome}. '
        f'Um dos produtos mais buscados para casa inteligente no Mercado Livre. '
        f'Instale sem complicação, controle pelo aplicativo no celular, '
        f'e integre facilmente com Alexa e Google Assistente. '
        f'Veja no blog Vida Útil o review completo com ficha técnica, '
        f'prós e contras reais, e comparativo com os concorrentes. '
        f'O link está na legenda. '
        f'Acesse vidautil.com.br e escolha com segurança. '
        f'Aproveite o melhor preço no Mercado Livre!'
    )


async def _save_audio(text: str, output: str):
    communicate = edge_tts.Communicate(text, VOICE_BR)
    await communicate.save(output)


def gerar_narracao(texto: str, saida: str) -> bool:
    try:
        asyncio.run(_save_audio(texto, saida))
        ok = os.path.exists(saida) and os.path.getsize(saida) > 0
        if ok:
            print(f'[OK] Narração gerada: {saida}')
        return ok
    except Exception as e:
        print(f'[ERRO] Edge TTS: {e}')
        return False


# === FFmpeg ===

def duracao_audio(path: str) -> float:
    r = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip()) if r.returncode == 0 else 35.0
    except ValueError:
        return 35.0


# Variações de pan do efeito Ken Burns — escolhida por produto (determinística,
# via soma dos caracteres do MLB ID) só para dar alguma variedade visual entre
# os vídeos, já que todos partem de uma única foto.
PAN_VARIANTES = [
    ('iw/2-(iw/zoom/2)',    'ih/2-(ih/zoom/2)'),     # zoom centrado
    ('iw*0.65-(iw/zoom/2)', 'ih*0.35-(ih/zoom/2)'),  # zoom canto superior-direito
    ('iw*0.35-(iw/zoom/2)', 'ih*0.6-(ih/zoom/2)'),   # zoom canto inferior-esquerdo
]


def montar_video(foto_path: str, narration_path: str, titulo: str, mlb_id: str, saida: str) -> bool:
    """
    Gera vídeo 1080×1920 9:16 com efeito Ken Burns (zoom + pan lento) sobre a
    única foto do produto disponível (ver nota no topo do arquivo) + texto
    sobreposto + narração.
    """
    dur_audio = duracao_audio(narration_path)
    dur_total = max(25.0, dur_audio)

    txt_titulo = '/tmp/vd_titulo.txt'
    txt_rodape = '/tmp/vd_rodape.txt'
    with open(txt_titulo, 'w', encoding='utf-8') as f:
        f.write(titulo[:55])
    with open(txt_rodape, 'w', encoding='utf-8') as f:
        f.write('Ver no Mercado Livre')

    x_expr, y_expr = PAN_VARIANTES[sum(ord(c) for c in mlb_id) % len(PAN_VARIANTES)]

    dt = (
        f'fontfile={FONT_PATH}:shadowcolor=black@0.8:shadowx=3:shadowy=3'
        f':box=1:boxcolor=black@0.5:boxborderw=12'
    )

    # scale upscale antes do zoompan evita pixelização ao aproximar; zoompan
    # cuida do crop final para 1080×1920 (cover, sem barras)
    filtro = (
        f"scale=2400:-2,"
        f"zoompan=z='min(zoom+0.0015,1.3)':x='{x_expr}':y='{y_expr}'"
        f":d=1:s={VIDEO_W}x{VIDEO_H}:fps=25,setsar=1[vz];"
        f'[vz]drawtext=textfile={txt_titulo}:{dt}'
        f':fontsize=46:fontcolor=white:x=(w-text_w)/2:y=80[vt];'
        f'[vt]drawtext=textfile={txt_rodape}:{dt}'
        f':fontsize=42:fontcolor=#FFD700:x=(w-text_w)/2:y=h-120[vout]'
    )

    cmd = [
        'ffmpeg', '-y',
        '-loop', '1', '-i', foto_path,
        '-i', narration_path,
        '-t', f'{dur_total:.2f}',
        '-filter_complex', filtro,
        '-map', '[vout]', '-map', '1:a',
        '-shortest',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '26',
        '-c:a', 'aac', '-b:a', '128k',
        '-pix_fmt', 'yuv420p', '-r', '30',
        '-movflags', '+faststart',
        saida,
    ]

    print(f'[INFO] FFmpeg: Ken Burns {dur_total:.1f}s (áudio {dur_audio:.1f}s)')
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f'[ERRO] FFmpeg:\n{r.stderr[-600:]}')
        return False
    print(f'[OK] Vídeo gerado: {saida}')
    return True


# === WordPress media ===

def upload_video_wp(video_path: str, slug: str, titulo: str) -> str:
    """Faz upload do vídeo para WP media. Retorna URL pública ou ''."""
    nome = f'video-{slug}.mp4'
    try:
        with open(video_path, 'rb') as f:
            conteudo = f.read()
        r = requests.post(
            f'{WP_URL}/wp-json/wp/v2/media',
            headers={
                **WP_AUTH_H,
                'Content-Disposition': f'attachment; filename="{nome}"',
            },
            files={'file': (nome, conteudo, 'video/mp4')},
            data={'title': titulo, 'alt_text': titulo},
            timeout=120,
        )
        if r.ok:
            data = r.json()
            url = data.get('source_url') or data.get('guid', {}).get('rendered', '')
            print(f'[OK] WP vídeo ID {data.get("id")} → {url[:70]}')
            return url
        print(f'[ERRO] WP media {r.status_code}: {r.text[:300]}')
    except Exception as e:
        print(f'[ERRO] WP video upload: {e}')
    return ''


# === Facebook ===

def publicar_facebook(video_url: str, titulo: str, caption: str) -> str:
    """Publica vídeo na página Facebook. Retorna video_id ou ''."""
    try:
        r = requests.post(
            f'{GRAPH_API}/{META_PAGE_ID}/videos',
            data={
                'access_token': META_TOKEN,
                'file_url':     video_url,
                'title':        titulo[:100],
                'description':  caption,
            },
            timeout=60,
        )
        if r.ok:
            vid_id = r.json().get('id', '')
            print(f'[OK] Facebook vídeo: {vid_id}')
            return vid_id
        print(f'[ERRO] Facebook {r.status_code}: {r.json()}')
    except Exception as e:
        print(f'[ERRO] Facebook: {e}')
    return ''


# === Instagram ===

def publicar_instagram(video_url: str, caption: str) -> str:
    """Publica Reel no Instagram. Retorna media_id ou ''."""
    # 1. Criar container
    try:
        r = requests.post(
            f'{GRAPH_API}/{META_IG_ID}/media',
            data={
                'access_token':  META_TOKEN,
                'media_type':    'REELS',
                'video_url':     video_url,
                'caption':       caption,
                'share_to_feed': 'true',
            },
            timeout=30,
        )
        if not r.ok:
            print(f'[ERRO] IG container {r.status_code}: {r.json()}')
            return ''
        container_id = r.json().get('id', '')
        print(f'[INFO] IG container: {container_id}')
    except Exception as e:
        print(f'[ERRO] IG container: {e}')
        return ''

    # 2. Aguardar processamento (até 5 min)
    for attempt in range(20):
        time.sleep(15)
        try:
            sr = requests.get(
                f'{GRAPH_API}/{container_id}',
                params={'fields': 'status_code', 'access_token': META_TOKEN},
                timeout=15,
            )
            if sr.ok:
                status = sr.json().get('status_code', '')
                print(f'[INFO] IG status t={attempt+1}: {status}')
                if status == 'FINISHED':
                    break
                if status == 'ERROR':
                    print(f'[ERRO] IG processamento: {sr.json()}')
                    return ''
        except Exception as e:
            print(f'[WARN] IG polling: {e}')
    else:
        print('[ERRO] IG timeout — container não ficou FINISHED')
        return ''

    # 3. Publicar
    try:
        r = requests.post(
            f'{GRAPH_API}/{META_IG_ID}/media_publish',
            data={'access_token': META_TOKEN, 'creation_id': container_id},
            timeout=30,
        )
        if r.ok:
            media_id = r.json().get('id', '')
            print(f'[OK] Instagram Reel: {media_id}')
            return media_id
        print(f'[ERRO] IG publish {r.status_code}: {r.json()}')
    except Exception as e:
        print(f'[ERRO] IG publish: {e}')
    return ''


# === Planilha ===

def atualizar_planilha_video(mlb_id: str, data_hoje: str) -> None:
    """Atualiza video_publicado e data_video na planilha."""
    creds_str = os.environ.get('GOOGLE_DRIVE_CREDENTIALS', '')
    if not creds_str:
        print('[WARN] GOOGLE_DRIVE_CREDENTIALS ausente — planilha não atualizada')
        return
    try:
        creds = Credentials.from_service_account_info(json.loads(creds_str), scopes=SCOPES)
        gc    = gspread.authorize(creds)
        ws    = gc.open_by_key(SPREADSHEET_ID).sheet1
        rows  = ws.get_all_values()
        for i, linha in enumerate(rows[1:], start=2):
            if linha and linha[0] == mlb_id:
                ws.update_cell(i, COL_VIDEO_PUB,  'TRUE')
                ws.update_cell(i, COL_DATA_VIDEO, data_hoje)
                print(f'[OK] Planilha: {mlb_id} → video_publicado=TRUE ({data_hoje})')
                return
        print(f'[WARN] {mlb_id} não encontrado na planilha')
    except Exception as e:
        print(f'[WARN] Planilha vídeo: {e}')


# === Caption ===

def montar_caption(titulo: str, slug: str) -> str:
    url = f'{WP_URL}/{slug}/'
    return (
        f'🏠 {titulo}\n\n'
        f'✅ Controle pelo celular\n'
        f'✅ Compatível com Alexa e Google\n'
        f'✅ Fácil instalação\n\n'
        f'👉 Review completo: {url}\n\n'
        f'{HASHTAGS}'
    )


# === Pipeline por produto ===

def processar_produto(post: dict, semana: str, tmp_dir: str) -> dict:
    mlb_id     = post['mlb_id']
    titulo     = post['titulo']
    slug       = post.get('slug', mlb_id.lower())
    imagem_url = post.get('imagem_url', '')

    resultado = {
        'mlb_id':    mlb_id,
        'titulo':    titulo[:50],
        'drive':     False,
        'facebook':  '',
        'instagram': '',
        'erro':      '',
    }

    print(f'\n[INFO] ── Vídeo {mlb_id}: {titulo[:50]} ──')

    # 1. Foto do produto (única — já capturada na sugestão semanal, ver nota no topo)
    if not imagem_url:
        resultado['erro'] = 'Sem imagem_url no estado salvo'
        return resultado

    foto_path = os.path.join(tmp_dir, 'foto1.jpg')
    if not baixar_foto_produto(imagem_url, foto_path):
        resultado['erro'] = 'Download da foto falhou'
        return resultado
    print('[INFO] Foto do produto baixada')

    # 2. Drive (não-crítico)
    salvar_foto_drive(mlb_id, semana, foto_path)
    resultado['drive'] = True

    # 3. Narração
    narration_path = os.path.join(tmp_dir, 'narracao.mp3')
    if not gerar_narracao(gerar_texto_narracao(titulo), narration_path):
        resultado['erro'] = 'Edge TTS falhou'
        return resultado

    # 4. Vídeo (Ken Burns sobre a foto única)
    video_path = os.path.join(tmp_dir, f'video-{slug}.mp4')
    if not montar_video(foto_path, narration_path, titulo, mlb_id, video_path):
        resultado['erro'] = 'FFmpeg falhou'
        return resultado

    # 5. Upload WP (URL pública para Meta)
    video_url = upload_video_wp(video_path, slug, titulo)
    if not video_url:
        resultado['erro'] = 'WP upload falhou'
        return resultado

    caption = montar_caption(titulo, slug)

    # 6. Facebook
    resultado['facebook'] = publicar_facebook(video_url, titulo, caption)

    # 7. Instagram Reel
    resultado['instagram'] = publicar_instagram(video_url, caption)

    return resultado


# === Main ===

def main():
    print(f'[INFO] {datetime.now().isoformat()} — pipeline de vídeo iniciado')

    aprovacao = ler_estado('aprovacao_atual')
    if not aprovacao:
        print('[ERRO] estado "aprovacao_atual" não encontrado na planilha')
        sys.exit(1)

    posts = aprovacao.get('posts_criados', [])
    if not posts:
        print('[INFO] posts_criados vazio — nada a fazer')
        sys.exit(0)

    pendentes = [p for p in posts if not p.get('video_publicado')]
    if not pendentes:
        print('[INFO] Todos os vídeos já publicados')
        sys.exit(0)

    print(f'[INFO] {len(pendentes)} produto(s) pendente(s) — processando {MAX_VIDEOS_POR_RUN}')

    semana    = aprovacao.get('semana', datetime.now(BRT).strftime('%Y-%m-%d'))
    data_hoje = datetime.now(BRT).strftime('%Y-%m-%d')

    processados = 0
    resultados  = []

    for post in posts:
        if post.get('video_publicado'):
            continue
        if processados >= MAX_VIDEOS_POR_RUN:
            break

        with tempfile.TemporaryDirectory(prefix=f'vidautil_{post["mlb_id"]}_') as tmp_dir:
            resultado = processar_produto(post, semana, tmp_dir)

        resultados.append(resultado)
        processados += 1

        sucesso = resultado.get('facebook') or resultado.get('instagram')
        if sucesso:
            post['video_publicado'] = True
            post['fb_video_id']     = resultado.get('facebook', '')
            post['ig_media_id']     = resultado.get('instagram', '')
            aprovacao['posts_criados'] = posts
            salvar_estado('aprovacao_atual', aprovacao)
            atualizar_planilha_video(post['mlb_id'], data_hoje)

    # Resumo Telegram
    if resultados:
        linhas = [f'<b>📹 Vídeo Vida Útil — {data_hoje}</b>\n']
        for r in resultados:
            fb  = '✅ FB'  if r.get('facebook')  else '❌ FB'
            ig  = '✅ IG'  if r.get('instagram') else '❌ IG'
            drv = '✅ Drive' if r.get('drive')     else '⚠️ Drive'
            err = f'\n  ⛔ {r["erro"]}' if r.get('erro') else ''
            linhas.append(f'• <b>{r["titulo"][:45]}</b>\n  {fb} | {ig} | {drv}{err}')

        restantes = len(pendentes) - MAX_VIDEOS_POR_RUN
        if restantes > 0:
            linhas.append(f'\n<i>⏳ {restantes} vídeo(s) restante(s) — próxima execução amanhã</i>')

        enviar_telegram('\n'.join(linhas))

    ok = sum(1 for r in resultados if r.get('facebook') or r.get('instagram'))
    print(f'\n[OK] {ok}/{len(resultados)} vídeo(s) publicado(s) com sucesso')


if __name__ == '__main__':
    main()
