#!/usr/bin/env python3
"""
gerar_capa.py — Blog Vida Útil
Para cada artigo sem capa no estado "aprovacao_atual" (aba estado_pipeline):
  1. Imagem ML → remove.bg → PNG transparente (produto sem fundo)
  2. Pexels → fundo contextual ao nicho do produto
  3. Pillow → composição 1200×628px (produto direita, fundo cover)
  4. Upload WP media → define como featured_media do post
Grava: capa_gerada=True por post no estado "aprovacao_atual"
"""

import io
import os
import re
import sys
import unicodedata
from base64 import b64encode

import requests
from PIL import Image

from estado_sheets import ler_estado, salvar_estado

REMOVE_BG_KEY    = os.environ['REMOVE_BG_API_KEY']
PEXELS_KEY       = os.environ['PEXELS_API_KEY']
WP_URL           = os.environ['WORDPRESS_URL'].rstrip('/')
WP_USER          = os.environ['WORDPRESS_USER']
WP_PASS          = os.environ['WORDPRESS_APP_PASSWORD']
TELEGRAM_TOKEN   = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

WP_AUTH    = b64encode(f'{WP_USER}:{WP_PASS}'.encode()).decode()
WP_AUTH_H  = {'Authorization': f'Basic {WP_AUTH}'}   # sem Content-Type (multipart usa boundary)
WP_JSON_H  = {**WP_AUTH_H, 'Content-Type': 'application/json'}

CAPA_W, CAPA_H = 1200, 628
PROD_H         = 560   # altura do produto na capa (px)
PROD_MARGEM    = 20    # margem direita

# Keyword Pexels por palavras do título
PEXELS_MAP = {
    'airfryer':    'modern kitchen cooking interior',
    'air fryer':   'modern kitchen cooking interior',
    'câmera':      'modern house exterior security camera',
    'camera':      'modern house exterior security camera',
    'lâmpada':     'modern living room lighting interior',
    'lampada':     'modern living room lighting interior',
    'led':         'modern living room lighting',
    'dimmer':      'modern living room smart home lighting',
    'fechadura':   'modern front door smart entrance',
    'robô':        'modern living room clean floor',
    'robo':        'modern living room clean floor',
    'aspirador':   'modern living room clean floor',
    'echo':        'modern living room smart speaker',
    'alexa':       'modern living room smart speaker',
    'smartband':   'fitness lifestyle smart technology',
    'smart band':  'fitness lifestyle smart technology',
    'tomada':      'modern living room interior smart',
    'plug':        'modern living room interior smart',
    'interruptor': 'modern living room interior smart',
    'sensor':      'modern smart home interior security',
    'controle':    'modern living room smart home',
    'geladeira':   'modern kitchen interior smart',
    'comedouro':   'modern living room pet home',
    'fire tv':     'modern living room entertainment tv',
    'roku':        'modern living room entertainment tv',
    'hub':         'modern smart home interior',
    'zigbee':      'modern smart home interior',
}
PEXELS_DEFAULT = 'modern smart home interior living room'


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


def slugify(texto: str) -> str:
    texto = unicodedata.normalize('NFD', texto)
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    texto = texto.lower()
    texto = re.sub(r'[^a-z0-9\s-]', '', texto)
    texto = re.sub(r'\s+', '-', texto.strip())
    return re.sub(r'-+', '-', texto)[:80]


def pexels_keyword(titulo: str) -> str:
    titulo_lower = titulo.lower()
    for kw, query in PEXELS_MAP.items():
        if kw in titulo_lower:
            return query
    return PEXELS_DEFAULT


def remover_fundo(image_url: str) -> bytes | None:
    """Chama remove.bg com URL da imagem ML → retorna PNG transparente."""
    try:
        r = requests.post(
            'https://api.remove.bg/v1.0/removebg',
            data={'image_url': image_url, 'size': 'auto'},
            headers={'X-Api-Key': REMOVE_BG_KEY},
            timeout=30,
        )
        if r.ok:
            return r.content
        print(f'[ERRO] remove.bg {r.status_code}: {r.text[:200]}')
    except Exception as e:
        print(f'[ERRO] remove.bg: {e}')
    return None


def buscar_fundo_pexels(keyword: str) -> bytes | None:
    """Busca foto landscape no Pexels e retorna bytes da imagem."""
    try:
        r = requests.get(
            'https://api.pexels.com/v1/search',
            headers={'Authorization': PEXELS_KEY},
            params={'query': keyword, 'per_page': 5, 'orientation': 'landscape'},
            timeout=15,
        )
        r.raise_for_status()
        fotos = r.json().get('photos', [])
        if not fotos:
            print(f'[WARN] Pexels: nenhuma foto para "{keyword}"')
            return None
        foto_url = fotos[0]['src']['large2x']
        img_r = requests.get(foto_url, timeout=20)
        img_r.raise_for_status()
        return img_r.content
    except Exception as e:
        print(f'[ERRO] Pexels: {e}')
    return None


def cover_resize(img: Image.Image, W: int, H: int) -> Image.Image:
    """Redimensiona e corta centralizando para preencher W×H sem barras."""
    scale = max(W / img.width, H / img.height)
    novo_w = int(img.width * scale)
    novo_h = int(img.height * scale)
    img = img.resize((novo_w, novo_h), Image.LANCZOS)
    x = (novo_w - W) // 2
    y = (novo_h - H) // 2
    return img.crop((x, y, x + W, y + H))


