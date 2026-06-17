#!/usr/bin/env python3
"""
gerar_capa_manual.py — Blog Vida Útil
Fix pontual para posts que ficaram fora do estado "posts_criados" (Sheets) e por
isso nunca tiveram capa gerada por gerar_capa.py. Recebe os dados do post via
variáveis de ambiente e roda o mesmo pipeline (remove.bg + Pexels + Pillow).
"""

import os
import sys

from gerar_capa import (
    processar_post,
    enviar_telegram,
    remover_fundo,
    buscar_fundo_pexels,
    compor_capa,
    upload_wp_media,
    definir_featured_media,
    pexels_keyword,
    slugify,
)


def trocar_fundo():
    """Troca apenas o fundo da capa de um post já publicado, mantendo o
    mesmo produto. Usa a própria capa atual como fonte para o remove.bg
    (o produto já está isolado nela, então o remove.bg consegue re-extrair
    o objeto em primeiro plano sem precisar da foto original do ML)."""
    post_id     = int(os.environ['CAPA_POST_ID'])
    titulo      = os.environ['CAPA_TITULO']
    capa_atual  = os.environ['CAPA_ATUAL_URL']
    slug        = os.environ.get('CAPA_SLUG', '') or slugify(titulo)
    pexels_idx  = int(os.environ.get('CAPA_PEXELS_INDEX', '1'))

    print(f'[INFO] Re-extraindo produto da capa atual: {capa_atual[:70]}...')
    produto_png = remover_fundo(capa_atual)
    if not produto_png:
        return False, 'falha no remove.bg ao re-extrair produto da capa atual'

    keyword = pexels_keyword(titulo)
    print(f'[INFO] Pexels: "{keyword}" (índice {pexels_idx}, evitando o fundo repetido)')
    fundo_bytes = buscar_fundo_pexels(keyword, index=pexels_idx)
    if not fundo_bytes:
        return False, f'falha no Pexels para "{keyword}" (índice {pexels_idx})'

    print('[INFO] Compondo 1200×628px com novo fundo...')
    capa_bytes = compor_capa(fundo_bytes, produto_png)

    media_id = upload_wp_media(capa_bytes, slug, titulo)
    if not media_id:
        return False, 'falha no upload WP media'

    if not definir_featured_media(post_id, media_id):
        return False, 'falha ao definir featured_media'
    return True, ''


def main():
    if os.environ.get('CAPA_MODO') == 'trocar_fundo':
        post_id = os.environ['CAPA_POST_ID']
        sucesso, erro = trocar_fundo()
    else:
        post = {
            'post_id':    int(os.environ['CAPA_POST_ID']),
            'titulo':     os.environ['CAPA_TITULO'],
            'imagem_url': os.environ['CAPA_IMAGEM_URL'],
            'slug':       os.environ.get('CAPA_SLUG', ''),
            'mlb_id':     os.environ.get('CAPA_MLB_ID', ''),
        }
        post_id = post['post_id']
        sucesso, erro = processar_post(post)

    if sucesso:
        enviar_telegram(f'✅ Capa manual aplicada — Post #{post_id}')
        print('[OK] capa aplicada')
    else:
        enviar_telegram(f'❌ Falha na capa manual — Post #{post_id}\n⛔ {erro}')
        print(f'[ERRO] {erro}')
    sys.exit(0 if sucesso else 1)


if __name__ == '__main__':
    main()
