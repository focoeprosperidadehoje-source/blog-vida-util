#!/usr/bin/env python3
"""
trocar_produto.py — Blog Vida Útil
Fix pontual: troca o produto de um post JÁ EXISTENTE por outro MLB ainda não
publicado (usado para resolver duplicidade — dois posts com o mesmo MLB).
Reaproveita o pipeline normal de gerar_artigo.py (Gemini → conteúdo) e
gerar_capa.py (remove.bg + Pexels + Pillow), mas aplicando no post_id
informado em vez de criar um post novo, e cria também o produto WooCommerce
correspondente (o post antigo nunca teve um, por ter sido criado fora do
fluxo rastreado pelo pipeline).

Variáveis de ambiente:
  TROCA_POST_ID       ID do post WordPress a atualizar
  TROCA_MLB_ID        novo MLB ID
  TROCA_TITULO        novo título do produto
  TROCA_PRECO         preço (número, ex.: 289.90)
  TROCA_IMAGEM_URL    URL da imagem do produto no ML
  TROCA_PEXELS_INDEX  índice da foto Pexels a forçar (opcional — default:
                       derivado do novo MLB ID, ver pexels_index_for)
"""

import os
import re
import sys

from gerar_artigo import (
    montar_produto,
    gerar_artigo_gemini,
    extrair_intro,
    montar_conteudo_wp,
    criar_produto_wc,
    categorias_post_wp,
    slugify,
    enviar_telegram,
    WP_URL,
    WP_HEADERS,
)
from gerar_capa import (
    remover_fundo,
    buscar_fundo_pexels,
    compor_capa,
    upload_wp_media,
    definir_featured_media,
    pexels_keyword,
    pexels_index_for,
)

import requests


def atualizar_post_wp(post_id: int, titulo: str, conteudo: str) -> bool:
    slug       = slugify(titulo) + '-vale-a-pena'
    categorias = categorias_post_wp(titulo)
    r = requests.post(
        f'{WP_URL}/wp-json/wp/v2/posts/{post_id}',
        headers=WP_HEADERS,
        json={
            'title':      titulo,
            'slug':       slug,
            'content':    conteudo,
            'categories': categorias,
        },
        timeout=30,
    )
    if r.ok:
        print(f'[OK] Post {post_id} atualizado — novo título/slug/conteúdo')
        return True
    print(f'[ERRO] atualizar post {post_id}: HTTP {r.status_code} — {r.text[:300]}')
    return False


def gerar_capa_para_post(post_id: int, titulo: str, imagem_url: str, mlb_id: str, pexels_index_override=None) -> bool:
    produto_png = remover_fundo(imagem_url)
    if not produto_png:
        print('[ERRO] remove.bg falhou para a nova imagem do produto')
        return False

    keyword = pexels_keyword(titulo)
    index   = pexels_index_override if pexels_index_override is not None else pexels_index_for(mlb_id)
    print(f'[INFO] Pexels: "{keyword}" (índice {index})')
    fundo_bytes = buscar_fundo_pexels(keyword, index=index)
    if not fundo_bytes:
        print(f'[ERRO] Pexels falhou para "{keyword}" (índice {index})')
        return False

    capa_bytes = compor_capa(fundo_bytes, produto_png)
    slug = slugify(titulo)
    media_id = upload_wp_media(capa_bytes, slug, titulo)
    if not media_id:
        print('[ERRO] upload da nova capa falhou')
        return False

    return definir_featured_media(post_id, media_id)


def main():
    post_id    = int(os.environ['TROCA_POST_ID'])
    mlb_id     = os.environ['TROCA_MLB_ID']
    titulo     = os.environ['TROCA_TITULO']
    preco      = float(os.environ['TROCA_PRECO'])
    imagem_url = os.environ['TROCA_IMAGEM_URL']
    pexels_idx_env = os.environ.get('TROCA_PEXELS_INDEX', '').strip()
    pexels_idx_override = int(pexels_idx_env) if pexels_idx_env else None

    item = {
        'id':         mlb_id,
        'title':      titulo,
        'price':      preco,
        'rating':     0,
        'review_count': 0,
        'image_url':  imagem_url,
        'permalink':  '',
    }
    produto = montar_produto(item)

    print(f'[INFO] Gerando artigo Gemini para {mlb_id} — {titulo[:60]}...')
    artigo_md = gerar_artigo_gemini(produto)
    if not artigo_md:
        enviar_telegram(f'❌ Troca de produto falhou — Post #{post_id}\n⛔ Gemini não respondeu')
        sys.exit(1)

    palavras = len(re.sub(r'\[.*?\]|\<[^>]+>', '', artigo_md).split())
    print(f'[INFO] Artigo: {palavras} palavras')

    intro_desc  = extrair_intro(artigo_md)
    conteudo_wp = montar_conteudo_wp(artigo_md, mlb_id)

    if not atualizar_post_wp(post_id, titulo, conteudo_wp):
        enviar_telegram(f'❌ Troca de produto falhou — Post #{post_id}\n⛔ falha ao atualizar post WP')
        sys.exit(1)

    wc_id = criar_produto_wc(produto, intro_desc)

    capa_ok = gerar_capa_para_post(post_id, titulo, imagem_url, mlb_id, pexels_idx_override)

    resumo = [
        f'<b>🔄 Produto trocado — Post #{post_id}</b>',
        f'Novo MLB: <code>{mlb_id}</code>',
        f'{titulo[:60]}',
        f'WC: {"#" + str(wc_id) if wc_id else "falhou"} | Capa: {"✅" if capa_ok else "❌"}',
    ]
    enviar_telegram('\n'.join(resumo))
    print('\n[OK] Troca de produto concluída' if capa_ok and wc_id else '\n[AVISO] Troca concluída com pendências (ver log)')
    sys.exit(0)


if __name__ == '__main__':
    main()