def compor_capa(fundo_bytes: bytes, produto_bytes: bytes) -> bytes:
    """Compõe capa 1200×628px: fundo cover + produto PNG direita."""
    bg   = Image.open(io.BytesIO(fundo_bytes)).convert('RGB')
    bg   = cover_resize(bg, CAPA_W, CAPA_H)

    prod = Image.open(io.BytesIO(produto_bytes)).convert('RGBA')
    prod_w = int(PROD_H * prod.width / prod.height)
    prod   = prod.resize((prod_w, PROD_H), Image.LANCZOS)

    canvas = bg.convert('RGBA')
    px = CAPA_W - prod_w - PROD_MARGEM
    py = (CAPA_H - PROD_H) // 2
    canvas.paste(prod, (px, py), prod)  # terceiro arg = máscara alpha

    result = canvas.convert('RGB')
    buf = io.BytesIO()
    result.save(buf, format='JPEG', quality=92)
    return buf.getvalue()


def upload_wp_media(capa_bytes: bytes, slug: str, alt_text: str) -> int | None:
    """Faz upload da capa ao WP media e retorna o media_id."""
    filename = f'capa-{slug}.jpg'
    try:
        r = requests.post(
            f'{WP_URL}/wp-json/wp/v2/media',
            headers={**WP_AUTH_H, 'Content-Disposition': f'attachment; filename="{filename}"'},
            files={'file': (filename, capa_bytes, 'image/jpeg')},
            data={'alt_text': alt_text, 'title': alt_text},
            timeout=30,
        )
        if r.ok:
            media_id = r.json().get('id')
            print(f'[OK] Media upload → ID {media_id} ({filename})')
            return media_id
        print(f'[ERRO] WP media {r.status_code}: {r.text[:200]}')
    except Exception as e:
        print(f'[ERRO] WP media upload: {e}')
    return None


def definir_featured_media(post_id: int, media_id: int) -> bool:
    """Define featured_media e focal point do post WP."""
    r = requests.post(
        f'{WP_URL}/wp-json/wp/v2/posts/{post_id}',
        headers=WP_JSON_H,
        json={
            'featured_media': media_id,
            'meta': {'_kad_post_feature_position': 'right center'},
        },
        timeout=15,
    )
    if r.ok:
        print(f'[OK] Post {post_id} → featured_media={media_id} (focal: right center)')
        return True
    print(f'[ERRO] set featured_media {r.status_code}: {r.text[:200]}')
    return False


def processar_post(post: dict) -> tuple[bool, str]:
    """Executa pipeline completo de capa para um post. Retorna (sucesso, erro)."""
    mlb_id    = post['mlb_id']
    post_id   = post['post_id']
    titulo    = post['titulo']
    imagem_url = post.get('imagem_url', '')
    slug      = post.get('slug', slugify(titulo))

    print(f'\n[INFO] ── Capa para Post #{post_id} ──')

    if not imagem_url:
        erro = 'sem imagem_url salva no estado'
        print(f'[WARN] {erro} — Post #{post_id} — pulando')
        return False, erro

    # 1. Remove fundo
    print(f'[INFO] remove.bg: {imagem_url[:60]}...')
    produto_png = remover_fundo(imagem_url)
    if not produto_png:
        return False, 'falha no remove.bg (ver log)'

    # 2. Fundo Pexels
    keyword = pexels_keyword(titulo)
    print(f'[INFO] Pexels: "{keyword}"')
    fundo_bytes = buscar_fundo_pexels(keyword)
    if not fundo_bytes:
        return False, f'falha no Pexels para "{keyword}" (ver log)'

    # 3. Composição
    print(f'[INFO] Compondo 1200×628px...')
    capa_bytes = compor_capa(fundo_bytes, produto_png)

    # 4. Upload WP
    media_id = upload_wp_media(capa_bytes, slug, titulo)
    if not media_id:
        return False, 'falha no upload WP media (ver log)'

    # 5. Define como capa do post
    if not definir_featured_media(post_id, media_id):
        return False, 'falha ao definir featured_media (ver log)'
    return True, ''


def main():
    aprovacao = ler_estado('aprovacao_atual')
    if not aprovacao:
        print('[ERRO] estado "aprovacao_atual" não encontrado na planilha')
        sys.exit(1)

    posts = aprovacao.get('posts_criados', [])
    pendentes = [p for p in posts if not p.get('capa_gerada')]

    if not pendentes:
        print('[INFO] Todas as capas já geradas — nada a fazer')
        sys.exit(0)

    print(f'[INFO] {len(pendentes)} capas a gerar')
    ok = 0
    resultados = []

    for i, post in enumerate(posts):
        if post.get('capa_gerada'):
            continue
        sucesso, erro = processar_post(post)
        resultados.append({
            'post_id': post.get('post_id'),
            'titulo':  post.get('titulo', ''),
            'sucesso': sucesso,
            'erro':    erro,
        })
        if sucesso:
            post['capa_gerada'] = True
            ok += 1
        # Salva progresso parcial após cada capa
        aprovacao['posts_criados'] = posts
        salvar_estado('aprovacao_atual', aprovacao)

    print(f'\n[OK] {ok}/{len(pendentes)} capas geradas com sucesso')

    # Resumo Telegram — sempre enviado, mesmo se tudo falhar (bug anterior:
    # falha silenciosa, sem nenhum aviso ao operador)
    linhas = [f'<b>🖼️ Capas Vida Útil</b>\n']
    for r in resultados:
        status = '✅' if r['sucesso'] else '❌'
        linha = f'{status} <b>Post #{r["post_id"]}</b> — {r["titulo"][:45]}'
        if not r['sucesso']:
            linha += f'\n  ⛔ {r["erro"]}'
        linhas.append(linha)
    linhas.append(f'\n<i>{ok}/{len(resultados)} capa(s) geradas com sucesso</i>')
    enviar_telegram('\n'.join(linhas))

    if ok == 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
